import type { ApiClient, ApiError, AuthConfig, ChatRunRequest, ChatRunScope, ChatDurableRunBinding, ChatSidebarResponse, ChatStreamOptions, ChatTranscriptResponse, ContainerStatusResponse, CurrentUserResponse, Id, ProjectsResponse, SessionTokens, SseEventEnvelope } from "./types";

export class ApiRequestError extends Error {
  constructor(public readonly error: ApiError) {
    super(error.detail ?? error.title);
    this.name = "ApiRequestError";
  }
}

export class ChatStreamProtocolError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = "ChatStreamProtocolError";
  }
}

/** The POST may have been accepted; callers must reattach with the same turn, never create a new one. */
export class ChatStreamOutcomeUnknownError extends Error {
  constructor(public readonly cause: unknown) {
    super("chat_stream_outcome_unknown");
    this.name = "ChatStreamOutcomeUnknownError";
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
}

interface SseFrame { data: string; id?: string; event?: string }

class SseFrameParser {
  private line = "";
  private data: string[] = [];
  private id: string | undefined;
  private event: string | undefined;
  private swallowLf = false;
  private hasFrameField = false;

  push(input: string, eof = false): SseFrame[] {
    const frames: SseFrame[] = [];
    for (const char of input) {
      if (this.swallowLf) {
        this.swallowLf = false;
        if (char === "\n") continue;
      }
      if (char === "\r") {
        this.finishLine(frames);
        this.swallowLf = true;
      } else if (char === "\n") this.finishLine(frames);
      else this.line += char;
    }
    if (eof) {
      if (this.line.length) this.finishLine(frames);
      this.dispatch(frames);
    }
    return frames;
  }

  private finishLine(frames: SseFrame[]): void {
    const line = this.line;
    this.line = "";
    if (!line) { this.dispatch(frames); return; }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "data") { this.data.push(value); this.hasFrameField = true; }
    else if (field === "id" && !value.includes("\0")) { this.id = value; this.hasFrameField = true; }
    else if (field === "event") { this.event = value; this.hasFrameField = true; }
  }

