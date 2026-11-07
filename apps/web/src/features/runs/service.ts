import type { Translate } from "../../i18n/catalogue";
import type { Locale } from "../../preferences/settings";
import { formatDateTime } from "../../i18n/format";
import type { AgentRunCancelled, AgentRunCreated, AgentRunRecord, ApprovalCommandInput, ApprovalDecisionResult, ApprovalInboxItem, CancelRunBody, CancelRunCommandInput, ControlPlaneEvent, CreateRunBody, CursorPage, RunCreateResult, RunEventsOptions, RunPageOptions, RunSession, RunTranscript, RunsService, RunsServiceOptions, SessionApproval, SessionApprovalDecision, WorkspaceFile, WriteOutcome } from "./contracts";

/**
 * What went wrong, as a code rather than a sentence.
 *
 * The service used to describe its own failures in English, which then appeared
 * inside a Russian screen. A code crosses that boundary unharmed: the screen
 * turns it into `runs.err<Code>` in whatever language the reader chose. The
 * backend's own message is not copy and does not get translated — it travels
 * beside the code as `detail`.
 */
export type RunsErrorCode =
  | "network"
  | "unauthorized"
  | "forbidden"
  | "notFound"
  | "invalidRequest"
  | "conflict"
  | "unavailable"
  | "server"
  | "invalidResponse";

export class RunsRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: RunsErrorCode,
    /** The server's own message, passed through untouched: data, not copy. */
    readonly detail?: string,
    /** The machine code the backend sent (`AGENT_RUN_CONFLICT` …), kept for support. */
    readonly serverCode?: string,
  ) {
    super(detail ?? code);
    this.name = "RunsRequestError";
  }
}

/** HTTP status → the code the screen knows how to say. */
function codeForStatus(status: number): RunsErrorCode {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "notFound";
  if (status === 409) return "conflict";
  if (status === 408 || status === 429 || status === 503) return "unavailable";
  if (status >= 500) return "server";
  return "invalidRequest";
}

async function readErrorBody(response: Response): Promise<Record<string, unknown>> {
  try {
    const body = await response.json() as unknown;
    return body && typeof body === "object" ? body as Record<string, unknown> : {};
  } catch { return {}; }
}

/** A failed response as a code plus whatever the server chose to say. */
function responseError(response: Response, body: Record<string, unknown>): RunsRequestError {
  const serverCode = typeof body.code === "string" && body.code.trim() ? body.code : undefined;
  const message = typeof body.detail === "string" && body.detail.trim() ? body.detail
    : typeof body.title === "string" && body.title.trim() ? body.title
    : response.statusText || undefined;
  return new RunsRequestError(response.status, codeForStatus(response.status), message, serverCode);
}

