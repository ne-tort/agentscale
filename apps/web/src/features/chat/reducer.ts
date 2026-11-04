import type { ChatAction, ChatBlock, ChatEvent, ChatState } from "./types";
import { EMPTY_CHAT_STATE } from "./types";

function blockId(event: ChatEvent, index: number): string {
  return `${event.seq ?? `${event.type}-${index}`}`;
}

function dataText(event: ChatEvent): string {
  const value = event.data.text ?? event.data.message ?? "";
  return typeof value === "string" ? value : "";
}

function normalizeDelta(previous: string, chunk: string): { delta: string; cumulative: string } {
  if (!chunk) return { delta: "", cumulative: previous };
  if (chunk.startsWith(previous)) return { delta: chunk.slice(previous.length), cumulative: chunk };
  if (previous.startsWith(chunk)) return { delta: "", cumulative: previous };
  if (previous.endsWith(chunk)) return { delta: "", cumulative: previous };
  return { delta: chunk, cumulative: previous + chunk };
}

function eventFingerprint(event: ChatEvent): string {
  return `${event.type}:${event.seq ?? ""}:${event.at ?? ""}:${JSON.stringify(event.data)}`;
}

function appendAssistant(blocks: ChatBlock[], event: ChatEvent): ChatBlock[] {
  const text = dataText(event);
  if (!text) return blocks;
  const last = blocks.at(-1);
  if (last?.kind === "assistant" && last.raw?.type === "text_delta") {
    const previous = last.text ?? "";
    const normalized = normalizeDelta(previous, text);
    if (!normalized.delta) return blocks;
    return [...blocks.slice(0, -1), { ...last, text: normalized.cumulative, raw: event }];
  }
  return [...blocks, { id: blockId(event, blocks.length), kind: "assistant", text, raw: event }];
}

