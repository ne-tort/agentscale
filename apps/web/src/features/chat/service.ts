import type { ChatSidebarResponse, ChatTranscriptResponse } from "../../api/types";
import {
  SseCursor,
  SseParser,
  UpstreamStreamMalformed,
  envelopeKind,
  readApplicationEnvelope,
  readWireEnvelope,
  streamIdentity,
} from "../../shared/sse";
import type { SseFrame, StreamIdentity, WireEventType } from "../../shared/sse";
import type { ChatAttachment, ChatEvent, PendingTurn } from "./types";

export type FetchLike = typeof fetch;

type JsonRecord = Record<string, unknown>;

/**
 * Stable error codes the chat UI keys on. Server codes are passed through when
 * they are already stable; only the stream-specific ones are normalized, so
 * existing branches (`session_create_outcome_unknown`, …) keep working.
 */
export const CHAT_STREAM_ERROR_CODES = {
  /** Upstream frame/envelope failed validation (HTTP 502 before headers, terminal error after). */
  upstreamStreamMalformed: "upstream_stream_malformed",
  /** The stream delivered frames from different runs or reused an event id. */
  streamCursorConflict: "stream_cursor_conflict",
  /** A terminal `error` frame that carries no run/sequence context. */
  streamTerminalError: "stream_terminal_error",
  /** `Last-Event-ID` could not be parsed by the bridge. */
  invalidLastEventId: "invalid_last_event_id",
  /** A bare sequence cursor is ambiguous for this request. */
  ambiguousLastEventId: "ambiguous_last_event_id",
  /** The run behind the cursor cannot be reattached. */
  runNotResumable: "run_not_resumable",
  invalidJson: "invalid_json",
  invalidEvent: "invalid_event",
  streamIncomplete: "stream_incomplete",
  streamOutcomeUnknown: "stream_outcome_unknown",
  missingStreamBody: "missing_stream_body",
} as const;

/** Bridge error code -> stable client code. */
const UPSTREAM_CODE_ALIASES: Record<string, string> = {
  UPSTREAM_STREAM_MALFORMED: CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed,
  INVALID_LAST_EVENT_ID: CHAT_STREAM_ERROR_CODES.invalidLastEventId,
  AMBIGUOUS_LAST_EVENT_ID: CHAT_STREAM_ERROR_CODES.ambiguousLastEventId,
  RUN_NOT_RESUMABLE: CHAT_STREAM_ERROR_CODES.runNotResumable,
};

/** Codes that describe a protocol failure rather than a transient outage. */
const NON_RETRYABLE_STREAM_CODES: ReadonlySet<string> = new Set([
  CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed,
  CHAT_STREAM_ERROR_CODES.streamCursorConflict,
  CHAT_STREAM_ERROR_CODES.invalidLastEventId,
  CHAT_STREAM_ERROR_CODES.ambiguousLastEventId,
  CHAT_STREAM_ERROR_CODES.runNotResumable,
]);

/** Wire `event_type` -> event type the chat reducer understands today. */
const WIRE_TO_UI_EVENT_TYPE: Record<WireEventType, string> = {
  run_started: "start",
  text_delta: "text_delta",
  transcript_block: "block",
  tool_call: "tool_call",
  tool_result: "tool_result",
  status: "status",
  error: "error",
  done: "done",
  keepalive: "keepalive",
};

/** Transient outages are retryable; protocol failures are not. */
function isRetryableStreamFailure(code: string | undefined, status: number): boolean {
  if (code !== undefined && NON_RETRYABLE_STREAM_CODES.has(code)) return false;
  return status >= 500 || status === 409;
}

/** Legacy parser output shape, kept for the exported `parseLegacySseFrames`. */
type LegacyEventFrame = {
  data: string[];
  event?: string;
};

export class LegacyChatError extends Error {
  constructor(
    message: string,
    public readonly code?: string,
    public readonly retryable = true,
    public readonly unknownOutcome = false,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "LegacyChatError";
  }
}

export type LegacyChatServiceOptions = {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetchImpl?: FetchLike;
};

