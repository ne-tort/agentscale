import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type { ChatSidebarResponse } from "../../api/types";
import { chatReducer, EMPTY_CHAT_STATE, makeUserBlock } from "./reducer";
import { attachmentRefs, fileToAttachmentUpload, isSameTurn, LegacyChatError, LegacyChatService } from "./service";
import type { ChatAttachment, ChatEvent, ChatState, PendingTurn } from "./types";

const TURN_STORAGE_PREFIX = "agentscale.chat.pending.v1";
const CREATE_UNKNOWN_STORAGE_PREFIX = "agentscale.chat.create-unknown.v1";

export type UseChatWorkspaceOptions = {
  projectId: string;
  cabinetId?: string;
  initialSessionId?: string;
  accessToken?: string;
  modelOptions?: string[];
  service?: LegacyChatService;
};

function storageKey(projectId: string, sessionId?: string): string {
  return `${TURN_STORAGE_PREFIX}:${projectId}:${sessionId ?? "new"}`;
}

function createUnknownStorageKey(projectId: string): string {
  return `${CREATE_UNKNOWN_STORAGE_PREFIX}:${projectId}`;
}

function readUnknownCreate(projectId: string): string[] | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(createUnknownStorageKey(projectId)) ?? "null");
    if (!Array.isArray(value) || value.some((id) => typeof id !== "string")) return undefined;
    return value;
  } catch {
    return undefined;
  }
}

function writeUnknownCreate(projectId: string, knownSessionIds: string[]): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(createUnknownStorageKey(projectId), JSON.stringify(knownSessionIds));
}

function removeUnknownCreate(projectId: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(createUnknownStorageKey(projectId));
}

function readUnknownApproval(projectId: string): { sessionId: string; approvalId: string } | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(`${TURN_STORAGE_PREFIX}:approval:${projectId}`) ?? "null");
    if (typeof value === "object" && value !== null && "sessionId" in value && "approvalId" in value && typeof value.sessionId === "string" && typeof value.approvalId === "string") {
      return { sessionId: value.sessionId, approvalId: value.approvalId };
    }
  } catch { /* discard malformed recovery state */ }
  return undefined;
}

function stopUnknownStorageKey(projectId: string, sessionId: string): string {
  return `${TURN_STORAGE_PREFIX}:stop:${projectId}:${sessionId}`;
}

function hasUnknownStop(projectId: string, sessionId?: string): boolean {
  return Boolean(sessionId && typeof window !== "undefined" && window.sessionStorage.getItem(stopUnknownStorageKey(projectId, sessionId)) === "1");
}

function uploadUnknownStorageKey(projectId: string): string {
  return `${TURN_STORAGE_PREFIX}:attachment:${projectId}`;
}

function readUnknownUpload(projectId: string): { filename: string; size: number; ids: string[] } | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(uploadUnknownStorageKey(projectId)) ?? "null");
    if (typeof value !== "object" || value === null || !("filename" in value) || typeof value.filename !== "string" || !("size" in value) || typeof value.size !== "number" || !("ids" in value) || !Array.isArray(value.ids) || value.ids.some((id) => typeof id !== "string")) return undefined;
    return { filename: value.filename, size: value.size, ids: value.ids };
  } catch {
    return undefined;
  }
}

function readPending(projectId: string, sessionId?: string): PendingTurn | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const value = JSON.parse(window.sessionStorage.getItem(storageKey(projectId, sessionId)) ?? "null") as PendingTurn | null;
    return value?.turnId && value.sessionId ? value : undefined;
  } catch {
    return undefined;
  }
}

function removePending(projectId: string, sessionId: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(storageKey(projectId, sessionId));
}

function initialChatState(projectId: string, cabinetId?: string, initialSessionId?: string): ChatState {
  const initial: ChatState = {
    ...EMPTY_CHAT_STATE(projectId),
    cabinetId,
    selectedSessionId: initialSessionId,
    modelOptions: ["Auto"],
  };
  if (readUnknownCreate(projectId) !== undefined) {
    initial.error = "A chat session may have been created. Refresh chats and select it before sending again.";
    initial.errorCode = "session_create_outcome_unknown";
    initial.retryable = false;
    initial.status = "error";
  } else if (hasUnknownStop(projectId, initialSessionId)) {
    initial.error = "The stop outcome is unknown. Refresh the transcript to confirm whether the turn stopped.";
    initial.errorCode = "cancel_outcome_unknown";
    initial.retryable = false;
    initial.status = "error";
  } else if (readUnknownUpload(projectId)) {
    initial.error = "The attachment upload outcome is unknown. Refresh chats to reconcile it before uploading again.";
    initial.errorCode = "attachment_upload_outcome_unknown";
    initial.retryable = false;
    initial.status = "error";
  }
  return initial;
}