export function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `runs-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function asError(error: unknown): Error { return error instanceof Error ? error : new Error(String(error)); }

export function createRunsService(options: RunsServiceOptions = {}): RunsService {
  const baseUrl = options.baseUrl ?? "/api/v1";
  const fetcher = options.fetcher ?? fetch;

  /** A transport-level failure becomes a code; a deliberate abort stays an abort. */
  const send = async (url: string, init?: RequestInit): Promise<Response> => {
    try {
      return await fetcher(url, init);
    } catch (error) {
      if (init?.signal?.aborted) throw error;
      throw new RunsRequestError(0, "network", error instanceof Error ? error.message : String(error));
    }
  };

  async function requestResponse(path: string, init?: RequestInit): Promise<Response> {
    const token = options.getAccessToken?.();
    const response = await send(`${baseUrl}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) throw responseError(response, await readErrorBody(response));
    return response;
  }

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await requestResponse(path, init);
    if (response.status === 204) return undefined as T;
    try {
      return await response.json() as T;
    } catch {
      throw new RunsRequestError(response.status, "invalidResponse");
    }
  }

  async function cursorPage<T>(path: string, signal?: AbortSignal): Promise<CursorPage<T>> {
    const response = await requestResponse(path, signal ? { signal } : undefined);
    let body: { items?: T[]; has_more?: boolean };
    try {
      body = await response.json() as { items?: T[]; has_more?: boolean };
    } catch {
      throw new RunsRequestError(response.status, "invalidResponse");
    }
    if (!body || !Array.isArray(body.items)) throw new RunsRequestError(response.status, "invalidResponse");
    const header = response.headers.get("X-Has-More");
    return {
      items: body.items,
      hasMore: header === null ? body.has_more === true : header.toLowerCase() === "true",
      nextCursor: response.headers.get("X-Next-Cursor") ?? undefined,
    };
  }

  const queryString = (entries: Record<string, string | number | undefined>) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(entries)) if (value !== undefined) params.set(key, String(value));
    return params.toString();
  };
  const project = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
  const runsPath = (projectId: string) => `${project(projectId)}/agent/runs`;
  const session = (projectId: string, sessionId: string) => `${project(projectId)}/agent/sessions/${encodeURIComponent(sessionId)}`;
  const runPath = (projectId: string, runId: string) => `${runsPath(projectId)}/${encodeURIComponent(runId)}`;
  const findRunByIdempotencyKey = async (projectId: string, idempotencyKey: string): Promise<AgentRunRecord | undefined> => {
    let cursor: string | undefined;
    for (let pageNumber = 0; pageNumber < 100; pageNumber += 1) {
      const query = queryString({ limit: 200, cursor });
      const page = await cursorPage<AgentRunRecord>(`${runsPath(projectId)}?${query}`);
      const match = page.items.find((run) => run.idempotency_key === idempotencyKey);
      if (match) return match;
      if (!page.hasMore || !page.nextCursor) return undefined;
      cursor = page.nextCursor;
    }
    return undefined;
  };
  const isStatus = (error: unknown, status: number) => error instanceof RunsRequestError && error.status === status;
  const isPersistenceBlocked = (error: unknown) => error instanceof RunsRequestError
    && (error.serverCode === "AGENT_RUN_PERSISTENCE_BLOCKED" || error.serverCode === "AGENT_RUN_API_DISABLED" || error.serverCode === "AGENT_RUN_PERSISTENCE_NOT_READY");
  const isCommandEvent = (event: ControlPlaneEvent, commandKey: string) => {
    const controlPlane = event.payload.control_plane;
    return Boolean(controlPlane && typeof controlPlane === "object"
      && (controlPlane as Record<string, unknown>).command_idempotency_key === commandKey);
  };
  const stableJson = (value: unknown): string => {
    if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
    if (value && typeof value === "object") {
      const record = value as Record<string, unknown>;
      return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
    }
    if (value === undefined) return "null";
    return JSON.stringify(value) ?? "null";
  };
  const matchesCreate = (saved: AgentRunRecord, projectId: string, body: CreateRunBody) =>
    saved.project_id === projectId
    && saved.session_id === (body.session_id ?? null)
    && stableJson(saved.input_payload) === stableJson(body.input_payload);
  const readApprovalDecision = async (projectId: string, input: ApprovalCommandInput): Promise<ControlPlaneEvent | undefined> => {
    const page = await cursorPage<ControlPlaneEvent>(`${runPath(projectId, input.item.approval.run_id)}/events?${queryString({ limit: 200, aggregate_type: "approval_request", aggregate_id: input.item.approval.id, after_sequence: input.item.approval_sequence })}`);
    const expectedType = input.decision === "approve" ? "approval_request.approved" : "approval_request.denied";
    return page.items.find((event) => event.aggregate_type === "approval_request"
      && event.aggregate_id === input.item.approval.id
      && event.sequence === input.item.approval_sequence + 1
      && event.event_type === expectedType
      && event.payload.status === (input.decision === "approve" ? "approved" : "denied")
      && isCommandEvent(event, input.idempotencyKey));
  };
  return {
    listRuns: (projectId: string, page: RunPageOptions = {}) => cursorPage<AgentRunRecord>(`${runsPath(projectId)}?${queryString({ limit: page.limit ?? 50, offset: page.cursor ? undefined : page.offset, cursor: page.cursor })}`),
    getRun: (projectId: string, runId: string, signal?: AbortSignal) => request<AgentRunRecord>(runPath(projectId, runId), signal ? { signal } : undefined),
    listRunEvents: (projectId: string, runId: string, query: RunEventsOptions = {}, signal?: AbortSignal) => cursorPage<ControlPlaneEvent>(`${runPath(projectId, runId)}/events?${queryString({ limit: query.limit ?? 100, aggregate_type: query.aggregateType, aggregate_id: query.aggregateId, after_sequence: query.afterSequence, cursor: query.cursor })}`, signal),
    // `requestResponse` already prefixes `baseUrl`; interpolating it again produced
    // `/api/v1/api/v1/agent/approvals/inbox`, which never resolves on the backend.
    listApprovalInbox: (projectId: string, page: Omit<RunPageOptions, "offset"> = {}) => cursorPage<ApprovalInboxItem>(`/agent/approvals/inbox?${queryString({ project_id: projectId, limit: page.limit ?? 50, cursor: page.cursor })}`),
    createRun: (projectId: string, body: CreateRunBody, idempotencyKey: string) => request<AgentRunCreated>(runsPath(projectId), {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(body),
    }),
    async createRunWithReadback(projectId: string, body: CreateRunBody, idempotencyKey: string): Promise<RunCreateResult> {
      let responseRun: AgentRunRecord | undefined;
      let duplicate = false;
      let queueIntentsRecorded = 0;
      let writeError: unknown;
      try {
        const result = await request<AgentRunCreated>(runsPath(projectId), {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: JSON.stringify(body),
        });
        responseRun = result.run;
        duplicate = result.duplicate;
        queueIntentsRecorded = result.queue_intents_recorded;
      } catch (error) { writeError = error; }

      if (responseRun && !matchesCreate(responseRun, projectId, body)) {
        return { kind: "conflict", error: new RunsRequestError(409, "conflict", undefined, "AGENT_RUN_CONFLICT"), current: responseRun };
      }
      try {
        const saved = responseRun
          ? await request<AgentRunRecord>(runPath(projectId, responseRun.id))
          : await findRunByIdempotencyKey(projectId, idempotencyKey);
        if (saved) {
          if (!matchesCreate(saved, projectId, body)) {
            return { kind: "conflict", error: new RunsRequestError(409, "conflict", undefined, "AGENT_RUN_CONFLICT"), current: saved };
          }
          return { kind: "confirmed", run: saved, duplicate: duplicate || Boolean(writeError), queueIntentsRecorded };
        }
        if (writeError && isStatus(writeError, 409)) return { kind: "conflict", error: asError(writeError) };
        if (writeError && isPersistenceBlocked(writeError)) return { kind: "blocked", error: asError(writeError) };
        return { kind: "unknown", error: asError(writeError ?? new RunsRequestError(0, "invalidResponse", undefined, "AGENT_RUN_WRITE_UNCONFIRMED")) };
      } catch (readbackError) {
        if (writeError && isStatus(writeError, 409)) return { kind: "conflict", error: asError(writeError), readbackError: asError(readbackError) };
        if (writeError && isPersistenceBlocked(writeError)) return { kind: "blocked", error: asError(writeError), readbackError: asError(readbackError) };
        return { kind: "unknown", error: asError(writeError ?? new RunsRequestError(0, "invalidResponse", undefined, "AGENT_RUN_WRITE_UNCONFIRMED")), readbackError: asError(readbackError) };
      }
    },
    cancelRun: (projectId: string, runId: string, body: CancelRunBody, idempotencyKey: string) => request<AgentRunCancelled>(`${runPath(projectId, runId)}/cancel`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(body),
    }),
    async cancelRunWithReadback(projectId: string, input: CancelRunCommandInput): Promise<WriteOutcome<AgentRunRecord>> {
      let writeError: unknown;
      let duplicate = false;
      try {
        const result = await request<AgentRunCancelled>(`${runPath(projectId, input.run.id)}/cancel`, {
          method: "POST",
          headers: { "Idempotency-Key": input.idempotencyKey },
          body: JSON.stringify({
            expected_sequence: input.run.sequence,
            expected_status: input.run.status,
            ...(input.reason ? { reason: input.reason } : {}),
          }),
        });
        duplicate = result.duplicate;
      } catch (error) { writeError = error; }
      try {
        const current = await request<AgentRunRecord>(runPath(projectId, input.run.id));
        const events = await cursorPage<ControlPlaneEvent>(`${runPath(projectId, input.run.id)}/events?${queryString({
          limit: 20,
          aggregate_type: "agent_run",
          aggregate_id: input.run.id,
          after_sequence: input.run.sequence,
        })}`);
        const cancelled = events.items.some((event) => event.sequence === input.run.sequence + 1
          && event.event_type === "agent_run.cancelled"
          && event.payload.status === "cancelled"
          && isCommandEvent(event, input.idempotencyKey));
        if (cancelled && current.status === "cancelled") return { kind: "confirmed", value: current, duplicate };
        if (writeError && isStatus(writeError, 409)) return { kind: "conflict", error: asError(writeError), current };
        if (writeError && isPersistenceBlocked(writeError)) return { kind: "blocked", error: asError(writeError) };
        return { kind: "unknown", error: asError(writeError ?? new RunsRequestError(0, "invalidResponse", undefined, "AGENT_RUN_CANCEL_UNCONFIRMED")) };
      } catch (readbackError) {
        if (writeError && isStatus(writeError, 409)) return { kind: "conflict", error: asError(writeError), readbackError: asError(readbackError) };
        if (writeError && isPersistenceBlocked(writeError)) return { kind: "blocked", error: asError(writeError), readbackError: asError(readbackError) };
        return { kind: "unknown", error: asError(writeError ?? new RunsRequestError(0, "invalidResponse", undefined, "AGENT_RUN_CANCEL_UNCONFIRMED")), readbackError: asError(readbackError) };
      }
    },
    decideAgentApproval: (projectId: string, input: ApprovalCommandInput) => request<ApprovalDecisionResult>(`${project(projectId)}/agent/approvals/${encodeURIComponent(input.item.approval.id)}/decision`, {
      method: "POST",
      headers: { "Idempotency-Key": input.idempotencyKey },
      body: JSON.stringify({
        expected_approval_sequence: input.item.approval_sequence,
        expected_run_sequence: input.item.run_sequence,
        decision: input.decision,
        ...(input.reason ? { reason: input.reason } : {}),
      }),
    }),
    async decideApprovalWithReadback(projectId: string, input: ApprovalCommandInput): Promise<WriteOutcome<ControlPlaneEvent>> {
      let duplicate = false;
      let writeError: unknown;
      try {
        const result = await request<ApprovalDecisionResult>(`${project(projectId)}/agent/approvals/${encodeURIComponent(input.item.approval.id)}/decision`, {
          method: "POST",
          headers: { "Idempotency-Key": input.idempotencyKey },
          body: JSON.stringify({
            expected_approval_sequence: input.item.approval_sequence,
            expected_run_sequence: input.item.run_sequence,
            decision: input.decision,
            ...(input.reason ? { reason: input.reason } : {}),
          }),
        });
        duplicate = result.duplicate;
      } catch (error) { writeError = error; }
      try {
        const decisionEvent = await readApprovalDecision(projectId, input);
        if (decisionEvent) {
          return { kind: "confirmed", duplicate: duplicate || writeError !== undefined, value: decisionEvent };
        }
        if (writeError && isStatus(writeError, 409)) return { kind: "conflict", error: asError(writeError) };
        if (writeError && isPersistenceBlocked(writeError)) return { kind: "blocked", error: asError(writeError) };
        return { kind: "unknown", error: asError(writeError ?? new RunsRequestError(0, "invalidResponse", undefined, "AGENT_RUN_DECISION_UNCONFIRMED")) };
      } catch (readbackError) {
        if (writeError && isStatus(writeError, 409)) return { kind: "conflict", error: asError(writeError), readbackError: asError(readbackError) };
        if (writeError && isPersistenceBlocked(writeError)) return { kind: "blocked", error: asError(writeError), readbackError: asError(readbackError) };
        return { kind: "unknown", error: asError(writeError ?? new RunsRequestError(0, "invalidResponse", undefined, "AGENT_RUN_DECISION_UNCONFIRMED")), readbackError: asError(readbackError) };
      }
    },
    async listSessions(projectId): Promise<RunSession[]> {
      const response = await request<{ items: RunSession[] }>(`${project(projectId)}/agent/sessions`);
      return response.items;
    },
    getSession: (projectId, sessionId) => request<RunSession>(session(projectId, sessionId)),
    getTranscript: (projectId, sessionId, query = {}) => {
      const params = new URLSearchParams({ session_id: sessionId, limit: "500" });
      if (query.beforeSeq !== undefined) params.set("before_seq", String(query.beforeSeq));
      return request<RunTranscript>(`${project(projectId)}/chat/transcript?${params}`);
    },
    async listPendingApprovals(projectId, sessionId): Promise<SessionApproval[]> {
      const response = await request<{ items: SessionApproval[] }>(`${session(projectId, sessionId)}/pending-approvals`);
      return response.items;
    },
    decideApproval: (projectId, sessionId, approvalId, decision): Promise<SessionApprovalDecision> => request<SessionApprovalDecision>(`${session(projectId, sessionId)}/tool-approvals`, {
      method: "POST",
      body: JSON.stringify({ id: approvalId, decision }),
    }),
    cancelSession: (projectId, sessionId) => request<unknown>(`${session(projectId, sessionId)}/cancel`, { method: "POST" }),
    async listWorkspaceFiles(projectId, path = "") {
      const params = new URLSearchParams();
      if (path) params.set("path", path);
      const response = await request<{ entries: WorkspaceFile[] }>(`${project(projectId)}/container/workspace/entries${params.size ? `?${params}` : ""}`);
      return response.entries;
    },
    async downloadWorkspaceFile(projectId, path) {
      const token = options.getAccessToken?.();
      const params = new URLSearchParams({ path });
      const response = await send(`${baseUrl}${project(projectId)}/container/workspace/content?${params}`, {
        headers: { Accept: "application/octet-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      });
      if (!response.ok) throw responseError(response, await readErrorBody(response));
      return response.blob();
    },
  };
}

/**
 * What a chat transcript says about the run behind it, in the reader's language.
 *
 * The values are copy, not data: they belonged in the catalogue, not in a
 * helper that only knows English.
 */
export function transcriptRunStatus(t: Translate, transcript?: RunTranscript, session?: RunSession): string {
  if (transcript?.turn_in_progress) return t("runs.turnRunning");
  if (session?.status === "cancelled") return t("runs.statusCancelled");
  if (session?.status === "closed") return t("runs.statusClosed");
  if (session?.status === "suspended") return t("runs.statusSuspended");
  if (session?.status === "active") return t("runs.statusActive");
  return t("runs.statusUnavailable");
}

/**
 * When an approval stops being decidable, in the reader's language.
 *
 * The date itself goes through `formatDateTime`, so a Russian interface shows
 * "12 нояб. 2026 г." even on an English machine.
 */
export function approvalExpiryLabel(t: Translate, locale: Locale, expiresAt?: string | null, now = Date.now()): string {
  if (!expiresAt) return t("runs.noExpiry");
  const timestamp = Date.parse(expiresAt);
  if (!Number.isFinite(timestamp)) return t("runs.expiryInvalid");
  if (timestamp <= now) return t("runs.expired");
  return t("runs.expiresAt", { date: formatDateTime(timestamp, locale) });
}

export function extractApprovalContext(transcript: RunTranscript | undefined, approval: SessionApproval): string | undefined {
  const block = transcript?.blocks.find((item) => item.kind === "approval" && (item.id === approval.id || item.metadata?.approval_id === approval.id || item.metadata?.id === approval.id));
  const context = block?.reason ?? block?.metadata?.context ?? block?.metadata?.reason ?? block?.text;
  return typeof context === "string" && context.trim() ? context : undefined;
}

export function normalizeWorkspacePath(path: string): string {
  return path.replace(/\\/g, "/").split("/").filter((part) => part && part !== "." && part !== "..").join("/");
}
