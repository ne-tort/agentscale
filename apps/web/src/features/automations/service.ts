import type {
  AutomationRequestErrorShape,
  AutomationService,
  AutomationServiceOptions,
  AgentRun,
  TriggerDispatchResult,
  WebhookDelivery,
  JsonObject,
  ProjectTrigger,
  Schedule,
  ScheduleInput,
  SchedulePatch,
  WebhookCreateInput,
  WebhookCreateResponse,
  WebhookPatch,
  WebhookSubscription,
} from "./types";

/**
 * Codes this layer raises itself. A code the server sends travels through
 * unchanged and is shown as the server wrote it; these are the ones the screen
 * knows how to word.
 */
export type AutomationErrorCode =
  | "NETWORK_ERROR"
  | "HTTP_ERROR"
  | "INVALID_RESPONSE"
  | "RESPONSE_OTHER_PROJECT"
  | "RETRY_NOT_CONFIRMED"
  | "PAYLOAD_NOT_JSON"
  | "PAYLOAD_NOT_OBJECT";

export class AutomationRequestError extends Error {
  readonly status?: number;
  readonly code: string;
  readonly traceId?: string;
  /** A field path or short machine token, for the technical line. Never UI copy. */
  readonly detail?: string;
  /** Text the server sent for a person, kept exactly as it arrived. */
  readonly serverMessage?: string;
  readonly retryable: boolean;

  constructor(code: AutomationErrorCode | string, details: Omit<AutomationRequestErrorShape, "code"> = {}) {
    // `message` is a diagnostic for logs and test output, not for the screen:
    // the code names the failure and the detail says where. Server text, when
    // there is any, is what a person should see, so it wins.
    super(details.server_message ?? (details.detail ? `${code}: ${details.detail}` : code));
    this.name = "AutomationRequestError";
    this.status = details.status;
    this.code = code;
    this.traceId = details.trace_id;
    this.detail = details.detail;
    this.serverMessage = details.server_message;
    this.retryable = details.status === undefined || details.status === 0 || details.status >= 500 || details.status === 408 || details.status === 429;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function itemsFromUnknown(value: unknown): unknown[] {
  if (!isRecord(value) || !Array.isArray(value.items)) throw invalidResponse("items");
  return value.items;
}

function itemsFrom<T>(value: unknown): T[] {
  return itemsFromUnknown(value) as T[];
}

/**
 * The server answered with something this client does not understand.
 *
 * `detail` is the path of the field that did not fit — `run.status`,
 * `deliveries[].attempts`. It is a locator, not an explanation: the sentence a
 * person reads is put together by the screen from the code.
 */
function invalidResponse(detail?: string): AutomationRequestError {
  return new AutomationRequestError("INVALID_RESPONSE", detail === undefined ? {} : { detail });
}

function rejectUnknownFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const unknown = Object.keys(value).filter((field) => !fields.includes(field));
  if (unknown.length) throw invalidResponse(`${label}.${unknown[0]}`);
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw invalidResponse(field);
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  return requiredString(value, field);
}

function requiredInteger(value: unknown, field: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) throw invalidResponse(field);
  return value;
}

function jsonObject(value: unknown, field: string): JsonObject {
  if (!isRecord(value)) throw invalidResponse(field);
  return value;
}

function agentRunStatus(value: unknown): AgentRun["status"] {
  if (value === "queued" || value === "running" || value === "waiting_approval" || value === "succeeded" || value === "failed" || value === "cancelled") return value;
  throw invalidResponse("run.status");
}

function dateTime(value: unknown, field: string): string | null {
  const result = nullableString(value, field);
  if (result !== null && !Number.isFinite(Date.parse(result))) throw invalidResponse(field);
  return result;
}

function projectWebhookFrom(value: unknown, projectId: string): WebhookSubscription {
  if (!isRecord(value) || !Array.isArray(value.events)) throw invalidResponse("webhooks[]");
  const id = requiredString(value.id, "webhook.id");
  const linkedProjectId = requiredString(value.project_id, "webhook.project_id");
  if (linkedProjectId !== projectId) throw new AutomationRequestError("RESPONSE_OTHER_PROJECT", { detail: "webhook.project_id" });
  const events = value.events.map((event) => requiredString(event, "webhook.events[]"));
  if (typeof value.enabled !== "boolean") throw invalidResponse("webhook.enabled");
  const failCount = value.fail_count === undefined ? undefined : requiredInteger(value.fail_count, "webhook.fail_count");
  const createdAt = value.created_at === undefined ? undefined : dateTime(value.created_at, "webhook.created_at");
  return {
    id,
    url: requiredString(value.url, "webhook.url"),
    events,
    project_id: linkedProjectId,
    enabled: value.enabled,
    ...(failCount === undefined ? {} : { fail_count: failCount }),
    ...(createdAt === undefined ? {} : { created_at: createdAt }),
  };
}

