import type {
  AgentSession,
  ApiClient,
  ApiError,
  AdminModule,
  AdminProfile,
  AttachmentUpload,
  CabinetBoundModule,
  CompanyModule,
  ContentAsset,
  ContentAssetVersion,
  ContentAssetVersionUpload,
  CreateOutgoingWebhook,
  CreatedOutgoingWebhook,
  AuthConfig,
  Cabinet,
  ChatAttachment,
  ChatErrorPolicy,
  ChatRunRequest,
  ChatSendResponse,
  ChatSidebarResponse,
  ChatStreamEvent,
  ChatStreamOptions,
  ChatTranscriptResponse,
  ComposerDraft,
  ContainerStatusResponse,
  CurrentUserResponse,
  CurrentUserWireResponse,
  DownloadedFile,
  Id,
  JsonObject,
  ModelCatalogEntry,
  ProjectModelsLiveResponse,
  ProjectRuntimeModule,
  ProjectModulesSettingsResponse,
  ProjectModuleSettings,
  ProjectSchedule,
  ProjectScheduleInput,
  ProjectSchedulePatch,
  ProjectSelection,
  ProjectSummary,
  ProjectTrigger,
  ProjectsResponse,
  ModuleActionInvocation,
  ModuleDataQuery,
  ModuleDataRows,
  ModuleMetaDocument,
  ModuleSecretUpload,
  ModuleBinding,
  OutgoingWebhook,
  WebhookDelivery,
  ModuleFileReference,
  SessionTokens,
  ToolApproval,
  ToolApprovalDecisionResponse,
  TriggerListResponse,
  UploadedModuleFile,
  WorkspaceListing,
  WorkspaceMutationResponse,
  WorkspacePreview,
} from "./types";
import { SseCursor, SseParser, UpstreamStreamMalformed, envelopeKind, readWireEnvelope, streamIdentity } from "../shared/sse";
import type { SseFrame, StreamIdentity, WireEventType } from "../shared/sse";

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

