import type { ChatSidebarResponse, ChatTranscriptResponse } from "../../api/types";
import type { ChatAttachment, ChatEvent, PendingTurn } from "./types";

export type FetchLike = typeof fetch;

type JsonRecord = Record<string, unknown>;

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

/** Parse the existing legacy SSE wire format: every data frame is {type,data}. */
export function parseLegacySseFrames(input: string, eof = false): LegacyEventFrame[] {
  const frames: LegacyEventFrame[] = [];
  let line = "";
  let data: string[] = [];
  let event: string | undefined;
  let swallowLf = false;

  const dispatch = () => {
    if (data.length) frames.push({ data, ...(event ? { event } : {}) });
    data = [];
    event = undefined;
  };
  const finishLine = () => {
    const current = line;
    line = "";
    if (!current) {
      dispatch();
      return;
    }
    if (current.startsWith(":")) return;
    const colon = current.indexOf(":");
    const field = colon < 0 ? current : current.slice(0, colon);
    const value = colon < 0 ? "" : current.slice(colon + 1).replace(/^ /, "");
    if (field === "data") data.push(value);
    if (field === "event") event = value;
  };

  for (const char of input) {
    if (swallowLf) {
      swallowLf = false;
      if (char === "\n") continue;
    }
    if (char === "\r") {
      finishLine();
      swallowLf = true;
    } else if (char === "\n") {
      finishLine();
    } else {
      line += char;
    }
  }
  if (eof) {
    if (line) finishLine();
    dispatch();
  }
  return frames;
}

function decodeFrame(frame: LegacyEventFrame): ChatEvent {
  let parsed: unknown;
  try {
    parsed = JSON.parse(frame.data.join("\n"));
  } catch {
    throw new LegacyChatError("The chat stream returned invalid JSON. Retry to reconnect the same turn.", "invalid_json", true, true);
  }
  if (!isRecord(parsed) || typeof parsed.type !== "string" || !isRecord(parsed.data)) {
    throw new LegacyChatError("The chat stream returned an invalid event. Retry to reconnect the same turn.", "invalid_event", true, true);
  }
  return {
    type: parsed.type,
    data: parsed.data,
    ...(typeof parsed.at === "string" ? { at: parsed.at } : {}),
    ...(typeof parsed.seq === "number" ? { seq: parsed.seq } : {}),
    ...(typeof parsed.created_at === "string" ? { created_at: parsed.created_at } : {}),
  };
}

function frameBoundaryLength(input: string, index: number): number {
  if (input.startsWith("\r\n\r\n", index)) return 4;
  if (input.startsWith("\n\n", index)) return 2;
  if (input.startsWith("\r\r", index)) return 2;
  return 0;
}

export class LegacyChatService {
  private readonly baseUrl: string;
  private readonly getAccessToken?: () => string | undefined;
  private readonly fetchImpl: FetchLike;

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

  async *streamTurn(turn: PendingTurn, projectId: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    const body = {
      text: turn.text,
      ...(turn.sessionId ? { session_id: turn.sessionId } : {}),
      attachment_refs: turn.attachmentRefs,
      ...(turn.model ? { model: turn.model } : {}),
      turn_id: turn.turnId,
    };
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/projects/${encodePath(projectId)}/chat/stream`, {
        method: "POST",
        signal,
        headers: {
          ...this.headers("text/event-stream"),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new LegacyChatError("The connection was interrupted. Retry to reconnect the same turn.", "stream_outcome_unknown", true, true);
    }
    if (!response.ok) {
      const error = parseErrorMessage(await parseJson(response), response.statusText || `Stream failed (${response.status})`);
      const unknownOutcome = response.status >= 500;
      throw new LegacyChatError(error.message, error.code, response.status >= 500 || response.status === 409, unknownOutcome, response.status);
    }
    if (!response.body) throw new LegacyChatError("The chat stream has no body.", "missing_stream_body", true, true);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    let sawTurnComplete = false;
    try {
      while (true) {
        let chunk: ReadableStreamReadResult<Uint8Array>;
        try {
          chunk = await readWithAbort(reader, signal);
        } catch (error) {
          if (signal?.aborted) throw error;
          throw new LegacyChatError("The connection was interrupted. Retry to reconnect the same turn.", "stream_outcome_unknown", true, true);
        }
        pending += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
        const boundaryIndexes: number[] = [];
        for (let index = 0; index < pending.length; index += 1) {
          if (frameBoundaryLength(pending, index)) boundaryIndexes.push(index);
        }
        const lastBoundary = boundaryIndexes.at(-1);
        const completeInput = lastBoundary === undefined ? "" : pending.slice(0, lastBoundary + frameBoundaryLength(pending, lastBoundary));
        if (completeInput) {
          for (const frame of parseLegacySseFrames(completeInput, false)) {
            const event = decodeFrame(frame);
            if (event.type === "_error") {
              const error = parseErrorMessage(event.data, "The agent could not complete this turn.");
              const status = typeof event.data.status === "number" ? event.data.status : 500;
              throw new LegacyChatError(error.message, error.code, status === 409, false, status);
            }
            if (event.type === "_turn_complete") sawTurnComplete = true;
            yield event;
            if (sawTurnComplete) break;
          }
          if (sawTurnComplete) break;
          pending = pending.slice(completeInput.length);
        }
        if (chunk.done) {
          for (const frame of parseLegacySseFrames(pending, true)) {
            const event = decodeFrame(frame);
            if (event.type === "_error") {
              const error = parseErrorMessage(event.data, "The agent could not complete this turn.");
              const status = typeof event.data.status === "number" ? event.data.status : 500;
              throw new LegacyChatError(error.message, error.code, status === 409, false, status);
            }
            if (event.type === "_turn_complete") sawTurnComplete = true;
            yield event;
            if (sawTurnComplete) break;
          }
          break;
        }
      }
      if (!sawTurnComplete) throw new LegacyChatError("The chat stream ended before the turn was complete. Retry to reconnect the same turn.", "stream_incomplete", true, true);
    } finally {
      try { await reader.cancel(); } catch { /* stream already closed */ }
      reader.releaseLock();
    }
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