export type AttachmentUpload = {
  filename: string;
  contentBase64: string;
  contentType?: string;
};

export type DownloadedAttachment = {
  blob: Blob;
  filename: string;
  contentType: string;
  size: number;
};

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseErrorMessage(payload: unknown, fallback: string): { message: string; code?: string } {
  if (!isRecord(payload)) return { message: fallback };
  const nested = isRecord(payload.error) ? payload.error : payload;
  const message = typeof nested.detail === "string"
    ? nested.detail
    : typeof nested.message === "string"
      ? nested.message
      : typeof nested.title === "string"
        ? nested.title
        : fallback;
  const code = typeof nested.code === "string" ? nested.code : undefined;
  return { message, code };
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

function mutationOutcomeUnknown(error: unknown, code: string, message: string): LegacyChatError | undefined {
  if (!(error instanceof LegacyChatError) || (error.code !== "network_error" && !(error.status !== undefined && error.status >= 500))) return undefined;
  return new LegacyChatError(message, code, false, true, error.status);
}

function normalizeBaseUrl(value: string | undefined): string {
  const base = (value ?? "/api/v1").replace(/\/+$/, "");
  return /(?:^|\/)api$/i.test(base) ? `${base}/v1` : base;
}

function attachmentFilename(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const extended = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (extended) {
    try { return decodeURIComponent(extended.replace(/^"|"$/g, "")); } catch { /* use the plain filename below */ }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

async function readWithAbort(reader: ReadableStreamDefaultReader<Uint8Array>, signal?: AbortSignal): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (!signal) return reader.read();
  if (signal.aborted) throw signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(signal.reason ?? new DOMException("The operation was aborted.", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([reader.read(), aborted]);
  } finally {
    if (onAbort) signal.removeEventListener("abort", onAbort);
  }
}

function eventKey(event: ChatEvent): string {
  if (event.seq !== undefined) return `seq:${event.seq}`;
  return `${event.type}:${event.at ?? ""}:${JSON.stringify(event.data)}`;
}

/**
 * Legacy compatibility view over the shared parser: same `{data, event}` frame
 * shape as before, but every transport rule (CRLF, comments, multi-line data,
 * chunk boundaries, final frame at EOF) now comes from `SseParser`.
 *
 * `eof = false` keeps the old "no terminating blank line, no frame" behaviour;
 * `eof = true` completes the pending line and flushes the final frame.
 */
export function parseLegacySseFrames(input: string, eof = false): LegacyEventFrame[] {
  const parser = new SseParser();
  const frames = parser.push(input).frames;
  if (eof) frames.push(...parser.end().frames);
  return frames.map((frame) => ({
    data: frame.data.split("\n"),
    ...(frame.event === undefined ? {} : { event: frame.event }),
  }));
}

// ---------------------------------------------------------------------------
// Stream classification: application (legacy `{type,data}`), canonical wire
// (`SseEventEnvelope`) and legacy control frames (`_session`, `_turn_complete`,
// `_error`, bare `error`). Transport parsing lives in `shared/sse/parser`.
// ---------------------------------------------------------------------------

type StreamFrameSource = "application" | "wire";

type DecodedStreamFrame = {
  /** Event type the reducer/UI understands. */
  type: string;
  data: Record<string, unknown>;
  at?: string;
  seq?: number;
  created_at?: string;
  source: StreamFrameSource;
  /** Transport `id:` of the frame, retained before any JSON validation. */
  transportId?: string;
  /** Last-event-id buffer in effect when the frame was dispatched. */
  lastEventId?: string;
  /** Envelope identity; an absent field means "not declared by this frame". */
  runId?: string;
  sequence?: number;
  eventId?: string;
  /** `keepalive`/heartbeat: liveness only, never moves the cursor. */
  livenessOnly?: boolean;
};

/** Resume state the caller may pass on a reconnect. */
export type StreamResumeState = {
  /** Value for `Last-Event-ID` (transport id or `run_id:sequence`). */
  lastEventId?: string;
  /** `run_id:seq` known before the first frame, when the caller already has it. */
  seed?: StreamIdentity;
};

export type StreamTurnOptions = StreamResumeState;

const STREAM_MESSAGES = {
  malformed: "The chat stream returned a malformed event. Reload the transcript and retry.",
  conflict: "The chat stream mixed events from different runs. Reload the transcript before retrying.",
  terminal: "The agent could not complete this turn.",
  incomplete: "The chat stream ended before the turn was complete. Retry to reconnect the same turn.",
  invalidLastEventId: "The chat stream cursor is not valid for this turn. The turn was not resumed.",
  notResumable: "This turn can no longer be resumed. Reload the transcript and send the message again.",
  unknownOutcome: "The connection was interrupted. Retry to reconnect the same turn.",
} as const;

function isTerminalType(frame: DecodedStreamFrame): boolean {
  // Legacy turn completion is the `_turn_complete` control frame; a legacy
  // `done` may still be followed by it, so it does not end the stream by
  // itself. A canonical wire `done` is terminal on its own.
  return frame.type === "_turn_complete" || (frame.source === "wire" && frame.type === "done");
}

function normalizeStreamCode(code: string | undefined): string | undefined {
  // Пустой код — не код: `??` оставил бы "" и замаскировал стабильный fallback,
  // поэтому пустая/пробельная строка нормализуется в отсутствие кода.
  const trimmed = code?.trim();
  if (!trimmed) return undefined;
  return UPSTREAM_CODE_ALIASES[trimmed] ?? trimmed;
}

/** HTTP status a stable stream code implies, unless the server named one. */
function streamStatusFor(code: string | undefined, fallback: number): number {
  if (code === CHAT_STREAM_ERROR_CODES.ambiguousLastEventId || code === CHAT_STREAM_ERROR_CODES.invalidLastEventId) return 400;
  if (code === CHAT_STREAM_ERROR_CODES.runNotResumable) return 409;
  if (code === CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed) return 502;
  if (code === CHAT_STREAM_ERROR_CODES.streamCursorConflict) return 409;
  return fallback;
}

/**
 * A frame-level failure after the response was opened is terminal for this
 * attempt: the server already answered, so the mutation outcome is known.
 */
function streamFailure(code: string, message: string, status: number): LegacyChatError {
  return new LegacyChatError(message, code, false, false, status);
}

/** Map a typed malformed envelope to a stable code and HTTP status. */
function malformedFailure(reason: string, unknownOutcome: boolean): LegacyChatError {
  switch (reason) {
    case "invalid_event_id":
    case "event_id_unresolved":
      return new LegacyChatError(STREAM_MESSAGES.invalidLastEventId, CHAT_STREAM_ERROR_CODES.invalidLastEventId, false, unknownOutcome, 400);
    case "run_scope_mismatch":
      return new LegacyChatError(STREAM_MESSAGES.conflict, CHAT_STREAM_ERROR_CODES.streamCursorConflict, false, unknownOutcome, 409);
    case "invalid_json":
      return new LegacyChatError("The chat stream returned invalid JSON. Retry to reconnect the same turn.", CHAT_STREAM_ERROR_CODES.invalidJson, true, true);
    default:
      return new LegacyChatError(STREAM_MESSAGES.malformed, CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed, false, unknownOutcome, 502);
  }
}

/** Convert a canonical wire envelope into the UI event. */
function wireToChatEvent(
  envelope: ReturnType<typeof readWireEnvelope>,
  frame: SseFrame,
): DecodedStreamFrame {
  const payload = envelope.payload;
  const seq = envelope.sequence;
  const at = typeof payload.at === "string" ? payload.at : undefined;
  const data: Record<string, unknown> = { ...payload };
  // Wire `status` carries the phase in `payload.status`; the reducer reads `phase`.
  if (envelope.eventType === "status" && typeof payload.status === "string" && typeof data.phase !== "string") data.phase = payload.status;
  return {
    type: WIRE_TO_UI_EVENT_TYPE[envelope.eventType],
    data,
    source: "wire",
    eventId: envelope.eventId,
    ...(envelope.runId === undefined ? {} : { runId: envelope.runId }),
    ...(seq === undefined ? {} : { seq, sequence: seq }),
    ...(at === undefined ? {} : { at }),
    ...(frame.id === undefined ? {} : { transportId: frame.id }),
    ...(frame.lastEventId === undefined ? {} : { lastEventId: frame.lastEventId }),
    ...(envelope.eventType === "keepalive" ? { livenessOnly: true } : {}),
  };
}

/**
 * Decode one SSE frame.
 *
 * `unknownOutcome` marks errors raised *before* the first successfully decoded
 * frame: the turn may or may not have started, so the caller must keep the
 * pending turn for a reconnect. Errors raised later are terminal for this
 * attempt and are reported as a stable code with a redacted message.
 *
 * Terminal frames:
 * - `_error` (legacy caught `AppError`) and `error` (bare tail error) become a
 *   `LegacyChatError` with the server's code/status; a bare `error` without a
 *   sequence gets the explicit `stream_terminal_error` code instead of a
 *   synthesized sequence.
 * - `_turn_complete` and `done` are terminal success markers for the caller.
 */
function decodeStreamFrame(frame: SseFrame): DecodedStreamFrame {
  if (frame.data.length === 0) throw new UpstreamStreamMalformed("empty_frame");
  let parsed: unknown;
  try {
    parsed = JSON.parse(frame.data);
  } catch {
    throw new UpstreamStreamMalformed("invalid_json");
  }
  const kind = envelopeKind(parsed);
  const transport = {
    ...(frame.id === undefined ? {} : { transportId: frame.id }),
    ...(frame.lastEventId === undefined ? {} : { lastEventId: frame.lastEventId }),
  };

  if (kind === "wire") {
    const envelope = readWireEnvelope(parsed, {
      ...(frame.id === undefined ? {} : { transportId: frame.id }),
      ...(frame.event === undefined ? {} : { transportEvent: frame.event }),
    });
    if (envelope.eventType === "error") {
      const payload = envelope.payload;
      const serverCode = normalizeStreamCode(typeof payload.code === "string" ? payload.code : undefined);
      const message = parseErrorMessage(payload, STREAM_MESSAGES.terminal).message;
      const status = streamStatusFor(serverCode, typeof payload.status === "number" ? payload.status : 500);
      throw streamFailure(serverCode ?? CHAT_STREAM_ERROR_CODES.streamTerminalError, message, status);
    }
    return wireToChatEvent(envelope, frame);
  }

  if (kind === "application") {
    const application = readApplicationEnvelope(parsed);
    const type = application.eventType;
    // Contract §7: transport `event:` and the envelope `event_type` must agree.
    // The check covers *every* application frame, not only error frames —
    // otherwise a `text_delta` payload arriving under `event: done` would be
    // delivered as an ordinary delta and the terminal marker silently lost.
    if (frame.event !== undefined && frame.event !== type) throw new UpstreamStreamMalformed("event_name_mismatch");
    if (type === "_error" || type === "error") {
      const payload = application.payload;
      const serverCode = normalizeStreamCode(typeof payload.code === "string" ? payload.code : undefined);
      const message = parseErrorMessage(payload, STREAM_MESSAGES.terminal).message;
      const status = streamStatusFor(serverCode, typeof payload.status === "number" ? payload.status : 500);
      // Neither legacy form carries run/sequence, so neither can become a
      // replayable event; both are surfaced as an explicit terminal code.
      throw streamFailure(
        serverCode ?? CHAT_STREAM_ERROR_CODES.streamTerminalError,
        message,
        status,
      );
    }
    const at = typeof application.payload.at === "string" ? application.payload.at : undefined;
    return {
      type,
      data: application.payload,
      source: "application",
      ...(application.runId === undefined ? {} : { runId: application.runId }),
      ...(application.sequence === undefined ? {} : { seq: application.sequence, sequence: application.sequence }),
      ...(at === undefined ? {} : { at }),
      ...(typeof application.payload.created_at === "string" ? { created_at: application.payload.created_at } : {}),
      ...(type === "keepalive" ? { livenessOnly: true } : {}),
      ...transport,
    };
  }

  throw new UpstreamStreamMalformed("invalid_event");
}

function cursorCandidate(frame: DecodedStreamFrame): StreamIdentity {
  const eventId = frame.eventId ?? frame.transportId;
  return streamIdentity({
    ...(eventId === undefined ? {} : { eventId }),
    ...(frame.runId === undefined ? {} : { runId: frame.runId }),
    ...(frame.sequence === undefined ? {} : { sequence: frame.sequence }),
  });
}

/**
 * Rebuild the cursor from a `Last-Event-ID` header value we sent earlier.
 *
 * Two documented forms: the application cursor `run_id:sequence` (exclusive
 * lower bound) and an opaque transport id (the canonical UUIDv5 `event_id`, or
 * any other server-issued id). An opaque id is only ever compared for equality:
 * it is never parsed as a number and never ordered lexicographically.
 */
function seedFromLastEventId(value: string | undefined): StreamIdentity | undefined {
  if (value === undefined || value === "") return undefined;
  const application = /^([^:]+):(\d+)$/.exec(value);
  if (application) {
    const sequence = Number.parseInt(application[2], 10);
    if (Number.isSafeInteger(sequence)) return streamIdentity({ runId: application[1], sequence });
  }
  // Anything else is a transport id: a replayed frame carrying it is dropped as
  // already handled, but no sequence is inferred from it.
  return streamIdentity({ eventId: value });
}

export class LegacyChatService {
  private readonly baseUrl: string;
  private readonly getAccessToken?: () => string | undefined;
  private readonly fetchImpl: FetchLike;
  /** Last committed `Last-Event-ID` per turn (see `lastEventIdFor`). */
  private readonly resumeCursors = new Map<string, string>();

  constructor(options: LegacyChatServiceOptions = {}) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl);
    this.getAccessToken = options.getAccessToken;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  private headers(accept = "application/json"): HeadersInit {
    const token = this.getAccessToken?.();
    return {
      Accept: accept,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        headers: {
          ...this.headers(),
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });
    } catch (error) {
      throw new LegacyChatError(error instanceof Error ? error.message : "Network request failed.", "network_error", true);
    }
    if (!response.ok) {
      const error = parseErrorMessage(await parseJson(response), response.statusText || `Request failed (${response.status})`);
      throw new LegacyChatError(error.message, error.code, response.status >= 500 || response.status === 409, false, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await parseJson(response)) as T;
  }

  getSidebar(cabinetId: string): Promise<ChatSidebarResponse> {
    return this.request<ChatSidebarResponse>(`/cabinets/${encodePath(cabinetId)}/chats/sidebar`);
  }

  listSessions(projectId: string): Promise<{ items: JsonRecord[] }> {
    return this.request<{ items: JsonRecord[] }>(`/projects/${encodePath(projectId)}/agent/sessions`);
  }

  getTranscript(projectId: string, sessionId: string, options: { beforeSeq?: number; afterSeq?: number; limit?: number } = {}): Promise<ChatTranscriptResponse> {
    const params = new URLSearchParams({ session_id: sessionId, limit: String(options.limit ?? 500) });
    if (options.beforeSeq !== undefined) params.set("before_seq", String(options.beforeSeq));
    if (options.afterSeq !== undefined) params.set("after_seq", String(options.afterSeq));
    return this.request<ChatTranscriptResponse>(`/projects/${encodePath(projectId)}/chat/transcript?${params.toString()}`);
  }

  getSessionTranscript(projectId: string, sessionId: string): Promise<ChatTranscriptResponse> {
    return this.request<ChatTranscriptResponse>(`/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/transcript`);
  }

  createSession(projectId: string, model?: string): Promise<JsonRecord> {
    return this.request<JsonRecord>(`/projects/${encodePath(projectId)}/agent/sessions`, {
      method: "POST",
      body: JSON.stringify({ ...(model ? { model } : {}) }),
    }).catch((error: unknown) => {
      throw mutationOutcomeUnknown(error, "session_create_outcome_unknown", "Session creation may have succeeded. Refresh the chat list and select the new session before sending again.") ?? error;
    });
  }

  patchSession(projectId: string, sessionId: string, patch: { title?: string | null; pin?: boolean }): Promise<JsonRecord> {
    return this.request<JsonRecord>(`/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  getDraft(projectId: string, sessionId?: string): Promise<JsonRecord> {
    const path = sessionId
      ? `/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/composer-draft`
      : `/projects/${encodePath(projectId)}/composer-draft`;
    return this.request<JsonRecord>(path);
  }

  saveDraft(projectId: string, text: string, sessionId?: string): Promise<JsonRecord> {
    const path = sessionId
      ? `/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/composer-draft`
      : `/projects/${encodePath(projectId)}/composer-draft`;
    return this.request<JsonRecord>(path, { method: "PUT", body: JSON.stringify({ text }) });
  }

  clearDraft(projectId: string, sessionId?: string): Promise<JsonRecord> {
    const path = sessionId
      ? `/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/composer-draft`
      : `/projects/${encodePath(projectId)}/composer-draft`;
    return this.request<JsonRecord>(path, { method: "DELETE" });
  }

  async uploadAttachment(projectId: string, upload: AttachmentUpload): Promise<ChatAttachment> {
    return this.request<ChatAttachment>(`/projects/${encodePath(projectId)}/attachments`, {
      method: "POST",
      body: JSON.stringify({
        filename: upload.filename,
        content_base64: upload.contentBase64,
        ...(upload.contentType ? { content_type: upload.contentType } : {}),
      }),
    }).catch((error: unknown) => {
      throw mutationOutcomeUnknown(error, "attachment_upload_outcome_unknown", "The attachment upload outcome is unknown. Refresh attachments before uploading it again.") ?? error;
    });
  }

  listAttachments(projectId: string): Promise<{ items: ChatAttachment[] }> {
    return this.request<{ items: ChatAttachment[] }>(`/projects/${encodePath(projectId)}/attachments`);
  }

  deleteAttachment(projectId: string, attachmentId: string): Promise<JsonRecord> {
    return this.request<JsonRecord>(`/projects/${encodePath(projectId)}/attachments/${encodePath(attachmentId)}`, { method: "DELETE" });
  }

  async downloadAttachment(projectId: string, attachmentId: string): Promise<DownloadedAttachment> {
    const path = `/projects/${encodePath(projectId)}/attachments/${encodePath(attachmentId)}/content`;
    const token = this.getAccessToken?.();
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        headers: {
          Accept: "application/octet-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
    } catch (error) {
      throw new LegacyChatError(error instanceof Error ? error.message : "Network request failed.", "network_error", true);
    }
    if (!response.ok) {
      const error = parseErrorMessage(await parseJson(response), response.statusText || `Request failed (${response.status})`);
      throw new LegacyChatError(error.message, error.code, response.status >= 500 || response.status === 409, false, response.status);
    }
    const blob = await response.blob();
    return {
      blob,
      filename: attachmentFilename(response.headers.get("Content-Disposition"), attachmentId),
      contentType: response.headers.get("Content-Type") ?? "application/octet-stream",
      size: blob.size,
    };
  }

  cancel(projectId: string, sessionId: string): Promise<JsonRecord> {
    return this.request<JsonRecord>(`/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/cancel`, { method: "POST" }).catch((error: unknown) => {
      throw mutationOutcomeUnknown(error, "cancel_outcome_unknown", "The stop outcome is unknown. Refresh the transcript to confirm whether the turn stopped.") ?? error;
    });
  }

  listPendingApprovals(projectId: string, sessionId: string): Promise<{ items: JsonRecord[] }> {
    return this.request<{ items: JsonRecord[] }>(`/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/pending-approvals`);
  }

  approve(projectId: string, sessionId: string, id: string, decision: "approve" | "deny"): Promise<JsonRecord> {
    return this.request<JsonRecord>(`/projects/${encodePath(projectId)}/agent/sessions/${encodePath(sessionId)}/tool-approvals`, {
      method: "POST",
      body: JSON.stringify({ id, decision }),
    }).catch((error: unknown) => {
      throw mutationOutcomeUnknown(error, "approval_outcome_unknown", "The approval outcome is unknown. Refresh the transcript before deciding again.") ?? error;
    });
  }

  async *streamTurn(
    turn: PendingTurn,
    projectId: string,
    signal?: AbortSignal,
    options: StreamTurnOptions = {},
  ): AsyncGenerator<ChatEvent> {
    const body = {
      text: turn.text,
      ...(turn.sessionId ? { session_id: turn.sessionId } : {}),
      attachment_refs: turn.attachmentRefs,
      ...(turn.model ? { model: turn.model } : {}),
      turn_id: turn.turnId,
    };
    // A reconnect sends the last committed cursor. It is only ever a value the
    // upstream produced (transport id or `run_id:sequence`), never a bare
    // sequence and never a synthesized UUID.
    const resume = options.lastEventId ?? (options.seed === undefined ? this.resumeCursor(turn.turnId) : undefined);
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/projects/${encodePath(projectId)}/chat/stream`, {
        method: "POST",
        signal,
        headers: {
          ...this.headers("text/event-stream"),
          "Content-Type": "application/json",
          ...(resume === undefined ? {} : { "Last-Event-ID": resume }),
        },
        body: JSON.stringify(body),
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new LegacyChatError(STREAM_MESSAGES.unknownOutcome, CHAT_STREAM_ERROR_CODES.streamOutcomeUnknown, true, true);
    }
    if (!response.ok) {
      const parsed = parseErrorMessage(await parseJson(response), response.statusText || `Stream failed (${response.status})`);
      const code = normalizeStreamCode(parsed.code);
      const status = streamStatusFor(code, response.status);
      // Before any event was delivered the turn outcome is unknown.
      const unknownOutcome = status >= 500;
      // A rejected cursor must not be replayed again: the resume state is only
      // usable while the bridge accepted it.
      if (code === CHAT_STREAM_ERROR_CODES.invalidLastEventId || code === CHAT_STREAM_ERROR_CODES.ambiguousLastEventId) {
        this.clearResume(turn.turnId);
      }
      throw new LegacyChatError(parsed.message, code, isRetryableStreamFailure(code, status), unknownOutcome, status);
    }
    if (!response.body) {
      throw new LegacyChatError("The chat stream has no body.", CHAT_STREAM_ERROR_CODES.missingStreamBody, true, true);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    const cursor = new SseCursor(options.seed ?? seedFromLastEventId(resume));
    let delivered = 0;
    let sawTerminal = false;
    try {
      let streamDone = false;
      while (!streamDone) {
        let chunk: ReadableStreamReadResult<Uint8Array>;
        try {
          chunk = await readWithAbort(reader, signal);
        } catch (error) {
          if (signal?.aborted) throw error;
          throw new LegacyChatError(STREAM_MESSAGES.unknownOutcome, CHAT_STREAM_ERROR_CODES.streamOutcomeUnknown, true, true);
        }
        const text = chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
        if (chunk.done) {
          streamDone = true;
        }
        const batch = chunk.done ? [...parser.push(text).frames, ...parser.end().frames] : parser.push(text).frames;
        for (const frame of batch) {
          let decoded: DecodedStreamFrame;
          try {
            decoded = decodeStreamFrame(frame);
          } catch (error) {
            throw this.translateFrameError(error, delivered > 0, turn.turnId);
          }

          const candidate = cursorCandidate(decoded);
          const verdict = cursor.inspect(candidate);
          if (verdict.kind === "conflict") {
            this.clearResume(turn.turnId);
            // A cursor that cannot be continued because the stream moved to
            // another run is not retryable; an id reused inside one run is a
            // protocol failure. Both are refused before any UI effect.
            if (verdict.reason === "run_mismatch" && delivered === 0) {
              throw streamFailure(CHAT_STREAM_ERROR_CODES.runNotResumable, STREAM_MESSAGES.notResumable, 409);
            }
            throw streamFailure(
              CHAT_STREAM_ERROR_CODES.streamCursorConflict,
              verdict.reason === "event_id_reused" ? STREAM_MESSAGES.malformed : STREAM_MESSAGES.conflict,
              409,
            );
          }
          // A replayed frame is acknowledged and dropped: the cursor was
          // committed only after this event was already handled once.
          if (verdict.kind === "duplicate" || verdict.kind === "stale") continue;

          const event: ChatEvent = {
            type: decoded.type,
            data: decoded.data,
            ...(decoded.at === undefined ? {} : { at: decoded.at }),
            ...(decoded.seq === undefined ? {} : { seq: decoded.seq }),
            ...(decoded.created_at === undefined ? {} : { created_at: decoded.created_at }),
          };
          yield event;
          delivered += 1;

          // Commit *after* the consumer accepted the event. A throwing consumer
          // aborts the generator here, so the cursor never advances for it.
          //
          // Only a transport id (server `id:` or canonical `event_id`) is
          // committed. A legacy `run_id:seq` pair is *not* turned into a
          // cursor: legacy sequence is session-local, so the pair proves
          // neither run identity nor a replay boundary.
          if (candidate.eventId !== undefined) {
            const commit = cursor.commit(candidate);
            if (commit.committed && commit.lastEventId !== undefined) this.rememberResume(turn.turnId, commit.lastEventId);
          }

          if (isTerminalType(decoded)) {
            sawTerminal = true;
            this.clearResume(turn.turnId);
            break;
          }
        }
        if (sawTerminal) break;
      }
      if (!sawTerminal) {
        throw new LegacyChatError(STREAM_MESSAGES.incomplete, CHAT_STREAM_ERROR_CODES.streamIncomplete, true, true);
      }
    } finally {
      try { await reader.cancel(); } catch { /* stream already closed */ }
      reader.releaseLock();
    }
  }

  /** Map a decoder/envelope failure to a stable code plus delivery stage. */
  private translateFrameError(error: unknown, delivered: boolean, turnId: string): unknown {
    if (error instanceof LegacyChatError) return error;
    if (error instanceof UpstreamStreamMalformed) {
      if (error.reason === "invalid_json") return malformedFailure("invalid_json", true);
      // Before the first accepted event the turn outcome is unknown; after it,
      // the malformed frame is a terminal but *known* failure.
      if (!delivered) this.clearResume(turnId);
      return malformedFailure(error.reason, !delivered);
    }
    return new LegacyChatError(STREAM_MESSAGES.malformed, CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed, false, !delivered, 502);
  }

  /**
   * Last committed `Last-Event-ID` for a turn, so a reconnect through
   * `streamTurn(turn, …)` resumes instead of replaying handled frames.
   */
  lastEventIdFor(turnId: string): string | undefined {
    return this.resumeCursors.get(turnId);
  }

  /** Drop resume state (terminal event, rejected cursor, completed turn). */
  clearResume(turnId: string): void {
    this.resumeCursors.delete(turnId);
  }

  private rememberResume(turnId: string, lastEventId: string): void {
    this.resumeCursors.set(turnId, lastEventId);
  }

  private resumeCursor(turnId: string): string | undefined {
    return this.resumeCursors.get(turnId);
  }
}

export function attachmentRefs(attachments: ChatAttachment[]): string[] {
  return attachments.map((attachment) => attachment.storage_ref ?? attachment.id).filter(Boolean);
}

export async function fileToAttachmentUpload(file: File): Promise<AttachmentUpload> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return {
    filename: file.name,
    contentBase64: btoa(binary),
    ...(file.type ? { contentType: file.type } : {}),
  };
}

export function isSameTurn(a: PendingTurn | undefined, b: PendingTurn): boolean {
  return Boolean(a && a.turnId === b.turnId && a.text === b.text && a.attachmentRefs.join("\u0000") === b.attachmentRefs.join("\u0000"));
}

export function streamEventIdentity(event: ChatEvent): string {
  return eventKey(event);
}