function agentRunFrom(value: unknown, projectId: string): AgentRun {
  if (!isRecord(value)) throw invalidResponse("runs[]");
  rejectUnknownFields(value, ["id", "project_id", "session_id", "requested_by_employee_id", "idempotency_key", "status", "attempt", "input_payload", "output_payload", "failure_code", "failure_message", "sequence", "started_at", "heartbeat_at", "finished_at", "created_at", "updated_at"], "run");
  const record: AgentRun = {
    id: requiredString(value.id, "run.id"),
    project_id: requiredString(value.project_id, "run.project_id"),
    session_id: nullableString(value.session_id, "run.session_id"),
    requested_by_employee_id: nullableString(value.requested_by_employee_id, "run.requested_by_employee_id"),
    idempotency_key: requiredString(value.idempotency_key, "run.idempotency_key"),
    status: agentRunStatus(value.status),
    attempt: requiredInteger(value.attempt, "run.attempt", 1),
    input_payload: jsonObject(value.input_payload, "run.input_payload"),
    output_payload: value.output_payload === null ? null : jsonObject(value.output_payload, "run.output_payload"),
    failure_code: nullableString(value.failure_code, "run.failure_code"),
    failure_message: nullableString(value.failure_message, "run.failure_message"),
    sequence: requiredInteger(value.sequence, "run.sequence", 1),
    started_at: dateTime(value.started_at, "run.started_at"),
    heartbeat_at: dateTime(value.heartbeat_at, "run.heartbeat_at"),
    finished_at: dateTime(value.finished_at, "run.finished_at"),
    created_at: requiredString(value.created_at, "run.created_at"),
    updated_at: requiredString(value.updated_at, "run.updated_at"),
  };
  dateTime(record.created_at, "run.created_at");
  dateTime(record.updated_at, "run.updated_at");
  if (record.project_id !== projectId) throw new AutomationRequestError("RESPONSE_OTHER_PROJECT", { detail: "run.project_id" });
  return record;
}

function agentRunsFrom(value: unknown, projectId: string): AgentRun[] {
  if (!isRecord(value) || !Array.isArray(value.items)) throw invalidResponse("runs.items");
  rejectUnknownFields(value, ["items", "limit", "offset"], "runs");
  requiredInteger(value.limit, "runs.limit", 1);
  requiredInteger(value.offset, "runs.offset");
  return value.items.map((item) => agentRunFrom(item, projectId));
}

function webhookDeliveryFrom(value: unknown): WebhookDelivery {
  if (!isRecord(value) || !Array.isArray(value.retry_history)) throw invalidResponse("deliveries[]");
  rejectUnknownFields(value, ["id", "subscription_id", "url", "subscription_enabled", "event_type", "status", "attempts", "retry_history", "response_status", "last_error", "created_at", "next_attempt_at", "delivered_at"], "delivery");
  const retryHistory = value.retry_history.map((entry) => {
    if (!isRecord(entry)) throw invalidResponse("deliveries[].retry_history[]");
    if (entry.kind !== undefined && typeof entry.kind !== "string") throw invalidResponse("deliveries[].retry_history[].kind");
    return entry;
  });
  if (typeof value.subscription_enabled !== "boolean") throw invalidResponse("delivery.subscription_enabled");
  const responseStatus = value.response_status;
  if (responseStatus !== null && (typeof responseStatus !== "number" || !Number.isInteger(responseStatus))) {
    throw invalidResponse("delivery.response_status");
  }
  return {
    id: requiredString(value.id, "delivery.id"),
    subscription_id: requiredString(value.subscription_id, "delivery.subscription_id"),
    url: requiredString(value.url, "delivery.url"),
    subscription_enabled: value.subscription_enabled,
    event_type: requiredString(value.event_type, "delivery.event_type"),
    status: requiredString(value.status, "delivery.status"),
    attempts: requiredInteger(value.attempts, "delivery.attempts"),
    retry_history: retryHistory,
    response_status: responseStatus,
    last_error: nullableString(value.last_error, "delivery.last_error"),
    created_at: dateTime(value.created_at, "delivery.created_at"),
    next_attempt_at: dateTime(value.next_attempt_at, "delivery.next_attempt_at"),
    delivered_at: dateTime(value.delivered_at, "delivery.delivered_at"),
  };
}

function webhookDeliveriesFrom(value: unknown): WebhookDelivery[] {
  if (!isRecord(value)) throw invalidResponse("deliveries");
  rejectUnknownFields(value, ["items"], "deliveries");
  return itemsFromUnknown(value).map(webhookDeliveryFrom);
}