/** A stream transport failed; the client never retries or invents replay state. */
export class ChatStreamNetworkError extends Error {
  constructor(public readonly cause: unknown) {
    super("chat_stream_network_error");
    this.name = "ChatStreamNetworkError";
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

/** `ChatStreamOptions` plus the reconnect cursor header value. */
export type ChatRunReconnectOptions = ChatStreamOptions & {
  /** Value for `Last-Event-ID`; only sent when resuming the same turn. */
  lastEventId?: string;
};

/** Wire `event_type` -> event type the chat UI understands today. */
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

function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJsonBody(body: BodyInit | null | undefined): body is string {
  return typeof body === "string";
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

function projectPath(projectId: Id): string {
  return `/projects/${encodePath(projectId)}`;
}

function sessionPath(projectId: Id, sessionId: Id): string {
  return `${projectPath(projectId)}/agent/sessions/${encodePath(sessionId)}`;
}

function filenameFromContentDisposition(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded.replace(/^"|"$/g, "")); } catch { /* use the plain filename below */ }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

function errorFromPayload(payload: unknown, status: number, statusText: string): ApiError {
  if (isRecord(payload)) {
    const nested = isRecord(payload.error) ? payload.error : payload;
    return {
      ...nested,
      code: typeof nested.code === "string" ? nested.code : `HTTP_${status}`,
      title: typeof nested.title === "string" ? nested.title : statusText || "Request failed",
      status: typeof nested.status === "number" ? nested.status : status,
      ...(typeof nested.detail === "string" ? { detail: nested.detail } : {}),
    };
  }
  return { code: `HTTP_${status}`, title: statusText || "Request failed", status };
}

/**
 * Seed the replay cursor from a `Last-Event-ID` value.
 *
 * Only the legacy `run_id:sequence` form is resolvable here. A UUID cursor is
 * sent back verbatim but is never parsed as a sequence and never re-derived
 * locally: that would require a server-side run index.
 */
function seedFromLastEventId(value: string | undefined): StreamIdentity | undefined {
  if (value === undefined || value === "") return undefined;
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return undefined;
  const match = /^(.+):(\d+)$/.exec(value);
  if (!match) return undefined;
  const sequence = Number.parseInt(match[2], 10);
  if (!Number.isSafeInteger(sequence)) return undefined;
  return streamIdentity({ runId: match[1], sequence });
}

function protocolFailure(error: unknown): ChatStreamProtocolError {
  if (error instanceof ChatStreamProtocolError) return error;
  if (error instanceof UpstreamStreamMalformed) {
    if (error.reason === "invalid_event_id" || error.reason === "event_id_unresolved") return new ChatStreamProtocolError("invalid_last_event_id");
    if (error.reason === "run_scope_mismatch") return new ChatStreamProtocolError("stream_cursor_conflict");
    return new ChatStreamProtocolError("upstream_stream_malformed");
  }
  return new ChatStreamProtocolError("upstream_stream_malformed");
}

/**
 * Decode one frame: legacy `{type, data}` or the canonical wire envelope.
 * Transport `id:`/`event:` values are validated against the envelope, and the
 * frame is rejected as a whole when the two disagree.
 */
function parseStreamEvent(frame: SseFrame): ChatStreamEvent {
  if (frame.data.length === 0) throw new ChatStreamProtocolError("empty_stream_frame");
  let parsed: unknown;
  try { parsed = JSON.parse(frame.data); } catch { throw new ChatStreamProtocolError("invalid_json_event"); }

  const kind = envelopeKind(parsed);
  if (kind === "wire") {
    let envelope: ReturnType<typeof readWireEnvelope>;
    try {
      envelope = readWireEnvelope(parsed, {
        ...(frame.id === undefined ? {} : { transportId: frame.id }),
        ...(frame.event === undefined ? {} : { transportEvent: frame.event }),
      });
    } catch (error) {
      throw protocolFailure(error);
    }
    return {
      type: WIRE_TO_UI_EVENT_TYPE[envelope.eventType],
      data: envelope.payload,
      ...(envelope.sequence === undefined ? {} : { seq: envelope.sequence }),
      ...(envelope.occurredAt === undefined ? {} : { at: envelope.occurredAt }),
      ...(envelope.runId === undefined ? {} : { run_id: envelope.runId }),
      ...(envelope.eventId === undefined ? {} : { event_id: envelope.eventId }),
      ...(envelope.sessionId === undefined ? {} : { session_id: envelope.sessionId }),
    };
  }

  if (kind !== "application" || !isRecord(parsed) || typeof parsed.type !== "string" || !isRecord(parsed.data)) {
    throw new ChatStreamProtocolError("invalid_legacy_event");
  }
  if (frame.event !== undefined && frame.event !== parsed.type) {
    throw new ChatStreamProtocolError("event_name_mismatch");
  }
  return {
    type: parsed.type,
    data: parsed.data,
    ...(typeof parsed.at === "string" ? { at: parsed.at } : {}),
    ...(typeof parsed.seq === "number" ? { seq: parsed.seq } : {}),
    ...(typeof parsed.created_at === "string" ? { created_at: parsed.created_at } : {}),
  };
}

function apiErrorFromStreamEvent(event: ChatStreamEvent): ApiRequestError {
  const data = event.data;
  return new ApiRequestError(errorFromPayload(data, typeof data.status === "number" ? data.status : 500, "Chat stream failed"));
}

function normalizeCurrentUser(raw: unknown): CurrentUserResponse {
  if (!isRecord(raw)) throw new TypeError("Invalid /me response: expected an object");
  if (typeof raw.active !== "boolean") throw new TypeError("Invalid /me response: active must be a boolean");

  const wire = raw as unknown as CurrentUserWireResponse;
  const company = wire.company ? {
    id: wire.company.id,
    ...(wire.company.name !== undefined ? { name: wire.company.name } : {}),
    ...(wire.company.username !== undefined ? { username: wire.company.username } : {}),
    ...(wire.company.contact_email !== undefined ? { contact_email: wire.company.contact_email } : {}),
  } : null;
  const memberships = wire.employee?.memberships?.map((membership) => ({
    company_id: membership.company_id,
    ...(membership.company_name !== undefined ? { company_name: membership.company_name } : {}),
    ...(membership.role !== undefined ? { role: membership.role } : {}),
  }));
  const employee = wire.employee ? {
    id: wire.employee.id,
    ...(wire.employee.display_name !== undefined ? { display_name: wire.employee.display_name } : {}),
    ...(wire.employee.email !== undefined ? { email: wire.employee.email } : {}),
    ...(wire.employee.contact_email !== undefined ? { contact_email: wire.employee.contact_email } : {}),
    ...(wire.employee.status !== undefined ? { status: wire.employee.status } : {}),
    ...(memberships !== undefined ? { memberships } : {}),
  } : null;
  const userMemberships = wire.memberships?.map((membership) => ({
    company_id: membership.company_id,
    ...(membership.company_name !== undefined ? { company_name: membership.company_name } : {}),
    ...(membership.role !== undefined ? { role: membership.role } : {}),
  })) ?? memberships;
  const email = wire.email ?? employee?.email ?? employee?.contact_email ?? company?.contact_email ?? undefined;
  const principalId = wire.company_id ?? (company ? company.id : wire.employee_id ?? employee?.id);
  const id = wire.id ?? principalId ?? wire.sub ?? wire.subject;
  if (typeof id !== "string") throw new TypeError("Invalid /me response: missing identity");
  const subject = wire.subject ?? wire.sub ?? id;
  const displayName = wire.display_name ?? employee?.display_name ?? company?.name ?? email ?? subject;
  const companyId = wire.company_id ?? company?.id;
  const employeeId = wire.employee_id ?? employee?.id;

  return {
    id,
    subject,
    ...(email !== undefined ? { email } : {}),
    display_name: displayName,
    roles: [...wire.roles],
    active: wire.active,
    ...(companyId ? { company_id: companyId } : {}),
    ...(employeeId ? { employee_id: employeeId } : {}),
    sub: subject,
    contours: [...wire.contours],
    company,
    employee,
    ...(userMemberships !== undefined ? { memberships: userMemberships } : {}),
    work_context: {
      ...(wire.work_context.cabinet_id !== undefined ? { cabinet_id: wire.work_context.cabinet_id } : {}),
      ...(wire.work_context.project_id !== undefined ? { project_id: wire.work_context.project_id } : {}),
    },
  };
}

function normalizedItems<T>(value: unknown): T[] {
  return isRecord(value) && Array.isArray(value.items) ? value.items as T[] : [];
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function readJson(response: Response): Promise<unknown> {
    try { return await response.json(); } catch { return undefined; }
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(isJsonBody(init.body) ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (cause) {
      throw cause;
    }
    if (!response.ok) throw new ApiRequestError(errorFromPayload(await readJson(response), response.status, response.statusText));
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  async function binaryRequest(path: string, init: RequestInit = {}): Promise<Response> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: { Accept: "application/octet-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
      });
    } catch (cause) {
      throw cause;
    }
    if (!response.ok) throw new ApiRequestError(errorFromPayload(await readJson(response), response.status, response.statusText));
    return response;
  }

  async function downloadedFile(path: string, fallbackFilename = "download"): Promise<DownloadedFile> {
    const response = await binaryRequest(path);
    const blob = await response.blob();
    return {
      blob,
      filename: filenameFromContentDisposition(response.headers.get("Content-Disposition"), fallbackFilename),
      contentType: response.headers.get("Content-Type") ?? "application/octet-stream",
      size: blob.size,
    };
  }

  async function* runChat(requestBody: ChatRunRequest, streamOptions: ChatRunReconnectOptions = {}): AsyncIterable<ChatStreamEvent> {
    const token = options.getAccessToken?.();
    const lastEventId = streamOptions.lastEventId;
    const body = {
      ...(requestBody.session_id ? { session_id: requestBody.session_id } : {}),
      text: requestBody.text,
      attachment_refs: requestBody.attachment_refs,
      ...(requestBody.model ? { model: requestBody.model } : {}),
      ...(requestBody.turn_id ? { turn_id: requestBody.turn_id } : {}),
    };
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${projectPath(requestBody.project_id)}/chat/stream`, {
        method: "POST",
        signal: streamOptions.signal,
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          // Only a reconnect carries a cursor; the initial POST never claims one.
          ...(lastEventId === undefined || lastEventId === "" ? {} : { "Last-Event-ID": lastEventId }),
        },
        body: JSON.stringify(body),
      });
    } catch (cause) {
      if (streamOptions.signal?.aborted) throw cause;
      throw new ChatStreamNetworkError(cause);
    }
    if (!response.ok) throw new ApiRequestError(errorFromPayload(await readJson(response), response.status, response.statusText));
    if (!response.body) throw new ChatStreamProtocolError("missing_stream_body");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    const cursor = new SseCursor(seedFromLastEventId(lastEventId));
    let closeReader = true;
    try {
      let done = false;
      while (!done) {
        let chunk: ReadableStreamReadResult<Uint8Array>;
        try { chunk = await reader.read(); } catch (cause) {
          if (streamOptions.signal?.aborted) throw cause;
          throw new ChatStreamNetworkError(cause);
        }
        done = chunk.done;
        const text = chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
        const frames = chunk.done ? [...parser.push(text).frames, ...parser.end().frames] : parser.push(text).frames;
        for (const frame of frames) {
          const event = parseStreamEvent(frame);
          const identity = streamIdentity({
            ...(frame.id !== undefined && frame.id !== "" ? { eventId: frame.id } : {}),
            ...(typeof event.run_id === "string" ? { runId: event.run_id } : {}),
            ...(typeof event.seq === "number" ? { sequence: event.seq } : {}),
          });
          const verdict = cursor.inspect(identity);
          if (verdict.kind === "conflict") throw new ChatStreamProtocolError("stream_cursor_conflict");
          // Replayed frames were already delivered and committed once.
          if (verdict.kind === "duplicate" || verdict.kind === "stale") continue;
          // Both legacy error shapes are terminal for this attempt. A bare
          // `error` without run/sequence stays non-replayable: the cursor is
          // not advanced for it.
          if (event.type === "_error" || event.type === "error") throw apiErrorFromStreamEvent(event);
          if (streamOptions.onEvent) await streamOptions.onEvent(event);
          yield event;
          // Advance the cursor only after the caller accepted the event.
          cursor.commit(identity);
        }
      }
      closeReader = false;
    } finally {
      if (closeReader) {
        try { await reader.cancel(); } catch { /* preserve the original error */ }
      }
      reader.releaseLock();
    }
  }

  const workspaceQuery = (path: string) => `?path=${encodeURIComponent(path)}`;
  const moduleDataQuery = (query: ModuleDataQuery = {}) => {
    const params = new URLSearchParams();
    if (query.page !== undefined) params.set("page", String(query.page));
    if (query.page_size !== undefined) params.set("page_size", String(query.page_size));
    if (query.search !== undefined) params.set("search", query.search);
    if (query.in_stock_only !== undefined) params.set("in_stock_only", String(query.in_stock_only));
    if (query.chats !== undefined) params.set("chats", query.chats);
    return params.size ? `?${params.toString()}` : "";
  };
  const moduleSessionHeaders = (sessionId?: Id): HeadersInit => sessionId ? { "X-Prodavan-Session-Id": sessionId } : {};
  const moduleDataBody = (body: JsonObject) => JSON.stringify({ body });
  const multipartUpload = (path: string, file: Blob, filename = "upload.bin") => {
    const form = new FormData();
    form.append("file", file, filename);
    return request<UploadedModuleFile>(path, { method: "POST", body: form });
  };
  const projectLifecyclePaths: Record<string, string> = {
    launch: "launch", pause: "pause", resume: "resume", complete: "complete", reload: "reload", sync: "sync",
    materialize: "materialize", "dismiss-workspace-outdated": "dismiss-workspace-outdated", "reset-agent": "agent/reset",
  };

  const client: ApiClient = {
    authConfig: () => request<AuthConfig>("/auth/config"),
    login: (username, password) => request<SessionTokens>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
    refresh: (refreshToken) => request<SessionTokens>("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }),
    logout: (tokens) => request<void>("/auth/logout", { method: "POST", body: JSON.stringify(tokens) }),
    me: async () => normalizeCurrentUser(await request<unknown>("/me")),

    listCabinets: async () => normalizedItems<Cabinet>(await request("/cabinets")),
    getCabinet: (cabinetId) => request<Cabinet>(`/cabinets/${encodePath(cabinetId)}`),
    createCabinet: (body) => request<Cabinet>("/cabinets", { method: "POST", body: JSON.stringify(body) }),
    renameCabinet: (cabinetId, name) => request<Cabinet>(`/cabinets/${encodePath(cabinetId)}`, { method: "PATCH", body: JSON.stringify({ name }) }),
    archiveCabinet: (cabinetId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/archive`, { method: "POST" }),
    deleteCabinet: (cabinetId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}`, { method: "DELETE" }),
    restoreCabinet: (cabinetId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/restore`, { method: "POST" }),
    purgeCabinet: (cabinetId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/purge`, { method: "DELETE" }),
    uploadCabinetContent: (cabinetId, file, filename = "upload.bin") => {
      const form = new FormData();
      form.append("file", file, filename);
      return request<UploadedModuleFile>(`/cabinets/${encodePath(cabinetId)}/content/upload`, { method: "POST", body: form });
    },
    getSelection: (cabinetId) => request<ProjectSelection>(`/cabinets/${encodePath(cabinetId)}/me/selection`),
    setSelection: (cabinetId, selection) => request<ProjectSelection>(`/cabinets/${encodePath(cabinetId)}/me/selection`, { method: "PUT", body: JSON.stringify(selection) }),
    chatSidebar: (cabinetId) => request<ChatSidebarResponse>(`/cabinets/${encodePath(cabinetId)}/chats/sidebar`),

    listProjects: (cabinetId) => request<ProjectsResponse>(`/cabinets/${encodePath(cabinetId)}/projects`),
    createProject: (cabinetId, body) => request<ProjectSummary>(`/cabinets/${encodePath(cabinetId)}/projects`, { method: "POST", body: JSON.stringify(body) }),
    getProject: (projectId) => request<ProjectSummary>(`${projectPath(projectId)}`),
    patchProject: (projectId, patch) => request<ProjectSummary>(projectPath(projectId), { method: "PATCH", body: JSON.stringify(patch) }),
    updateProject: (projectId, patch) => request<ProjectSummary>(projectPath(projectId), { method: "PATCH", body: JSON.stringify(patch) }),
    deleteProject: (projectId) => request<JsonObject>(projectPath(projectId), { method: "DELETE" }),
    restoreProject: (projectId) => request<JsonObject>(`${projectPath(projectId)}/restore`, { method: "POST" }),
    purgeProject: (projectId) => request<JsonObject>(`${projectPath(projectId)}/purge`, { method: "DELETE" }),
    projectLifecycle: (projectId, action) => request<JsonObject>(`${projectPath(projectId)}/${projectLifecyclePaths[action]}`, { method: "POST" }),
    launchProject: (projectId) => client.projectLifecycle(projectId, "launch"),
    pauseProject: (projectId) => client.projectLifecycle(projectId, "pause"),
    resumeProject: (projectId) => client.projectLifecycle(projectId, "resume"),
    completeProject: (projectId) => client.projectLifecycle(projectId, "complete"),
    reloadProject: (projectId) => client.projectLifecycle(projectId, "reload"),
    syncProject: (projectId) => client.projectLifecycle(projectId, "sync"),
    materializeProject: (projectId) => client.projectLifecycle(projectId, "materialize"),
    dismissWorkspaceOutdated: (projectId) => client.projectLifecycle(projectId, "dismiss-workspace-outdated"),
    resetProjectAgent: (projectId) => client.projectLifecycle(projectId, "reset-agent"),
    lifecycle: async (projectId, action) => { await client.projectLifecycle(projectId, action); },
    containerStatus: async (projectId) => {
      const raw = await request<JsonObject>(`${projectPath(projectId)}/container`);
      const status = typeof raw.status === "string" ? raw.status : typeof raw.observed_state === "string" ? raw.observed_state : "unknown";
      return { ...raw, project_id: projectId, status } as ContainerStatusResponse;
    },
    projectMetrics: (projectId) => request<JsonObject>(`${projectPath(projectId)}/metrics`),
    containerMetrics: (projectId, window = "1h") => request<JsonObject>(`${projectPath(projectId)}/container/metrics?window=${encodeURIComponent(window)}`),
    listWorkspaceEntries: (projectId, path = "") => request<WorkspaceListing>(`${projectPath(projectId)}/container/workspace/entries${workspaceQuery(path)}`),
    previewWorkspace: (projectId, path) => request<WorkspacePreview>(`${projectPath(projectId)}/container/workspace/preview${workspaceQuery(path)}`),
    uploadWorkspace: (projectId, path, body) => request<WorkspaceMutationResponse>(`${projectPath(projectId)}/container/workspace/content${workspaceQuery(path)}`, { method: "PUT", body, headers: { "Content-Type": "application/octet-stream" } }),
    downloadWorkspace: (projectId, path) => downloadedFile(`${projectPath(projectId)}/container/workspace/content${workspaceQuery(path)}`),
    deleteWorkspaceEntry: (projectId, path) => request<WorkspaceMutationResponse>(`${projectPath(projectId)}/container/workspace/entries${workspaceQuery(path)}`, { method: "DELETE" }),
    moveWorkspaceEntry: (projectId, src, dst) => request<WorkspaceMutationResponse>(`${projectPath(projectId)}/container/workspace/move`, { method: "POST", body: JSON.stringify({ src, dst }) }),
    copyWorkspaceEntry: (projectId, src, dst) => request<WorkspaceMutationResponse>(`${projectPath(projectId)}/container/workspace/copy`, { method: "POST", body: JSON.stringify({ src, dst }) }),
    setProjectVisibility: (projectId, visibility_mode) => request<JsonObject>(`${projectPath(projectId)}/visibility`, { method: "PUT", body: JSON.stringify({ visibility_mode }) }),
    listProjectModules: (projectId) => request<ProjectModulesSettingsResponse>(`${projectPath(projectId)}/modules`),
    getProjectModule: (projectId, moduleId) => request<ProjectModuleSettings>(`${projectPath(projectId)}/modules/${encodePath(moduleId)}`),
    setProjectModuleProfile: (projectId, moduleId, profile_id) => request<ProjectModuleSettings>(`${projectPath(projectId)}/modules/${encodePath(moduleId)}/profile`, { method: "PATCH", body: JSON.stringify({ profile_id }) }),
    setProjectModules: (projectId, module_ids) => request<JsonObject>(`${projectPath(projectId)}/modules`, { method: "PATCH", body: JSON.stringify({ module_ids }) }),
    listProjectWorkspaceEntries: (projectId, path = "") => client.listWorkspaceEntries(projectId, path),
    previewProjectWorkspace: (projectId, path) => client.previewWorkspace(projectId, path),
    downloadProjectWorkspace: (projectId, path) => client.downloadWorkspace(projectId, path),

    listSessions: async (projectId) => ({ items: normalizedItems<AgentSession>(await request(`${projectPath(projectId)}/agent/sessions`)) }),
    createSession: (projectId, body = {}) => request<AgentSession>(`${projectPath(projectId)}/agent/sessions`, { method: "POST", body: JSON.stringify(body) }),
    getSession: (projectId, sessionId) => request<AgentSession>(sessionPath(projectId, sessionId)),
    patchSession: (projectId, sessionId, patch) => request<AgentSession>(sessionPath(projectId, sessionId), { method: "PATCH", body: JSON.stringify(patch) }),
    deleteSession: (projectId, sessionId) => request<JsonObject>(sessionPath(projectId, sessionId), { method: "DELETE" }),
    forkSession: (projectId, sessionId) => request<AgentSession>(`${sessionPath(projectId, sessionId)}/fork`, { method: "POST" }),
    cancelSession: (projectId, sessionId) => request<AgentSession>(`${sessionPath(projectId, sessionId)}/cancel`, { method: "POST" }),
    sendMessage: (projectId, sessionId, body) => request<ChatSendResponse>(`${sessionPath(projectId, sessionId)}/send`, { method: "POST", body: JSON.stringify(body) }),
    getTranscript: (projectId, sessionId, query = {}) => {
      const params = new URLSearchParams({ session_id: sessionId });
      if (query.beforeSeq !== undefined) params.set("before_seq", String(query.beforeSeq));
      if (query.afterSeq !== undefined) params.set("after_seq", String(query.afterSeq));
      if (query.limit !== undefined) params.set("limit", String(query.limit));
      return request<ChatTranscriptResponse>(`${projectPath(projectId)}/chat/transcript?${params.toString()}`);
    },
    chatTranscript: (projectId, sessionId, query = {}) => client.getTranscript(projectId, sessionId, query),
    runChat,
    getSessionTranscript: (projectId, sessionId) => request<ChatTranscriptResponse>(`${sessionPath(projectId, sessionId)}/transcript`),
    listPendingApprovals: async (projectId, sessionId) => ({ items: normalizedItems<ToolApproval>(await request(`${sessionPath(projectId, sessionId)}/pending-approvals`)) }),
    resolveApproval: (projectId, sessionId, id, decision) => request<ToolApprovalDecisionResponse>(`${sessionPath(projectId, sessionId)}/tool-approvals`, { method: "POST", body: JSON.stringify({ id, decision }) }),
    getDraft: (projectId, sessionId) => request<ComposerDraft>(sessionId ? `${sessionPath(projectId, sessionId)}/composer-draft` : `${projectPath(projectId)}/composer-draft`),
    saveDraft: (projectId, text, sessionId) => request<ComposerDraft>(sessionId ? `${sessionPath(projectId, sessionId)}/composer-draft` : `${projectPath(projectId)}/composer-draft`, { method: "PUT", body: JSON.stringify({ text }) }),
    clearDraft: (projectId, sessionId) => request<JsonObject>(sessionId ? `${sessionPath(projectId, sessionId)}/composer-draft` : `${projectPath(projectId)}/composer-draft`, { method: "DELETE" }),
    listSessionEvents: async (projectId, sessionId) => ({ items: normalizedItems<ChatStreamEvent>(await request(`${sessionPath(projectId, sessionId)}/events`)) }),
    appendSessionEvent: (projectId, sessionId, event) => request<ChatStreamEvent>(`${sessionPath(projectId, sessionId)}/events`, { method: "POST", body: JSON.stringify(event) }),

    listAttachments: async (projectId) => ({ items: normalizedItems<ChatAttachment>(await request(`${projectPath(projectId)}/attachments`)) }),
    uploadAttachment: (projectId, upload: AttachmentUpload) => request<ChatAttachment>(`${projectPath(projectId)}/attachments`, { method: "POST", body: JSON.stringify({ filename: upload.filename, content_base64: upload.contentBase64, ...(upload.contentType ? { content_type: upload.contentType } : {}) }) }),
    deleteAttachment: (projectId, attachmentId) => request<JsonObject>(`${projectPath(projectId)}/attachments/${encodePath(attachmentId)}`, { method: "DELETE" }),
    downloadAttachment: (projectId, attachmentId) => downloadedFile(`${projectPath(projectId)}/attachments/${encodePath(attachmentId)}/content`),
    listProjectAttachments: (projectId) => client.listAttachments(projectId),
    uploadProjectAttachment: (projectId, upload) => client.uploadAttachment(projectId, upload),
    downloadProjectAttachment: (projectId, attachmentId) => client.downloadAttachment(projectId, attachmentId),

    enqueueTrigger: (projectId, body) => request<ProjectTrigger>(`${projectPath(projectId)}/triggers`, { method: "POST", body: JSON.stringify(body) }),
    enqueueScheduleTrigger: (projectId, payload = {}) => client.enqueueTrigger(projectId, { kind: "system.schedule", payload }),
    postTrigger: (projectId, body) => client.enqueueTrigger(projectId, body),
    listTriggers: async (projectId) => ({ items: normalizedItems<ProjectTrigger>(await request(`${projectPath(projectId)}/triggers`)) }),
    dispatchTriggers: (projectId, max = 1) => request<JsonObject>(`${projectPath(projectId)}/triggers/dispatch?max=${encodeURIComponent(String(max))}`, { method: "POST" }),
    getChatErrorPolicy: (projectId) => request<ChatErrorPolicy>(`${projectPath(projectId)}/chat-error-policy`),
    setChatErrorPolicy: (projectId, patch) => request<ChatErrorPolicy>(`${projectPath(projectId)}/chat-error-policy`, { method: "PUT", body: JSON.stringify(patch) }),
    listProjectModels: (projectId) => request<ProjectModelsLiveResponse>(`${projectPath(projectId)}/models/live`),
    listProjectModelsLive: (projectId) => client.listProjectModels(projectId),
    listProjectAiKeys: async (projectId) => ({ items: normalizedItems<JsonObject>(await request(`${projectPath(projectId)}/ai-keys/available`)) }),
    listCompanyModels: (companyId) => request<ModelCatalogEntry[]>(`/companies/${encodePath(companyId)}/ai-models`),
    listCompanyKeys: (companyId) => request<JsonObject[]>(`/companies/${encodePath(companyId)}/ai-keys`),
    listKeyModels: (companyId, keyId) => request<JsonObject[]>(`/companies/${encodePath(companyId)}/ai-keys/${encodePath(keyId)}/models`),

    listProjectSchedules: (projectId) => request<{ items: ProjectSchedule[] }>(`${projectPath(projectId)}/schedules`),
    createProjectSchedule: (projectId, body) => request<ProjectSchedule>(`${projectPath(projectId)}/schedules`, { method: "POST", body: JSON.stringify(body) }),
    patchProjectSchedule: (projectId, scheduleId, patch) => request<ProjectSchedule>(`${projectPath(projectId)}/schedules/${encodePath(scheduleId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    deleteProjectSchedule: (projectId, scheduleId) => request<void>(`${projectPath(projectId)}/schedules/${encodePath(scheduleId)}`, { method: "DELETE" }),

    listOutgoingWebhooks: (projectId) => request<{ items: OutgoingWebhook[] }>(`/external/webhooks${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
    createOutgoingWebhook: (body) => request<CreatedOutgoingWebhook>("/external/webhooks", { method: "POST", body: JSON.stringify(body) }),
    setOutgoingWebhookEnabled: (subscriptionId, enabled) => request<Pick<OutgoingWebhook, "id" | "enabled">>(`/external/webhooks/${encodePath(subscriptionId)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
    deleteOutgoingWebhook: (subscriptionId) => request<JsonObject>(`/external/webhooks/${encodePath(subscriptionId)}`, { method: "DELETE" }),
    listWebhookDeliveries: (limit = 100) => request<{ items: WebhookDelivery[] }>(`/external/webhook-deliveries?limit=${encodeURIComponent(String(limit))}`),
    retryWebhookDelivery: (deliveryId) => request<JsonObject>(`/external/webhook-deliveries/${encodePath(deliveryId)}/retry`, { method: "POST" }),

    listProjectRuntimeModules: (projectId) => request<{ items: ProjectRuntimeModule[] }>(`${projectPath(projectId)}/runtime-modules`),
    getProjectRuntimeModuleMeta: (projectId, moduleId, slug) => request<ModuleMetaDocument>(`${projectPath(projectId)}/runtime-modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`),
    listProjectRuntimeModuleData: (projectId, moduleId, tableSlug, query = {}, sessionId) => request<ModuleDataRows>(`${projectPath(projectId)}/runtime-modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}${moduleDataQuery(query)}`, { headers: moduleSessionHeaders(sessionId) }),
    createProjectRuntimeModuleData: (projectId, moduleId, tableSlug, body, sessionId) => request<JsonObject>(`${projectPath(projectId)}/runtime-modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}`, { method: "POST", body: moduleDataBody(body), headers: moduleSessionHeaders(sessionId) }),
    patchProjectRuntimeModuleData: (projectId, moduleId, tableSlug, rowId, body, sessionId) => request<JsonObject>(`${projectPath(projectId)}/runtime-modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "PATCH", body: moduleDataBody(body), headers: moduleSessionHeaders(sessionId) }),
    deleteProjectRuntimeModuleData: (projectId, moduleId, tableSlug, rowId) => request<JsonObject>(`${projectPath(projectId)}/runtime-modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "DELETE" }),
    bindProjectModule: (projectId, moduleId, body = {}) => request<ModuleBinding>(`${projectPath(projectId)}/modules/${encodePath(moduleId)}/bind`, { method: "POST", body: JSON.stringify(body) }),
    unbindProjectModule: (projectId, moduleId) => request<JsonObject>(`${projectPath(projectId)}/modules/${encodePath(moduleId)}`, { method: "DELETE" }),
    invokeProjectModuleAction: (cabinetId, projectId, moduleId, actionId, body = {}, sessionId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/actions/${encodePath(actionId)}/invoke`, { method: "POST", body: JSON.stringify({ project_id: projectId, ...(body.row_id ? { row_id: body.row_id } : {}), ...(body.params ? { params: body.params } : {}) }), headers: moduleSessionHeaders(sessionId) }),
    uploadProjectModuleContent: (cabinetId, file, filename = "upload.bin") => multipartUpload(`/cabinets/${encodePath(cabinetId)}/content/upload`, file, filename),
    downloadProjectModuleFile: (fileRef) => {
      const params = new URLSearchParams({ proxy: "1" });
      const versionId = fileRef.blob_version_id ?? fileRef.version_id;
      if (typeof versionId === "string" && versionId) params.set("blob_version_id", versionId);
      return downloadedFile(`/content/assets/${encodePath(fileRef.asset_id)}/download?${params.toString()}`, fileRef.filename || "download");
    },
    listCabinetModules: (cabinetId) => request<{ items: CabinetBoundModule[] }>(`/cabinets/${encodePath(cabinetId)}/modules`),
    listCabinetModuleBoundProjects: (cabinetId, moduleId) => request<{ items: Array<{ project_id: Id; name: string }> }>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/bound-projects`),
    getCabinetModuleMeta: (cabinetId, moduleId, slug) => request<ModuleMetaDocument>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`),
    listCabinetModuleData: (cabinetId, moduleId, tableSlug, query = {}, sessionId) => request<ModuleDataRows>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}${moduleDataQuery(query)}`, { headers: moduleSessionHeaders(sessionId) }),
    createCabinetModuleData: (cabinetId, moduleId, tableSlug, body, sessionId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}`, { method: "POST", body: moduleDataBody(body), headers: moduleSessionHeaders(sessionId) }),
    patchCabinetModuleData: (cabinetId, moduleId, tableSlug, rowId, body, sessionId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "PATCH", body: moduleDataBody(body), headers: moduleSessionHeaders(sessionId) }),
    deleteCabinetModuleData: (cabinetId, moduleId, tableSlug, rowId, sessionId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "DELETE", headers: moduleSessionHeaders(sessionId) }),
    invokeCabinetModuleAction: (cabinetId, moduleId, actionId, body, sessionId) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/actions/${encodePath(actionId)}/invoke`, { method: "POST", body: JSON.stringify({ ...(body.row_id ? { row_id: body.row_id } : {}), ...(body.project_id ? { project_id: body.project_id } : {}), ...(body.params ? { params: body.params } : {}) }), headers: moduleSessionHeaders(sessionId) }),
    uploadCabinetModuleSecret: (cabinetId, moduleId, body) => request<JsonObject>(`/cabinets/${encodePath(cabinetId)}/modules/${encodePath(moduleId)}/secrets/upload`, { method: "POST", body: JSON.stringify(body) }),

    listCompanyModules: (companyId) => request<{ items: CompanyModule[] }>(`/companies/${encodePath(companyId)}/modules`),
    createCompanyModule: (companyId, name) => request<CompanyModule>(`/companies/${encodePath(companyId)}/modules`, { method: "POST", body: JSON.stringify({ name }) }),
    getCompanyModule: (companyId, moduleId) => request<CompanyModule>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}`),
    patchCompanyModule: (companyId, moduleId, patch) => request<CompanyModule>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    deleteCompanyModule: (companyId, moduleId) => request<JsonObject>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}`, { method: "DELETE" }),
    copyCompanyModule: (companyId, moduleId, name) => request<CompanyModule>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/copy`, { method: "POST", body: JSON.stringify(name ? { name } : {}) }),
    listCompanyModuleProjects: (companyId, moduleId) => request<{ items: ModuleBinding[] }>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/projects`),
    listCompanyModuleCabinets: (companyId, moduleId) => request<{ items: JsonObject[] }>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/cabinets`),
    listCompanyModuleMeta: (companyId, moduleId) => request<{ items: ModuleMetaDocument[] }>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/meta/documents`),
    getCompanyModuleMeta: (companyId, moduleId, slug) => request<ModuleMetaDocument>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`),
    putCompanyModuleMeta: (companyId, moduleId, slug, body) => request<ModuleMetaDocument>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`, { method: "PUT", body: JSON.stringify({ body }) }),
    listCompanyModuleData: (companyId, moduleId, tableSlug) => request<ModuleDataRows>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}`),
    createCompanyModuleData: (companyId, moduleId, tableSlug, body) => request<JsonObject>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}`, { method: "POST", body: moduleDataBody(body) }),
    patchCompanyModuleData: (companyId, moduleId, tableSlug, rowId, body) => request<JsonObject>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "PATCH", body: moduleDataBody(body) }),
    deleteCompanyModuleData: (companyId, moduleId, tableSlug, rowId) => request<JsonObject>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "DELETE" }),
    invokeCompanyModuleAction: (companyId, moduleId, actionId, body) => request<JsonObject>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/actions/${encodePath(actionId)}/invoke`, { method: "POST", body: JSON.stringify({ ...(body.row_id ? { row_id: body.row_id } : {}), ...(body.params ? { params: body.params } : {}) }) }),
    uploadCompanyModuleContent: (companyId, moduleId, file, filename = "upload.bin") => multipartUpload(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/content/upload`, file, filename),
    uploadCompanyModuleSecret: (companyId, moduleId, body) => request<JsonObject>(`/companies/${encodePath(companyId)}/modules/${encodePath(moduleId)}/secrets/upload`, { method: "POST", body: JSON.stringify(body) }),

    listAdminModules: () => request<{ items: AdminModule[] }>("/admin/modules"),
    // Plugin-платформа (дизайн v4):PluginInfoPort читает установленные плагины
    // компании, чтобы решить, рендерить iframe или показать «не подключён».
    // Токен идёт в заголовке (request добавляет Authorization), в query — только
    // company_id; capability-токен плагина в URL не попадает никогда (§5.2).
    listAdminPlugins: (companyId) => request<AdminPluginsResponse>(`/admin/plugins/?company_id=${encodeURIComponent(companyId)}`),
    createAdminModule: (name) => request<AdminModule>("/admin/modules", { method: "POST", body: JSON.stringify({ name }) }),
    getAdminModule: (moduleId) => request<AdminModule>(`/admin/modules/${encodePath(moduleId)}`),
    patchAdminModule: (moduleId, patch) => request<AdminModule>(`/admin/modules/${encodePath(moduleId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    deleteAdminModule: (moduleId) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}`, { method: "DELETE" }),
    copyAdminModule: (moduleId, name) => request<AdminModule>(`/admin/modules/${encodePath(moduleId)}/copy`, { method: "POST", body: JSON.stringify(name ? { name } : {}) }),
    listAdminModuleMeta: (moduleId) => request<{ items: ModuleMetaDocument[] }>(`/admin/modules/${encodePath(moduleId)}/meta/documents`),
    getAdminModuleMeta: (moduleId, slug) => request<ModuleMetaDocument>(`/admin/modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`),
    putAdminModuleMeta: (moduleId, slug, body) => request<ModuleMetaDocument>(`/admin/modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`, { method: "PUT", body: JSON.stringify({ body }) }),
    deleteAdminModuleMeta: (moduleId, slug) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/meta/documents/${encodePath(slug)}`, { method: "DELETE" }),
    listAdminModuleProjects: (moduleId) => request<{ items: ModuleBinding[] }>(`/admin/modules/${encodePath(moduleId)}/projects`),
    listAdminModuleCabinets: (moduleId) => request<{ items: JsonObject[] }>(`/admin/modules/${encodePath(moduleId)}/cabinets`),
    bindAdminProjectModule: (moduleId, projectId, body = {}) => request<ModuleBinding>(`/admin/modules/${encodePath(moduleId)}/projects/${encodePath(projectId)}`, { method: "POST", body: JSON.stringify(body) }),
    unbindAdminProjectModule: (moduleId, projectId) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/projects/${encodePath(projectId)}`, { method: "DELETE" }),
    listAdminModuleData: (moduleId, tableSlug) => request<ModuleDataRows>(`/admin/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}`),
    createAdminModuleData: (moduleId, tableSlug, body) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}`, { method: "POST", body: moduleDataBody(body) }),
    patchAdminModuleData: (moduleId, tableSlug, rowId, body) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "PATCH", body: moduleDataBody(body) }),
    deleteAdminModuleData: (moduleId, tableSlug, rowId) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/data/${encodePath(tableSlug)}/${encodePath(rowId)}`, { method: "DELETE" }),
    invokeAdminModuleAction: (moduleId, actionId, body) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/actions/${encodePath(actionId)}/invoke`, { method: "POST", body: JSON.stringify({ ...(body.row_id ? { row_id: body.row_id } : {}), ...(body.params ? { params: body.params } : {}) }) }),
    uploadAdminModuleContent: (moduleId, file, filename = "upload.bin") => multipartUpload(`/admin/modules/${encodePath(moduleId)}/content/upload`, file, filename),
    uploadAdminModuleSecret: (moduleId, body) => request<JsonObject>(`/admin/modules/${encodePath(moduleId)}/secrets/upload`, { method: "POST", body: JSON.stringify(body) }),

    getAdminProfile: () => request<AdminProfile>("/admin/profile"),
    setAdminLogin: (login) => request<AdminProfile>("/admin/profile/login", { method: "PUT", body: JSON.stringify({ login }) }),
    setAdminPassword: (password) => request<JsonObject>("/admin/profile/password", { method: "PUT", body: JSON.stringify({ password }) }),

    listContentAssets: (companyId, limit = 50) => request<{ items: ContentAsset[] }>(`/content/assets?company_id=${encodeURIComponent(companyId)}&limit=${encodeURIComponent(String(limit))}`),
    createContentAsset: (body) => request<ContentAsset>("/content/assets", { method: "POST", body: JSON.stringify(body) }),
    getContentAsset: (assetId) => request<ContentAsset>(`/content/assets/${encodePath(assetId)}`),
    patchContentAsset: (assetId, patch) => request<ContentAsset>(`/content/assets/${encodePath(assetId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    deleteContentAsset: (assetId) => request<JsonObject>(`/content/assets/${encodePath(assetId)}`, { method: "DELETE" }),
    beginContentAssetVersion: (assetId, body = {}) => request<ContentAssetVersionUpload>(`/content/assets/${encodePath(assetId)}/versions`, { method: "POST", body: JSON.stringify(body) }),
    uploadContentAssetVersion: (assetId, versionId, file, contentType = "application/octet-stream") => request<ContentAssetVersion>(`/content/assets/${encodePath(assetId)}/versions/${encodePath(versionId)}/upload`, { method: "PUT", body: file, headers: { "Content-Type": contentType } }),
    finalizeContentAssetVersion: (assetId, versionId) => request<JsonObject>(`/content/assets/${encodePath(assetId)}/versions/${encodePath(versionId)}/finalize`, { method: "POST" }),
    downloadContentAsset: (assetId, options = { proxy: true }) => {
      const params = new URLSearchParams();
      if (options.blob_version_id) params.set("blob_version_id", options.blob_version_id);
      if (options.proxy !== false) params.set("proxy", "1");
      const query = params.size ? `?${params.toString()}` : "";
      return downloadedFile(`/content/assets/${encodePath(assetId)}/download${query}`);
    },
    getProjectChatErrorPolicy: (projectId) => client.getChatErrorPolicy(projectId),
    setProjectChatErrorPolicy: (projectId, patch) => client.setChatErrorPolicy(projectId, patch),
  };
  return client;
}