  private dispatch(frames: SseFrame[]): void {
    if (this.hasFrameField && this.data.length) frames.push({ data: this.data.join("\n"), ...(this.id === undefined ? {} : { id: this.id }), ...(this.event === undefined ? {} : { event: this.event }) });
    this.data = [];
    this.event = undefined;
    this.hasFrameField = false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isDurableBinding(binding: ChatDurableRunBinding, scope: ChatRunScope): boolean {
  return isNonEmptyString(binding.binding_digest) && binding.tenant_id === scope.tenant_id && binding.project_id === scope.project_id && binding.session_id === scope.session_id && binding.turn_id === scope.turn_id && binding.run_id === scope.run_id;
}

const SSE_EVENT_TYPES = new Set(["run_started", "text_delta", "transcript_block", "tool_call", "tool_result", "status", "error", "done", "keepalive"]);

function validateEnvelope(frame: SseFrame, scope: ChatRunScope, binding: ChatDurableRunBinding): SseEventEnvelope {
  let parsed: unknown;
  try { parsed = JSON.parse(frame.data); } catch { throw new ChatStreamProtocolError("invalid_json_envelope"); }
  if (!isRecord(parsed) || !isNonEmptyString(parsed.event_id) || !isNonEmptyString(parsed.event_type) || !SSE_EVENT_TYPES.has(parsed.event_type) || !Number.isSafeInteger(parsed.schema_version) || (parsed.schema_version as number) < 1 || typeof parsed.occurred_at !== "string" || !Number.isFinite(Date.parse(parsed.occurred_at)) || !isNonEmptyString(parsed.project_id) || !isRecord(parsed.payload)) throw new ChatStreamProtocolError("invalid_envelope_shape");
  if (parsed.project_id !== scope.project_id || parsed.tenant_id !== undefined && parsed.tenant_id !== scope.tenant_id || parsed.session_id !== scope.session_id || parsed.turn_id !== scope.turn_id || parsed.run_id !== binding.run_id) throw new ChatStreamProtocolError("envelope_scope_mismatch");
  if (frame.event !== undefined && frame.event !== parsed.event_type) throw new ChatStreamProtocolError("transport_envelope_event_mismatch");
  if (parsed.sequence !== undefined && (!Number.isSafeInteger(parsed.sequence) || (parsed.sequence as number) < 0)) throw new ChatStreamProtocolError("invalid_envelope_sequence");
  if (parsed.resume_after !== undefined && (!Number.isSafeInteger(parsed.resume_after) || (parsed.resume_after as number) < 0)) throw new ChatStreamProtocolError("invalid_envelope_resume_after");
  return parsed as unknown as SseEventEnvelope;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl = options.baseUrl ?? "/api";

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = options.getAccessToken?.();
    const response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) {
      let error: ApiError = { code: "HTTP_ERROR", title: response.statusText, status: response.status };
      try { error = (await response.json()) as ApiError; } catch { /* preserve HTTP fallback */ }
      throw new ApiRequestError(error);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  async function* runChat(requestBody: ChatRunRequest, streamOptions: ChatStreamOptions): AsyncIterable<SseEventEnvelope> {
    const { scope, durableBinding, cursor } = streamOptions;
    if (!durableBinding || !isDurableBinding(durableBinding, scope)) throw new ChatStreamProtocolError("durable_run_binding_required");
    if (requestBody.project_id !== scope.project_id || requestBody.session_id !== scope.session_id || requestBody.turn_id !== scope.turn_id) throw new ChatStreamProtocolError("request_scope_mismatch");
    const initialEventId = cursor?.event_id;
    let lastCommittedEventId = initialEventId;
    let lastCommittedSequence = cursor?.sequence;
    const committedEventIds = new Set<string>(initialEventId ? [initialEventId] : []);
    if (cursor?.sequence !== undefined && (!Number.isSafeInteger(cursor.sequence) || cursor.sequence < 0)) throw new ChatStreamProtocolError("invalid_cursor");
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetch(`${baseUrl}/projects/${encodeURIComponent(scope.project_id)}/chat/stream`, {
        method: "POST",
        signal: streamOptions.signal,
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          "X-Chat-Tenant-ID": scope.tenant_id,
          "X-Chat-Session-ID": scope.session_id,
          "X-Chat-Turn-ID": scope.turn_id,
          "X-Chat-Run-ID": scope.run_id,
          ...(initialEventId ? { "Last-Event-ID": initialEventId } : {}),
          ...(cursor?.sequence === undefined ? {} : { "X-Chat-Event-Sequence": String(cursor.sequence) }),
        },
        body: JSON.stringify(requestBody),
      });
    } catch (error) {
      throw new ChatStreamOutcomeUnknownError(error);
    }
    if (!response.ok) {
      let error: ApiError = { code: "HTTP_ERROR", title: response.statusText, status: response.status };
      try { error = (await response.json()) as ApiError; } catch { /* preserve HTTP fallback */ }
      throw new ApiRequestError(error);
    }
    if (!response.body) throw new ChatStreamProtocolError("missing_stream_body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseFrameParser();
    let cleanupReader = true;
    try {
      while (true) {
        const chunk = await reader.read();
        const text = chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
        for (const frame of parser.push(text, chunk.done)) {
          const event = validateEnvelope(frame, scope, durableBinding);
          if (frame.id !== undefined && frame.id !== event.event_id) throw new ChatStreamProtocolError("transport_envelope_id_mismatch");
          if (event.event_id === lastCommittedEventId || committedEventIds.has(event.event_id)) continue;
          if (event.sequence !== undefined && lastCommittedSequence !== undefined && event.sequence <= lastCommittedSequence) throw new ChatStreamProtocolError("non_monotonic_cursor");
          await streamOptions.onEvent(event);
          const committed = { event_id: event.event_id, ...(event.sequence === undefined ? {} : { sequence: event.sequence }) };
          await streamOptions.onCursorCommit?.(committed);
          committedEventIds.add(event.event_id);
          lastCommittedEventId = event.event_id;
          if (event.sequence !== undefined) lastCommittedSequence = event.sequence;
          yield event;
        }
        if (chunk.done) break;
      }
      cleanupReader = false;
    } finally {
      if (cleanupReader) {
        try { await reader.cancel(); } catch { /* preserve the protocol/handler error */ }
      }
      reader.releaseLock();
    }
  }

  return {
    authConfig: () => request<AuthConfig>("/auth/config"),
    login: (username, password) => request<SessionTokens>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
    refresh: (refresh_token) => request<SessionTokens>("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token }) }),
    logout: (tokens) => request<void>("/auth/logout", { method: "POST", body: JSON.stringify(tokens) }),
    me: () => request<CurrentUserResponse>("/me"),
    listProjects: (cabinetId: Id) => request<ProjectsResponse>(`/cabinets/${encodeURIComponent(cabinetId)}/projects`),
    containerStatus: (projectId: Id) => request<ContainerStatusResponse>(`/projects/${encodeURIComponent(projectId)}/container/status`),
    chatSidebar: (cabinetId: Id) => request<ChatSidebarResponse>(`/cabinets/${encodeURIComponent(cabinetId)}/chats/sidebar`),
    chatTranscript: (projectId, sessionId, query = {}) => {
      const params = new URLSearchParams();
      params.set("session_id", sessionId);
      if (query.beforeSeq !== undefined) params.set("before_seq", String(query.beforeSeq));
      if (query.afterSeq !== undefined) params.set("after_seq", String(query.afterSeq));
      if (query.limit !== undefined) params.set("limit", String(query.limit));
      return request<ChatTranscriptResponse>(`/projects/${encodeURIComponent(projectId)}/chat/transcript?${params}`);
    },
    runChat,
  };
}