function webhookRetryFrom(value: unknown): { id: string; status: string } {
  if (!isRecord(value)) throw invalidResponse("retry");
  rejectUnknownFields(value, ["id", "status"], "retry");
  return { id: requiredString(value.id, "retry.id"), status: requiredString(value.status, "retry.status") };
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function projectPath(projectId: string, suffix = ""): string {
  return `/projects/${encode(projectId)}${suffix}`;
}

function errorDetails(value: unknown, response: Response): AutomationRequestErrorShape {
  if (!isRecord(value)) return { status: response.status, server_message: response.statusText || undefined };
  return {
    status: response.status,
    server_message: stringValue(value.detail) ?? stringValue(value.message) ?? stringValue(value.title),
    code: stringValue(value.code),
    trace_id: stringValue(value.trace_id),
  };
}

/**
 * Reads run parameters.
 *
 * Throws a coded error rather than a sentence: both callers replace it with
 * their own translated field message, and a message written here would be
 * Russian prose inside an English screen the moment one of them stopped.
 */
export function parseJsonObject(value: string): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new AutomationRequestError("PAYLOAD_NOT_JSON");
  }
  if (!isRecord(parsed)) throw new AutomationRequestError("PAYLOAD_NOT_OBJECT");
  return parsed;
}

export function createAutomationService(options: AutomationServiceOptions = {}): AutomationService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function readJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      return undefined;
    }
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (cause) {
      throw new AutomationRequestError("NETWORK_ERROR", {
        status: 0,
        detail: cause instanceof Error ? cause.message : undefined,
      });
    }

    if (!response.ok) {
      const details = errorDetails(await readJson(response), response);
      throw new AutomationRequestError(details.code ?? "HTTP_ERROR", details);
    }
    if (response.status === 204) return undefined as T;
    const body = await readJson(response);
    return body as T;
  }

  return {
    async listSchedules(projectId): Promise<Schedule[]> {
      const body = await request<unknown>(projectPath(projectId, "/schedules"));
      return itemsFrom<Schedule>(body);
    },

    createSchedule: (projectId: string, input: ScheduleInput) =>
      request<Schedule>(projectPath(projectId, "/schedules"), {
        method: "POST",
        body: JSON.stringify(input),
      }),

    updateSchedule: (projectId: string, scheduleId: string, patch: SchedulePatch) =>
      request<Schedule>(projectPath(projectId, `/schedules/${encode(scheduleId)}`), {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),

    deleteSchedule: (projectId: string, scheduleId: string) =>
      request<void>(projectPath(projectId, `/schedules/${encode(scheduleId)}`), { method: "DELETE" }),

    async listTriggers(projectId): Promise<ProjectTrigger[]> {
      const body = await request<unknown>(projectPath(projectId, "/triggers"));
      return itemsFrom<ProjectTrigger>(body);
    },

    enqueueTrigger: (projectId: string, input) =>
      request<ProjectTrigger>(projectPath(projectId, "/triggers"), {
        method: "POST",
        body: JSON.stringify(input),
      }),

    dispatchNextTrigger: (projectId: string) =>
      request<TriggerDispatchResult>(projectPath(projectId, "/triggers/dispatch"), { method: "POST" }),

    async listAgentRuns(projectId: string): Promise<AgentRun[]> {
      const body = await request<unknown>(projectPath(projectId, "/agent/runs?limit=50"));
      return agentRunsFrom(body, projectId);
    },

    async listWebhooks(projectId): Promise<WebhookSubscription[]> {
      const query = `?project_id=${encode(projectId)}`;
      const body = await request<unknown>(`/external/webhooks${query}`);
      return itemsFromUnknown(body).map((item) => projectWebhookFrom(item, projectId));
    },

    createWebhook: (input: WebhookCreateInput) =>
      request<WebhookCreateResponse>("/external/webhooks", {
        method: "POST",
        body: JSON.stringify(input),
      }),

    updateWebhook: (subscriptionId: string, patch: WebhookPatch) =>
      request<{ id: string; enabled: boolean }>(`/external/webhooks/${encode(subscriptionId)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),

    deleteWebhook: (subscriptionId: string) =>
      request<{ ok: boolean }>(`/external/webhooks/${encode(subscriptionId)}`, { method: "DELETE" }),

    async listWebhookDeliveries(limit = 200): Promise<WebhookDelivery[]> {
      const body = await request<unknown>(`/external/webhook-deliveries?limit=${encode(String(limit))}`);
      return webhookDeliveriesFrom(body);
    },

    async retryWebhookDelivery(deliveryId: string) {
      const body = await request<unknown>(`/external/webhook-deliveries/${encode(deliveryId)}/retry`, {
        method: "POST",
      });
      const response = webhookRetryFrom(body);
      if (response.id !== deliveryId || response.status !== "pending") throw new AutomationRequestError("RETRY_NOT_CONFIRMED", { detail: response.id });
      return response;
    },
  };
}

export function createDefaultAutomationService(getAccessToken?: () => string | undefined): AutomationService {
  return createAutomationService({ getAccessToken });
}
