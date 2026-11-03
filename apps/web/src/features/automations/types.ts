export type JsonObject = Record<string, unknown>;

export type AutomationLoadStatus = "loading" | "ready" | "error";

export interface Schedule {
  id: string;
  project_id: string;
  name: string;
  cron: string;
  timezone: string;
  payload: JsonObject;
  enabled: boolean;
  next_run_at?: string | null;
  last_run_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ScheduleInput {
  name: string;
  cron: string;
  timezone: string;
  payload: JsonObject;
  enabled: boolean;
}

export type SchedulePatch = Partial<ScheduleInput>;

export interface ProjectTrigger {
  id: string;
  project_id: string;
  kind: string;
  payload: JsonObject;
  status: string;
  attempts?: number;
  lease_until?: string | null;
  leased_by?: string | null;
  available_at?: string | null;
  last_error?: string | null;
  created_at?: string | null;
}

export interface TriggerInput {
  kind: string;
  payload: JsonObject;
}

export interface TriggerDispatchResult {
  dispatched: boolean;
  reason?: string;
  trigger_id?: string;
  kind?: string;
  count?: number;
  items?: TriggerDispatchItem[];
  action?: string;
  run?: Record<string, unknown>;
}

export interface TriggerDispatchItem {
  dispatched: boolean;
  trigger_id?: string;
  kind?: string;
  retried?: boolean;
  attempts?: number;
  reason?: string;
  action?: string;
  run?: Record<string, unknown>;
}

export interface AgentRun {
  id: string;
  project_id: string;
  session_id: string | null;
  requested_by_employee_id: string | null;
  idempotency_key: string;
  status: "queued" | "running" | "waiting_approval" | "succeeded" | "failed" | "cancelled";
  attempt: number;
  input_payload: JsonObject;
  output_payload: JsonObject | null;
  failure_code: string | null;
  failure_message: string | null;
  sequence: number;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookSubscription {
  id: string;
  url: string;
  events: string[];
  project_id?: string | null;
  enabled: boolean;
  fail_count?: number;
  created_at?: string | null;
}

export interface WebhookCreateInput {
  url: string;
  events: string[];
  project_id: string;
}

export interface WebhookCreateResponse {
  id: string;
  url: string;
  events: string[];
  project_id?: string | null;
  secret: string;
}

export interface WebhookPatch {
  enabled: boolean;
}

export interface WebhookDelivery {
  id: string;
  subscription_id: string;
  url: string;
  subscription_enabled: boolean;
  event_type: string;
  status: string;
  attempts: number;
  retry_history: Array<Record<string, unknown>>;
  response_status: number | null;
  last_error: string | null;
  created_at: string | null;
  next_attempt_at: string | null;
  delivered_at: string | null;
}

export interface AutomationCollection<T> {
  status: AutomationLoadStatus;
  items: T[];
  error?: string;
}

export interface AutomationRequestErrorShape {
  code?: string;
  title?: string;
  detail?: string;
  status?: number;
  trace_id?: string;
}

export interface AutomationService {
  listSchedules(projectId: string): Promise<Schedule[]>;
  createSchedule(projectId: string, input: ScheduleInput): Promise<Schedule>;
  updateSchedule(projectId: string, scheduleId: string, patch: SchedulePatch): Promise<Schedule>;
  deleteSchedule(projectId: string, scheduleId: string): Promise<void>;

  listTriggers(projectId: string): Promise<ProjectTrigger[]>;
  enqueueTrigger(projectId: string, input: TriggerInput): Promise<ProjectTrigger>;
  dispatchNextTrigger(projectId: string): Promise<TriggerDispatchResult>;
  listAgentRuns(projectId: string): Promise<AgentRun[]>;

  listWebhooks(projectId: string): Promise<WebhookSubscription[]>;
  createWebhook(input: WebhookCreateInput): Promise<WebhookCreateResponse>;
  updateWebhook(subscriptionId: string, patch: WebhookPatch): Promise<{ id: string; enabled: boolean }>;
  deleteWebhook(subscriptionId: string): Promise<{ ok: boolean }>;
  listWebhookDeliveries(limit?: number): Promise<WebhookDelivery[]>;
  retryWebhookDelivery(deliveryId: string): Promise<{ id: string; status: string }>;
}

export interface AutomationServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export const PROJECT_TRIGGER_KINDS = [
  "chat.message",
  "chat.regenerate",
  "project.prepare",
  "system.schedule",
  "webhook.http",
  "telegram.message",
] as const;

export type ProjectTriggerKind = (typeof PROJECT_TRIGGER_KINDS)[number];

export interface AutomationsViewProps {
  projectId: string;
  service: AutomationService;
}

export interface AutomationsPageProps {
  projectId: string;
  service?: AutomationService;
}
