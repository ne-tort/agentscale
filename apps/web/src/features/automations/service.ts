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

export class AutomationRequestError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly traceId?: string;
  readonly retryable: boolean;

  constructor(message: string, details: AutomationRequestErrorShape = {}) {
    super(message);
    this.name = "AutomationRequestError";
    this.status = details.status;
    this.code = details.code;
    this.traceId = details.trace_id;
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
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new AutomationRequestError("Backend вернул некорректный список автоматизаций.", { code: "INVALID_RESPONSE" });
  }
  return value.items;
}

function itemsFrom<T>(value: unknown): T[] {
  return itemsFromUnknown(value) as T[];
}

function invalidResponse(detail: string): AutomationRequestError {
  return new AutomationRequestError(`Backend вернул некорректные данные автоматизации: ${detail}`, { code: "INVALID_RESPONSE" });
}

function rejectUnknownFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const unknown = Object.keys(value).filter((field) => !fields.includes(field));
  if (unknown.length) throw invalidResponse(`${label} содержит неизвестные поля: ${unknown.join(", ")}.`);
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw invalidResponse(`${field} должен быть строкой.`);
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  return requiredString(value, field);
}

function requiredInteger(value: unknown, field: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) {
    throw invalidResponse(`${field} должен быть целым числом не меньше ${minimum}.`);
  }
  return value;
}

function jsonObject(value: unknown, field: string): JsonObject {
  if (!isRecord(value)) throw invalidResponse(`${field} должен быть JSON-объектом.`);
  return value;
}

function agentRunStatus(value: unknown): AgentRun["status"] {
  if (value === "queued" || value === "running" || value === "waiting_approval" || value === "succeeded" || value === "failed" || value === "cancelled") return value;
  throw invalidResponse("run.status не входит в enum AgentRunStatus.");
}

function dateTime(value: unknown, field: string): string | null {
  const result = nullableString(value, field);
  if (result !== null && !Number.isFinite(Date.parse(result))) throw invalidResponse(`${field} должен быть датой ISO или null.`);
  return result;
}

function projectWebhookFrom(value: unknown, projectId: string): WebhookSubscription {
  if (!isRecord(value) || !Array.isArray(value.events)) throw invalidResponse("элемент списка webhook-подписок имеет неверную форму.");
  const id = requiredString(value.id, "webhook.id");
  const linkedProjectId = requiredString(value.project_id, "webhook.project_id");
  if (linkedProjectId !== projectId) throw invalidResponse("ответ фильтра webhook содержит другую project_id.");
  const events = value.events.map((event) => requiredString(event, "webhook.events[]"));
  if (typeof value.enabled !== "boolean") throw invalidResponse("webhook.enabled должен быть boolean.");
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
  if (!isRecord(value)) throw invalidResponse("элемент списка Agent Runs должен быть объектом.");
  rejectUnknownFields(value, ["id", "project_id", "session_id", "requested_by_employee_id", "idempotency_key", "status", "attempt", "input_payload", "output_payload", "failure_code", "failure_message", "sequence", "started_at", "heartbeat_at", "finished_at", "created_at", "updated_at"], "Agent Run");
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
  if (record.project_id !== projectId) throw invalidResponse("Agent Run списка относится к другому проекту.");
  return record;
}

function agentRunsFrom(value: unknown, projectId: string): AgentRun[] {
  if (!isRecord(value) || !Array.isArray(value.items)) throw invalidResponse("список Agent Runs должен содержать items.");
  rejectUnknownFields(value, ["items", "limit", "offset"], "список Agent Runs");
  requiredInteger(value.limit, "runs.limit", 1);
  requiredInteger(value.offset, "runs.offset");
  return value.items.map((item) => agentRunFrom(item, projectId));
}

function webhookDeliveryFrom(value: unknown): WebhookDelivery {
  if (!isRecord(value) || !Array.isArray(value.retry_history)) throw invalidResponse("delivery имеет неверную форму.");
  rejectUnknownFields(value, ["id", "subscription_id", "url", "subscription_enabled", "event_type", "status", "attempts", "retry_history", "response_status", "last_error", "created_at", "next_attempt_at", "delivered_at"], "delivery");
  const retryHistory = value.retry_history.map((entry) => {
    if (!isRecord(entry)) throw invalidResponse("delivery.retry_history должен содержать объекты.");
    if (entry.kind !== undefined && typeof entry.kind !== "string") throw invalidResponse("delivery.retry_history[].kind должен быть строкой.");
    return entry;
  });
  if (typeof value.subscription_enabled !== "boolean") throw invalidResponse("delivery.subscription_enabled должен быть boolean.");
  const responseStatus = value.response_status;
  if (responseStatus !== null && (typeof responseStatus !== "number" || !Number.isInteger(responseStatus))) {
    throw invalidResponse("delivery.response_status должен быть целым числом или null.");
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
  if (!isRecord(value)) throw invalidResponse("список доставок должен быть объектом.");
  rejectUnknownFields(value, ["items"], "список доставок");
  return itemsFromUnknown(value).map(webhookDeliveryFrom);
}

function webhookRetryFrom(value: unknown): { id: string; status: string } {
  if (!isRecord(value)) throw invalidResponse("ответ retry должен быть объектом.");
  rejectUnknownFields(value, ["id", "status"], "ответ retry");
  return { id: requiredString(value.id, "retry.id"), status: requiredString(value.status, "retry.status") };
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function projectPath(projectId: string, suffix = ""): string {
  return `/projects/${encode(projectId)}${suffix}`;
}

function errorDetails(value: unknown, response: Response): AutomationRequestErrorShape {
  if (!isRecord(value)) return { status: response.status, title: response.statusText };
  return {
    status: response.status,
    title: stringValue(value.title) ?? response.statusText,
    detail: stringValue(value.detail) ?? stringValue(value.message),
    code: stringValue(value.code),
    trace_id: stringValue(value.trace_id),
  };
}

export function parseJsonObject(value: string): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Payload должен быть корректным JSON-объектом.");
  }
  if (!isRecord(parsed)) throw new Error("Payload должен быть JSON-объектом, а не массивом или примитивом.");
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
      throw new AutomationRequestError(
        cause instanceof Error ? `Не удалось связаться с API: ${cause.message}` : "Не удалось связаться с API.",
        { status: 0, code: "NETWORK_ERROR" },
      );
    }

    if (!response.ok) {
      const details = errorDetails(await readJson(response), response);
      throw new AutomationRequestError(details.detail ?? details.title ?? `API вернул HTTP ${response.status}.`, details);
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
      if (response.id !== deliveryId || response.status !== "pending") throw invalidResponse("ответ retry не подтвердил указанную delivery в состоянии pending.");
      return response;
    },
  };
}

export function createDefaultAutomationService(getAccessToken?: () => string | undefined): AutomationService {
  return createAutomationService({ getAccessToken });
}