function addEvent(blocks: ChatBlock[], event: ChatEvent): ChatBlock[] {
  const index = blocks.length;
  switch (event.type) {
    case "user_message": {
      const text = typeof event.data.text === "string" ? event.data.text : "";
      const refs = Array.isArray(event.data.attachment_refs) ? event.data.attachment_refs : [];
      return replaceOptimisticUser(blocks, {
        id: blockId(event, index),
        kind: "user",
        text,
        ...(refs.length ? { attachments: refs.map((ref, attachmentIndex) => ({ id: String(ref), filename: `Attachment ${attachmentIndex + 1}` })) } : {}),
        raw: event,
      });
    }
    case "text_delta":
      return appendAssistant(blocks, event);
    case "thinking_delta": {
      const text = dataText(event);
      const last = blocks.at(-1);
      if (last?.kind === "thinking") {
        const normalized = normalizeDelta(last.text ?? "", text);
        return normalized.delta ? [...blocks.slice(0, -1), { ...last, text: normalized.cumulative, raw: event }] : blocks;
      }
      return text ? [...blocks, { id: blockId(event, index), kind: "thinking", text, raw: event }] : blocks;
    }
    case "thinking_complete":
      return blocks.map((block) => block.kind === "thinking" ? { ...block, raw: event } : block);
    case "tool_call":
    case "tool_call_delta":
      return [...blocks, {
        id: String(event.data.id ?? blockId(event, index)),
        kind: "tool_call",
        name: typeof event.data.name === "string" ? event.data.name : "Tool call",
        input: event.data.input ?? event.data.partial ?? event.data.partial_json ?? {},
        raw: event,
      }];
    case "tool_progress":
      return [...blocks, {
        id: String(event.data.id ?? blockId(event, index)),
        kind: "tool_progress",
        name: typeof event.data.name === "string" ? event.data.name : "Tool activity",
        message: typeof event.data.message === "string" ? event.data.message : "Working…",
        raw: event,
      }];
    case "tool_result":
      return [...blocks, {
        id: String(event.data.id ?? blockId(event, index)),
        kind: "tool_result",
        name: typeof event.data.name === "string" ? event.data.name : "Tool result",
        output: event.data.output ?? event.data.result ?? "",
        is_error: Boolean(event.data.is_error) || Boolean(isRecord(event.data.output) && event.data.output.isError),
        raw: event,
      }];
    case "tool_approval_request":
      return [...blocks, {
        id: String(event.data.id ?? blockId(event, index)),
        kind: "approval",
        approvalId: String(event.data.id ?? ""),
        name: typeof event.data.name === "string" ? event.data.name : "Tool approval",
        input: event.data.input ?? {},
        reason: typeof event.data.reason === "string" ? event.data.reason : undefined,
        raw: event,
      }];
    case "status":
      return [...blocks, {
        id: blockId(event, index),
        kind: "status",
        phase: typeof event.data.phase === "string" ? event.data.phase : undefined,
        message: typeof event.data.message === "string" ? event.data.message : typeof event.data.detail === "string" ? event.data.detail : undefined,
        attempt: typeof event.data.attempt === "number" ? event.data.attempt : undefined,
        max_attempts: typeof event.data.max_attempts === "number" ? event.data.max_attempts : undefined,
        next_model: typeof event.data.next_model === "string" ? event.data.next_model : undefined,
        retry_in_ms: typeof event.data.retry_in_ms === "number" ? event.data.retry_in_ms : undefined,
        raw: event,
      }];
    case "error":
      return [...blocks, { id: blockId(event, index), kind: "error", message: dataText(event) || "Agent error", raw: event }];
    case "done":
      return blocks;
    case "keepalive":
      return blocks;
    default:
      return [...blocks, { id: blockId(event, index), kind: "event", message: event.type, raw: event }];
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameAttachments(left: ChatBlock, right: ChatBlock): boolean {
  const leftIds = (left.attachments ?? []).map((attachment) => attachment.id).join("\u0000");
  const rightIds = (right.attachments ?? []).map((attachment) => attachment.id).join("\u0000");
  return leftIds === rightIds;
}

function replaceOptimisticUser(blocks: ChatBlock[], incoming: ChatBlock): ChatBlock[] {
  if (incoming.kind !== "user") return blocks;
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const current = blocks[index];
    if (current.kind === "user" && current.id.startsWith("local-user-") && current.text === incoming.text && sameAttachments(current, incoming)) {
      return [...blocks.slice(0, index), incoming, ...blocks.slice(index + 1)];
    }
  }
  return [...blocks, incoming];
}

function mergeTranscriptBlocks(blocks: ChatBlock[], incoming: ChatBlock[]): ChatBlock[] {
  const merged = [...blocks];
  for (const block of incoming) {
    const existingIndex = merged.findIndex((current) => current.id === block.id);
    if (existingIndex >= 0) {
      const existing = merged[existingIndex];
      if (block.kind === "assistant" && existing.kind === "assistant") {
        const normalized = normalizeDelta(existing.text ?? "", block.text ?? "");
        merged[existingIndex] = { ...existing, text: normalized.cumulative, raw: block.raw ?? existing.raw };
      } else {
        merged[existingIndex] = block;
      }
      continue;
    }
    const last = merged.at(-1);
    if (block.kind === "assistant" && last?.kind === "assistant") {
      const normalized = normalizeDelta(last.text ?? "", block.text ?? "");
      merged[merged.length - 1] = { ...last, text: normalized.cumulative, raw: block.raw ?? last.raw };
      continue;
    }
    if (block.kind === "thinking" && last?.kind === "thinking") {
      const normalized = normalizeDelta(last.text ?? "", block.text ?? "");
      merged[merged.length - 1] = { ...last, text: normalized.cumulative, raw: block.raw ?? last.raw };
      continue;
    }
    merged.push(block);
  }
  return merged;
}

function rebuildFromBlocks(blocks: ChatBlock[], incoming: ChatBlock[]): ChatBlock[] {
  let merged = blocks;
  for (const block of incoming) {
    if (block.kind === "user") {
      const optimistic = merged.find((current) => current.kind === "user" && current.id.startsWith("local-user-") && current.text === block.text && sameAttachments(current, block));
      if (optimistic) {
        merged = merged.map((current) => current.id === optimistic.id ? block : current);
        continue;
      }
    }
    merged = mergeTranscriptBlocks(merged, [block]);
  }
  return merged;
}

function transcriptBlocksToLocal(blocks: Array<Record<string, unknown>>, prefix = "block"): ChatBlock[] {
  return blocks.map((block, index) => {
    const kind = typeof block.kind === "string" ? block.kind : "event";
    const refs = Array.isArray(block.attachment_refs) ? block.attachment_refs : [];
    const attachments = Array.isArray(block.attachments)
      ? block.attachments.filter(isRecord).map((attachment, attachmentIndex) => ({
        id: String(attachment.id ?? attachment.storage_ref ?? `${prefix}-attachment-${index}-${attachmentIndex}`),
        filename: typeof attachment.filename === "string" ? attachment.filename : `Attachment ${attachmentIndex + 1}`,
        ...(typeof attachment.content_type === "string" ? { content_type: attachment.content_type } : {}),
        ...(typeof attachment.storage_ref === "string" ? { storage_ref: attachment.storage_ref } : {}),
        ...(typeof attachment.size_bytes === "number" ? { size: attachment.size_bytes } : {}),
      }))
      : refs.map((ref, attachmentIndex) => ({ id: String(ref), filename: `Attachment ${attachmentIndex + 1}` }));
    return {
      id: String(block.seq ?? block.id ?? `${prefix}-${kind}-${index}`),
      kind: kind === "assistant_markdown" ? "assistant" : kind === "user" ? "user" : kind,
      ...(typeof block.text === "string" ? { text: block.text } : {}),
      ...(typeof block.name === "string" ? { name: block.name } : {}),
      ...(block.input !== undefined ? { input: block.input } : {}),
      ...(block.output !== undefined ? { output: block.output } : {}),
      ...(typeof block.message === "string" ? { message: block.message } : {}),
      ...(typeof block.reason === "string" ? { reason: block.reason } : {}),
      ...(attachments.length ? { attachments } : {}),
      ...(typeof block.id === "string" && kind === "approval" ? { approvalId: block.id } : {}),
    };
  });
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "sidebar_loading":
      return { ...state, loadingSidebar: true, error: undefined };
    case "sidebar_loaded": {
      const { sidebar, projectId } = action.payload;
      return {
        ...state,
        projectId,
        cabinetId: sidebar.cabinet_id,
        projects: sidebar.projects,
        projectChats: sidebar.project_chats,
        sessionPinned: (() => {
          const selectedId = state.selectedSessionId ?? sidebar.selected_session_id;
          const selected = [...sidebar.projects.flatMap((project) => project.chats), ...sidebar.project_chats].find((chat) => chat.session_id === selectedId);
          return Boolean(selected?.pinned);
        })(),
        sessionTitle: (() => {
          const selectedId = state.selectedSessionId ?? sidebar.selected_session_id;
          const selected = [...sidebar.projects.flatMap((project) => project.chats), ...sidebar.project_chats].find((chat) => chat.session_id === selectedId);
          return selected?.title ?? state.sessionTitle;
        })(),
        selectedSessionId: state.selectedSessionId ?? sidebar.selected_session_id ?? undefined,
        loadingSidebar: false,
      };
    }
    case "sidebar_failed":
      return { ...state, loadingSidebar: false, error: action.message, retryable: true };
    case "select_session":
      return { ...state, selectedSessionId: action.sessionId, sessionTitle: action.title === undefined ? undefined : action.title ?? undefined, sessionPinned: action.pinned ?? false, blocks: action.preserveTurn ? state.blocks : [], draft: action.preserveTurn ? state.draft : "", attachments: action.preserveTurn ? state.attachments : [], approvingId: undefined, lastSeq: action.preserveTurn ? state.lastSeq : 0, hasMore: action.preserveTurn ? state.hasMore : false, oldestSeq: action.preserveTurn ? state.oldestSeq : undefined, status: action.preserveTurn ? state.status : "idle", pendingTurn: action.preserveTurn ? state.pendingTurn : undefined, error: undefined, errorCode: undefined, retryable: false, loadingTranscript: action.preserveTurn ? state.loadingTranscript : false };
    case "start_new_chat":
      return { ...state, projectId: action.projectId ?? state.projectId, selectedSessionId: undefined, sessionTitle: undefined, sessionPinned: false, blocks: [], draft: "", attachments: [], status: "idle", error: undefined, errorCode: undefined, retryable: false, pendingTurn: undefined, lastSeq: 0, hasMore: false, loadingTranscript: false, approvingId: undefined, oldestSeq: undefined };
    case "session_loading":
      return { ...state, loadingTranscript: true, error: undefined };
    case "transcript_loaded": {
      const payload = action.payload;
      const transcriptBlocks = Array.isArray(payload.blocks) ? transcriptBlocksToLocal(payload.blocks as Array<Record<string, unknown>>, action.prefix) : [];
      return {
        ...state,
        blocks: action.replace ? transcriptBlocks : rebuildFromBlocks(state.blocks, transcriptBlocks),
        loadingTranscript: false,
        oldestSeq: payload.oldest_seq ?? state.oldestSeq,
        status: payload.turn_in_progress ? "streaming" : state.status === "loading" || state.status === "streaming" ? "idle" : state.status,
        lastSeq: Math.max(state.lastSeq, payload.newest_seq ?? 0),
        hasMore: payload.has_more,
        error: undefined,
        errorCode: undefined,
        retryable: false,
      };
    }
    case "older_transcript_loaded": {
      const payload = action.payload;
      const transcriptBlocks = Array.isArray(payload.blocks) ? transcriptBlocksToLocal(payload.blocks as Array<Record<string, unknown>>, action.prefix) : [];
      return {
        ...state,
        blocks: [...transcriptBlocks, ...state.blocks.filter((current) => !transcriptBlocks.some((incoming) => incoming.id === current.id))],
        oldestSeq: payload.oldest_seq ?? state.oldestSeq,
        hasMore: payload.has_more,
      };
    }
    case "transcript_failed":
      return { ...state, loadingTranscript: false, error: action.message, retryable: true };
    case "draft_changed":
      return { ...state, draft: action.draft };
    case "attachments_changed":
      return { ...state, attachments: action.attachments };
    case "attachment_upload_started":
      return { ...state, status: "uploading", error: undefined };
    case "attachment_upload_finished":
      return { ...state, status: state.status === "uploading" ? "idle" : state.status };
    case "model_changed":
      return { ...state, model: action.model };
    case "session_patched":
      return { ...state, sessionTitle: action.payload.title === undefined ? state.sessionTitle : action.payload.title ?? undefined, sessionPinned: action.payload.pinned ?? state.sessionPinned };
    case "send_started":
      return {
        ...state,
        blocks: [...state.blocks, action.userBlock],
        status: "streaming",
        pendingTurn: action.turn,
        draft: "",
        attachments: [],
        error: undefined,
        retryable: false,
      };
    case "stream_event": {
      const event = action.event;
      const nextSeq = event.seq ?? state.lastSeq;
      if (event.seq !== undefined && event.seq <= state.lastSeq) return state;
      if (event.seq === undefined && state.blocks.some((block) => block.raw && eventFingerprint(block.raw) === eventFingerprint(event))) return state;
      return { ...state, blocks: addEvent(state.blocks, event), lastSeq: Math.max(state.lastSeq, nextSeq), status: event.type === "done" ? "idle" : event.type === "error" ? "error" : "streaming" };
    }
    case "stream_done":
      return { ...state, status: "idle", pendingTurn: undefined };
    case "stream_failed":
      return { ...state, status: "error", error: action.message, errorCode: action.code, retryable: action.retryable, pendingTurn: action.clearPending ? undefined : action.turn ?? state.pendingTurn };
    case "pending_restored":
      return { ...state, pendingTurn: action.turn, status: "streaming", error: undefined, retryable: false };
    case "stop_started":
      return { ...state, status: "stopping" };
    case "stop_finished":
      return { ...state, status: "idle", pendingTurn: undefined };
    case "approval_started":
      return { ...state, approvingId: action.id };
    case "approval_finished":
      return { ...state, approvingId: state.approvingId === action.id ? undefined : state.approvingId };
    case "approval_reconciled":
      return { ...state, error: undefined, errorCode: undefined, retryable: false };
    case "retry_started":
      return { ...state, status: "streaming", pendingTurn: action.turn, error: undefined, retryable: false };
    case "clear_error":
      return { ...state, error: undefined, errorCode: undefined, retryable: false };
    default:
      return state;
  }
}

export function makeUserBlock(turn: { turnId: string; text: string; attachmentRefs: string[] }): ChatBlock {
  return {
    id: `local-user-${turn.turnId}`,
    kind: "user",
    text: turn.text,
    ...(turn.attachmentRefs.length ? { attachments: turn.attachmentRefs.map((id, index) => ({ id, filename: `Attachment ${index + 1}` })) } : {}),
  };
}

export function appendEventForTest(state: ChatState, event: ChatEvent): ChatState {
  return chatReducer(state, { type: "stream_event", event });
}

export { EMPTY_CHAT_STATE };