function writePending(projectId: string, turn: PendingTurn): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(storageKey(projectId, turn.sessionId), JSON.stringify(turn));
}

function toMessage(error: unknown): { message: string; code?: string; retryable: boolean; unknownOutcome: boolean } {
  if (error instanceof LegacyChatError) return { message: error.message, code: error.code, retryable: error.retryable, unknownOutcome: error.unknownOutcome };
  return { message: error instanceof Error ? error.message : "Something went wrong.", retryable: true, unknownOutcome: false };
}

function sessionFromPayload(payload: Record<string, unknown>): string | undefined {
  const value = payload.session_id ?? payload.id;
  return typeof value === "string" ? value : undefined;
}

function createTurnId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useChatWorkspace(options: UseChatWorkspaceOptions) {
  const { projectId, cabinetId, initialSessionId, accessToken, service: providedService, modelOptions = ["Auto"] } = options;
  const service = useMemo(() => providedService ?? new LegacyChatService({ getAccessToken: () => accessToken }), [accessToken, providedService]);
  const normalizedModelOptions = useMemo(() => [...new Set(["Auto", ...modelOptions])], [modelOptions]);
  const [state, dispatch] = useReducer(chatReducer, undefined, () => ({
    ...initialChatState(projectId, cabinetId, initialSessionId),
    modelOptions: normalizedModelOptions.length ? normalizedModelOptions : ["Auto"],
  }));
  const stateRef = useRef(state);
  const streamAbortRef = useRef<AbortController | undefined>(undefined);
  const streamTurnRef = useRef<string | undefined>(undefined);
  const draftSaveTimerRef = useRef<number | undefined>(undefined);
  const draftSaveChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const draftScopeRef = useRef({ revision: 0, sessionId: initialSessionId });
  const materializedDraftSessionsRef = useRef(new Map<number, string>());
  const pendingDraftRef = useRef<{ text: string; sessionId?: string; revision: number } | undefined>(undefined);
  const selectionRevisionRef = useRef(0);
  const activeSessionRef = useRef(initialSessionId);
  const loadedProjectRef = useRef<string | undefined>(undefined);
  const loadedSessionRef = useRef<string | undefined>(undefined);
  const reattachedTurnRef = useRef<string | undefined>(undefined);
  const sessionCreateUnknownRef = useRef(readUnknownCreate(projectId) !== undefined);
  const unknownCreateCandidatesRef = useRef<string[]>([]);
  const stopUnknownRef = useRef<string | undefined>(hasUnknownStop(projectId, initialSessionId) ? initialSessionId : undefined);
  const approvalUnknownRef = useRef<{ sessionId: string; approvalId: string } | undefined>(readUnknownApproval(projectId));
  const uploadUnknownRef = useRef<{ filename: string; size: number; ids: string[] } | undefined>(readUnknownUpload(projectId));

  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => {
    if (activeSessionRef.current === state.selectedSessionId) return;
    activeSessionRef.current = state.selectedSessionId;
    ++selectionRevisionRef.current;
    loadedSessionRef.current = undefined;
  }, [state.selectedSessionId]);

  useEffect(() => {
    const createMarker = readUnknownCreate(projectId);
    sessionCreateUnknownRef.current = createMarker !== undefined;
    unknownCreateCandidatesRef.current = [];
    approvalUnknownRef.current = readUnknownApproval(projectId);
    stopUnknownRef.current = hasUnknownStop(projectId, stateRef.current.selectedSessionId) ? stateRef.current.selectedSessionId : undefined;
    uploadUnknownRef.current = readUnknownUpload(projectId);
  }, [projectId]);

  useEffect(() => {
    const sessionId = state.selectedSessionId;
    if (!hasUnknownStop(projectId, sessionId)) return;
    stopUnknownRef.current = sessionId;
    if (state.errorCode !== "cancel_outcome_unknown") {
      dispatch({ type: "stream_failed", message: "The stop outcome is unknown. Refresh the transcript to confirm whether the turn stopped.", code: "cancel_outcome_unknown", retryable: false });
    }
  }, [projectId, state.errorCode, state.selectedSessionId]);

  const loadSidebar = useCallback(async () => {
    if (!cabinetId) return;
    dispatch({ type: "sidebar_loading" });
    try {
      const sidebar = await service.getSidebar(cabinetId);
      dispatch({ type: "sidebar_loaded", payload: { sidebar, projectId } });
    } catch (error) {
      const failure = toMessage(error);
      dispatch({ type: "sidebar_failed", message: failure.message });
    }
  }, [cabinetId, projectId, service]);

  const persistDraft = useCallback((text: string, sessionId: string | undefined, scopeRevision: number) => {
    let targetSessionId = sessionId;
    const write = draftSaveChainRef.current.catch(() => undefined).then(async () => {
      targetSessionId = materializedDraftSessionsRef.current.get(scopeRevision) ?? sessionId;
      const payload = text.trim()
        ? await service.saveDraft(projectId, text, targetSessionId)
        : await service.clearDraft(projectId, targetSessionId);
      pendingDraftRef.current = undefined;
      const createdSessionId = payload.materialized === true ? sessionFromPayload(payload) : undefined;
      if (createdSessionId) {
        materializedDraftSessionsRef.current.set(scopeRevision, createdSessionId);
        const currentScope = draftScopeRef.current;
        if (currentScope.revision === scopeRevision && activeSessionRef.current === sessionId) {
          activeSessionRef.current = createdSessionId;
          draftScopeRef.current = { revision: scopeRevision, sessionId: createdSessionId };
          ++selectionRevisionRef.current;
          dispatch({ type: "select_session", sessionId: createdSessionId, preserveTurn: true });
          void loadSidebar();
        }
      }
      return payload;
    });
    // The serialization latch must never stay rejected. Callers see the failure
    // through the returned `write`; the ref is only a sequencing chain, and a
    // rejected promise parked there has no attached handler until the *next*
    // save begins — the browser reports it as an unhandled rejection meanwhile.
    // Keep the latch settled-safe and let `write` carry the error to its caller.
    draftSaveChainRef.current = write.then(
      () => undefined,
      (error: unknown) => {
        pendingDraftRef.current = { text, sessionId: targetSessionId, revision: scopeRevision };
      },
    );
    return write;
  }, [loadSidebar, projectId, service]);

  const loadTranscript = useCallback(async (sessionId: string, options: { afterSeq?: number; beforeSeq?: number; replace?: boolean } = {}) => {
    if (activeSessionRef.current !== sessionId) {
      if (stateRef.current.selectedSessionId !== sessionId) return undefined;
      activeSessionRef.current = sessionId;
      ++selectionRevisionRef.current;
    }
    const revision = selectionRevisionRef.current;
    const { replace, ...query } = options;
    if (query.afterSeq === undefined) dispatch({ type: "session_loading" });
    try {
      const transcript = await service.getTranscript(projectId, sessionId, query);
      if (activeSessionRef.current !== sessionId || selectionRevisionRef.current !== revision) return undefined;
      dispatch({ type: "transcript_loaded", payload: transcript, replace, prefix: query.afterSeq === undefined ? "transcript" : `tail-${query.afterSeq}` });
      if (query.afterSeq === undefined && (stateRef.current.status === "stopping" || stopUnknownRef.current === sessionId)) {
        if (transcript.turn_in_progress) {
          dispatch({ type: "stream_failed", message: "The stop outcome is still unknown. Refresh the transcript again to confirm.", code: "cancel_outcome_unknown", retryable: false, turn: stateRef.current.pendingTurn });
        } else {
          stopUnknownRef.current = undefined;
          if (typeof window !== "undefined") window.sessionStorage.removeItem(stopUnknownStorageKey(projectId, sessionId));
          removePending(projectId, sessionId);
          dispatch({ type: "stop_finished" });
        }
      }
      if (query.afterSeq === undefined && approvalUnknownRef.current?.sessionId === sessionId) {
        const unresolved = approvalUnknownRef.current;
        const pendingApprovals = Array.isArray(transcript.pending_approvals) ? transcript.pending_approvals : [];
        if (pendingApprovals.some((approval) => approval.id === unresolved.approvalId)) {
          dispatch({ type: "stream_failed", message: "The approval outcome is still unknown. Refresh again before deciding.", code: "approval_outcome_unknown", retryable: false });
        } else {
          approvalUnknownRef.current = undefined;
          if (typeof window !== "undefined") window.sessionStorage.removeItem(`${TURN_STORAGE_PREFIX}:approval:${projectId}`);
          dispatch({ type: "approval_reconciled" });
        }
      }
      return transcript;
    } catch (error) {
      if (activeSessionRef.current !== sessionId || selectionRevisionRef.current !== revision) return undefined;
      const failure = toMessage(error);
      dispatch({ type: "transcript_failed", message: failure.message });
      return undefined;
    }
  }, [projectId, service]);

  const loadOlder = useCallback(async () => {
    const sessionId = stateRef.current.selectedSessionId;
    const revision = selectionRevisionRef.current;
    const beforeSeq = stateRef.current.oldestSeq;
    if (!sessionId || activeSessionRef.current !== sessionId || beforeSeq === undefined || !stateRef.current.hasMore) return;
    try {
      const transcript = await service.getTranscript(projectId, sessionId, { beforeSeq });
      if (activeSessionRef.current !== sessionId || selectionRevisionRef.current !== revision) return;
      dispatch({ type: "older_transcript_loaded", payload: transcript, prefix: `before-${beforeSeq}` });
    } catch (error) {
      if (activeSessionRef.current !== sessionId || selectionRevisionRef.current !== revision) return;
      const failure = toMessage(error);
      dispatch({ type: "transcript_failed", message: failure.message });
    }
  }, [projectId, service]);

  const selectSession = useCallback((sessionId?: string) => {
    const previousSessionId = activeSessionRef.current;
    if (previousSessionId === sessionId && stateRef.current.selectedSessionId === sessionId) return;
    const previousDraft = stateRef.current.draft;
    const revision = ++selectionRevisionRef.current;
    activeSessionRef.current = sessionId;
    const oldDraftScope = draftScopeRef.current;
    const nextDraftScope = { revision: oldDraftScope.revision + 1, sessionId };
    draftScopeRef.current = nextDraftScope;
    if (draftSaveTimerRef.current !== undefined) {
      window.clearTimeout(draftSaveTimerRef.current);
      draftSaveTimerRef.current = undefined;
      void persistDraft(previousDraft, oldDraftScope.sessionId, oldDraftScope.revision).catch(() => undefined);
    }
    if (sessionId && unknownCreateCandidatesRef.current.length === 1 && unknownCreateCandidatesRef.current[0] === sessionId) {
      sessionCreateUnknownRef.current = false;
      unknownCreateCandidatesRef.current = [];
      removeUnknownCreate(projectId);
      dispatch({ type: "clear_error" });
    }
    streamAbortRef.current?.abort();
    streamTurnRef.current = undefined;
    reattachedTurnRef.current = undefined;
    loadedSessionRef.current = undefined;
    stopUnknownRef.current = hasUnknownStop(projectId, sessionId) ? sessionId : undefined;
    const selected = sessionId
      ? [...stateRef.current.projects.flatMap((project) => project.chats), ...stateRef.current.projectChats].find((chat) => chat.session_id === sessionId)
      : undefined;
    dispatch({ type: "select_session", sessionId, title: selected?.title, pinned: selected?.pinned });
    if (stopUnknownRef.current === sessionId && sessionId) {
      dispatch({ type: "stream_failed", message: "The stop outcome is unknown. Refresh the transcript to confirm whether the turn stopped.", code: "cancel_outcome_unknown", retryable: false });
    }
    void service.getDraft(projectId, materializedDraftSessionsRef.current.get(nextDraftScope.revision) ?? sessionId).then((draft) => {
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      if (typeof draft?.text === "string") dispatch({ type: "draft_changed", draft: draft.text });
    }).catch(() => undefined);
  }, [persistDraft, projectId, service]);

  useEffect(() => {
    if (!cabinetId || loadedProjectRef.current === `${cabinetId}:${projectId}`) return;
    loadedProjectRef.current = `${cabinetId}:${projectId}`;
    void loadSidebar();
  }, [cabinetId, loadSidebar, projectId]);

  useEffect(() => {
    if (!state.selectedSessionId || loadedSessionRef.current === state.selectedSessionId) return;
    loadedSessionRef.current = state.selectedSessionId;
    void loadTranscript(state.selectedSessionId);
  }, [loadTranscript, state.selectedSessionId]);

  useEffect(() => {
    if (!state.selectedSessionId || state.status !== "streaming" || reattachedTurnRef.current) return;
    const pending = readPending(projectId, state.selectedSessionId);
    if (!pending) return;
    reattachedTurnRef.current = pending.turnId;
    dispatch({ type: "pending_restored", turn: pending });
  }, [projectId, state.selectedSessionId, state.status]);
  const startNewChat = useCallback((nextProjectId = projectId) => {
    if (sessionCreateUnknownRef.current || readUnknownCreate(projectId) !== undefined || uploadUnknownRef.current) return;
    const previousScope = draftScopeRef.current;
    const nextScope = { revision: previousScope.revision + 1, sessionId: undefined };
    activeSessionRef.current = undefined;
    draftScopeRef.current = nextScope;
    ++selectionRevisionRef.current;
    loadedSessionRef.current = undefined;
    if (draftSaveTimerRef.current !== undefined) {
      window.clearTimeout(draftSaveTimerRef.current);
      draftSaveTimerRef.current = undefined;
      void persistDraft(stateRef.current.draft, previousScope.sessionId, previousScope.revision).catch(() => undefined);
    }
    streamAbortRef.current?.abort();
    streamTurnRef.current = undefined;
    reattachedTurnRef.current = undefined;
    loadedSessionRef.current = undefined;
    dispatch({ type: "start_new_chat", projectId: nextProjectId });
    if (nextProjectId === projectId) {
      const revision = selectionRevisionRef.current;
      void service.getDraft(nextProjectId).then((draft) => {
        if (selectionRevisionRef.current !== revision || activeSessionRef.current) return;
        if (typeof draft?.text === "string") dispatch({ type: "draft_changed", draft: draft.text });
      }).catch(() => undefined);
    }
  }, [persistDraft, projectId, service]);

  const saveDraft = useCallback((text: string) => {
    dispatch({ type: "draft_changed", draft: text });
    const scope = draftScopeRef.current;
    if (draftSaveTimerRef.current !== undefined) window.clearTimeout(draftSaveTimerRef.current);
    draftSaveTimerRef.current = window.setTimeout(() => {
      void persistDraft(text, scope.sessionId, scope.revision).catch(() => undefined);
    }, 350);
  }, [persistDraft]);

  useEffect(() => () => {
    if (draftSaveTimerRef.current !== undefined) window.clearTimeout(draftSaveTimerRef.current);
    streamAbortRef.current?.abort();
  }, []);

  const handleStream = useCallback(async (turn: PendingTurn) => {
    if (streamTurnRef.current === turn.turnId) return;
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    let streamSelectionRevision = selectionRevisionRef.current;
    streamAbortRef.current = controller;
    streamTurnRef.current = turn.turnId;
    writePending(projectId, turn);
    try {
      // The service keeps the last committed cursor per `turnId`, so a retry of
      // this same turn resumes with `Last-Event-ID` instead of replaying events
      // the UI already applied. No cursor is passed for a new turn.
      for await (const event of service.streamTurn(turn, projectId, controller.signal)) {
        if (controller.signal.aborted) return;
        if (streamSelectionRevision !== selectionRevisionRef.current) {
          controller.abort();
          return;
        }
        if (event.type === "_session") {
          const sessionId = sessionFromPayload(event.data);
          if (sessionId && sessionId !== stateRef.current.selectedSessionId) {
            activeSessionRef.current = sessionId;
            draftScopeRef.current = { revision: draftScopeRef.current.revision + 1, sessionId };
            ++selectionRevisionRef.current;
            loadedSessionRef.current = undefined;
            streamSelectionRevision = selectionRevisionRef.current;
            dispatch({ type: "select_session", sessionId, preserveTurn: true });
            writePending(projectId, { ...turn, sessionId });
          }
          continue;
        }
        if (event.type === "_turn_complete") {
          dispatch({ type: "stream_done" });
          removePending(projectId, turn.sessionId);
          continue;
        }
        dispatch({ type: "stream_event", event });
      }
      if (controller.signal.aborted || streamSelectionRevision !== selectionRevisionRef.current) return;
      dispatch({ type: "stream_done" });
      removePending(projectId, turn.sessionId);
      void loadSidebar();
    } catch (error) {
      if (controller.signal.aborted || streamSelectionRevision !== selectionRevisionRef.current) return;
      const failure = toMessage(error);
      if (failure.retryable) {
        dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: true, turn });
        writePending(projectId, turn);
      } else {
        dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: false, turn, clearPending: true });
        removePending(projectId, turn.sessionId);
      }
    } finally {
      if (streamAbortRef.current === controller) streamAbortRef.current = undefined;
      if (streamTurnRef.current === turn.turnId) streamTurnRef.current = undefined;
    }
  }, [loadSidebar, projectId, service]);

  const send = useCallback(async () => {
    let sessionId = stateRef.current.selectedSessionId;
    const current = stateRef.current;
    const selectionRevision = selectionRevisionRef.current;
    if (sessionCreateUnknownRef.current || current.errorCode === "session_create_outcome_unknown") return;
    if (current.status === "streaming" || current.status === "loading" || current.status === "stopping" || current.status === "uploading") return;
    if (!current.draft.trim() && current.attachments.length === 0) return;
    if (draftSaveTimerRef.current !== undefined) {
      window.clearTimeout(draftSaveTimerRef.current);
      draftSaveTimerRef.current = undefined;
      await persistDraft(current.draft, draftScopeRef.current.sessionId, draftScopeRef.current.revision).catch(() => undefined);
      if (selectionRevisionRef.current !== selectionRevision) return;
      sessionId = stateRef.current.selectedSessionId;
    }
    if (!sessionId) {
      dispatch({ type: "session_loading" });
      let knownSessionIds: string[] = [];
      try {
        const existing = await service.listSessions(projectId);
        if (selectionRevisionRef.current !== selectionRevision) return;
        knownSessionIds = existing.items.map((item) => item.id).filter((id): id is string => typeof id === "string");
        const created = await service.createSession(projectId, current.model === "Auto" ? undefined : current.model);
        if (selectionRevisionRef.current !== selectionRevision) return;
        sessionId = sessionFromPayload(created);
      } catch (error) {
        const failure = toMessage(error);
        if (failure.code === "session_create_outcome_unknown") {
          sessionCreateUnknownRef.current = true;
          writeUnknownCreate(projectId, knownSessionIds);
        }
        dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: failure.retryable });
        return;
      }
      if (!sessionId) {
        sessionCreateUnknownRef.current = true;
        writeUnknownCreate(projectId, knownSessionIds);
        dispatch({
          type: "stream_failed",
          message: "The server response did not identify the session. Refresh chats before sending again.",
          code: "session_create_outcome_unknown",
          retryable: false,
        });
        return;
      }
      activeSessionRef.current = sessionId;
      const oldDraftScope = draftScopeRef.current;
      const nextScope = { revision: oldDraftScope.revision + 1, sessionId };
      draftScopeRef.current = nextScope;
      materializedDraftSessionsRef.current.set(nextScope.revision, sessionId);
      ++selectionRevisionRef.current;
      loadedSessionRef.current = undefined;
      dispatch({ type: "select_session", sessionId });
    }
    const turn: PendingTurn = {
      turnId: createTurnId(),
      sessionId,
      text: current.draft.trim(),
      attachmentRefs: attachmentRefs(current.attachments),
      ...(current.model && current.model !== "Auto" ? { model: current.model } : {}),
      submittedAt: Date.now(),
    };
    dispatch({ type: "send_started", turn, userBlock: makeUserBlock(turn) });
    await service.clearDraft(projectId, sessionId).catch(() => undefined);
    await handleStream(turn);
  }, [handleStream, projectId, service]);

  const retry = useCallback(async () => {
    const current = stateRef.current;
    const pending = current.pendingTurn ?? readPending(projectId, current.selectedSessionId);
    if (!pending || !isSameTurn(readPending(projectId, current.selectedSessionId), pending) && current.status !== "error") return;
    dispatch({ type: "retry_started", turn: pending });
    await handleStream(pending);
  }, [handleStream, projectId]);

  const stop = useCallback(async () => {
    const current = stateRef.current;
    const sessionId = current.selectedSessionId;
    const revision = selectionRevisionRef.current;
    const retryingUnknownStop = Boolean(sessionId && stopUnknownRef.current === sessionId && current.errorCode === "cancel_outcome_unknown");
    if (!sessionId || (!retryingUnknownStop && current.status !== "streaming" && current.status !== "loading")) return;
    dispatch({ type: "stop_started" });
    if (!retryingUnknownStop) {
      streamAbortRef.current?.abort();
      streamTurnRef.current = undefined;
    }
    stopUnknownRef.current = sessionId;
    if (typeof window !== "undefined") window.sessionStorage.setItem(stopUnknownStorageKey(projectId, sessionId), "1");
    try {
      await service.cancel(projectId, sessionId);
    } catch (error) {
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      const failure = toMessage(error);
      if (failure.code === "cancel_outcome_unknown") {
        stopUnknownRef.current = sessionId;
        if (typeof window !== "undefined") window.sessionStorage.setItem(stopUnknownStorageKey(projectId, sessionId), "1");
      } else {
        stopUnknownRef.current = undefined;
        if (typeof window !== "undefined") window.sessionStorage.removeItem(stopUnknownStorageKey(projectId, sessionId));
      }
      dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: false, turn: current.pendingTurn });
      return;
    }
    await loadTranscript(sessionId);
  }, [loadTranscript, projectId, service]);

  const approve = useCallback(async (id: string, decision: "approve" | "deny") => {
    const sessionId = stateRef.current.selectedSessionId;
    const revision = selectionRevisionRef.current;
    if (!sessionId) return;
    if (approvalUnknownRef.current?.sessionId === sessionId && approvalUnknownRef.current.approvalId === id) return;
    dispatch({ type: "approval_started", id });
    try {
      await service.approve(projectId, sessionId, id, decision);
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      dispatch({ type: "approval_finished", id });
      await loadTranscript(sessionId, { afterSeq: stateRef.current.lastSeq });
    } catch (error) {
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      const failure = toMessage(error);
      if (failure.code === "approval_outcome_unknown") {
        approvalUnknownRef.current = { sessionId, approvalId: id };
        if (typeof window !== "undefined") window.sessionStorage.setItem(`${TURN_STORAGE_PREFIX}:approval:${projectId}`, JSON.stringify(approvalUnknownRef.current));
      }
      dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: failure.retryable });
      dispatch({ type: "approval_finished", id });
    }
  }, [loadTranscript, projectId, service]);

  const chooseFile = useCallback(async (file: File) => {
    if (uploadUnknownRef.current) return;
    const sessionId = stateRef.current.selectedSessionId;
    const revision = selectionRevisionRef.current;
    let upload: Awaited<ReturnType<typeof fileToAttachmentUpload>>;
    let known: Awaited<ReturnType<typeof service.listAttachments>> | undefined;
    try {
      upload = await fileToAttachmentUpload(file);
      known = await service.listAttachments(projectId);
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
    } catch (error) {
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      const failure = toMessage(error);
      dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: failure.retryable });
      return;
    }
    if (!known) {
      dispatch({ type: "stream_failed", message: "Could not verify existing attachments. Refresh chats before uploading.", code: "attachment_list_failed", retryable: true });
      return;
    }
    dispatch({ type: "attachment_upload_started" });
    try {
      const uploaded = await service.uploadAttachment(projectId, upload);
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      const attachments = [...stateRef.current.attachments, uploaded];
      dispatch({ type: "attachments_changed", attachments });
    } catch (error) {
      if (selectionRevisionRef.current !== revision || activeSessionRef.current !== sessionId) return;
      const failure = toMessage(error);
      if (failure.code === "attachment_upload_outcome_unknown") {
        uploadUnknownRef.current = {
          filename: upload.filename,
          size: file.size,
          ids: known.items.map((item) => item.id),
        };
        if (typeof window !== "undefined") window.sessionStorage.setItem(uploadUnknownStorageKey(projectId), JSON.stringify(uploadUnknownRef.current));
      }
      dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: failure.retryable });
    } finally {
      dispatch({ type: "attachment_upload_finished" });
    }
  }, [projectId, service]);

  const patchSession = useCallback(async (patch: { title?: string | null; pin?: boolean }) => {
    const sessionId = stateRef.current.selectedSessionId;
    if (!sessionId) return;
    try {
      const payload = await service.patchSession(projectId, sessionId, patch);
      dispatch({ type: "session_patched", payload: { title: typeof payload.title === "string" ? payload.title : patch.title, pinned: typeof payload.pinned === "boolean" ? payload.pinned : patch.pin } });
      void loadSidebar();
    } catch (error) {
      const failure = toMessage(error);
      dispatch({ type: "stream_failed", message: failure.message, code: failure.code, retryable: failure.retryable });
    }
  }, [loadSidebar, projectId, service]);

  const reload = useCallback(async () => {
    if (pendingDraftRef.current) {
      const pendingDraft = pendingDraftRef.current;
      try {
        await persistDraft(pendingDraft.text, pendingDraft.sessionId, pendingDraft.revision);
      } catch {
        return;
      }
    }
    await loadSidebar();
    if (uploadUnknownRef.current) {
      const attachments = await service.listAttachments(projectId).catch(() => undefined);
      if (!attachments) return;
      const marker = uploadUnknownRef.current;
      const matches = attachments.items.filter((attachment) => !marker.ids.includes(attachment.id)
        && attachment.filename === marker.filename
        && (attachment.size_bytes ?? attachment.size) === marker.size);
      if (matches.length === 1) {
        dispatch({ type: "attachments_changed", attachments: [...stateRef.current.attachments, matches[0]] });
        uploadUnknownRef.current = undefined;
        if (typeof window !== "undefined") window.sessionStorage.removeItem(uploadUnknownStorageKey(projectId));
        dispatch({ type: "clear_error" });
      } else if (!matches.length) {
        dispatch({ type: "stream_failed", message: "No matching attachment was found in the recent list. Do not upload it again until its status is confirmed.", code: "attachment_upload_outcome_unknown", retryable: false });
        return;
      } else {
        dispatch({ type: "stream_failed", message: "Several matching attachments exist. Remove or inspect them before sending.", code: "attachment_upload_outcome_ambiguous", retryable: false });
        return;
      }
    }
    const sessionId = stateRef.current.selectedSessionId;
    const knownIds = readUnknownCreate(projectId);
    if (knownIds !== undefined) {
      const sessions = await service.listSessions(projectId).catch(() => undefined);
      if (!sessions) return;
      unknownCreateCandidatesRef.current = sessions.items
        .map((item) => item.id)
        .filter((id): id is string => typeof id === "string" && !knownIds.includes(id));
      if (unknownCreateCandidatesRef.current.length === 1 && unknownCreateCandidatesRef.current[0] === sessionId) {
        sessionCreateUnknownRef.current = false;
        unknownCreateCandidatesRef.current = [];
        removeUnknownCreate(projectId);
        dispatch({ type: "clear_error" });
      }
      if (sessionCreateUnknownRef.current) return;
    }
    if (!sessionId) {
      dispatch({ type: "clear_error" });
      return;
    }
    const transcript = await loadTranscript(sessionId);
    if (!transcript) return;
    if (sessionCreateUnknownRef.current) {
      dispatch({ type: "stream_failed", message: "A chat session may have been created. Refresh chats and select the new session before sending again.", code: "session_create_outcome_unknown", retryable: false });
    }
    const pending = readPending(projectId, sessionId);
    if (!pending) return;
    if (transcript.turn_in_progress) {
      dispatch({ type: "retry_started", turn: pending });
      await handleStream(pending);
      return;
    }
    removePending(projectId, sessionId);
    dispatch({ type: "stream_done" });
  }, [handleStream, loadSidebar, loadTranscript, persistDraft, projectId, service]);

  useEffect(() => {
    if (state.status !== "streaming" || !state.selectedSessionId) return;
    const pending = state.pendingTurn ?? readPending(projectId, state.selectedSessionId);
    if (pending && streamTurnRef.current !== pending.turnId) void handleStream(pending);
    const interval = window.setInterval(() => {
      void loadTranscript(stateRef.current.selectedSessionId!, { afterSeq: stateRef.current.lastSeq });
    }, 1500);
    return () => window.clearInterval(interval);
  }, [handleStream, loadTranscript, projectId, state.pendingTurn, state.selectedSessionId, state.status]);

  return {
    state,
    sessionCreateBlocked: sessionCreateUnknownRef.current,
    approvalBlocked: state.errorCode === "approval_outcome_unknown" || Boolean(state.selectedSessionId && approvalUnknownRef.current?.sessionId === state.selectedSessionId),
    actions: {
      loadSidebar,
      reload,
      loadOlder,
      startNewChat,
      selectSession,
      saveDraft,
      send,
      retry,
      stop,
      approve,
      chooseFile,
      patchSession,
      setModel: (model?: string) => dispatch({ type: "model_changed", model }),
      removeAttachment: (id: string) => dispatch({ type: "attachments_changed", attachments: stateRef.current.attachments.filter((attachment) => attachment.id !== id) }),
      clearError: () => {
        if (stateRef.current.errorCode === "session_create_outcome_unknown" || stateRef.current.errorCode === "approval_outcome_unknown" || stateRef.current.errorCode === "cancel_outcome_unknown" || stateRef.current.errorCode === "attachment_upload_outcome_unknown") return;
        dispatch({ type: "clear_error" });
      },
    },
  };
}

export type ChatWorkspaceController = ReturnType<typeof useChatWorkspace>;
export type { ChatSidebarResponse, ChatEvent, ChatState, ChatAttachment };
