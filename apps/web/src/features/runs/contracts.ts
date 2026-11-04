export type RunStatus = "queued" | "running" | "waiting_approval" | "succeeded" | "failed" | "cancelled" | string;
export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "cancelled" | string;
export type RunAggregateType = "agent_run" | "approval_request";

export interface AgentRunRecord {
  id: string;
  project_id: string;
  session_id: string | null;
  requested_by_employee_id: string | null;
  idempotency_key: string;
  status: RunStatus;
  attempt: number;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown> | null;
  failure_code: string | null;
  failure_message: string | null;
  sequence: number;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRecord {
  id: string;
  run_id: string;
  tool_call_id: string;
  approval_kind: string;
  status: ApprovalStatus;
  requested_payload: Record<string, unknown>;
  decision_reason: string | null;
  decided_by_employee_id: string | null;
  requested_at: string;
  expires_at: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalInboxItem {
  approval: ApprovalRecord;
  project_id: string;
  session_id: string | null;
  run_status: RunStatus;
  approval_sequence: number;
  run_sequence: number;
}

export interface ControlPlaneEvent {
  event_id: string;
  event_type: string;
  schema_version: number;
  occurred_at: string;
  producer: string;
  aggregate_type: RunAggregateType | "agent_session";
  aggregate_id: string;
  run_id: string | null;
  session_id: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  idempotency_key: string | null;
  sequence: number | null;
  payload: Record<string, unknown>;
}

export interface CursorPage<T> {
  items: T[];
  hasMore: boolean;
  nextCursor?: string;
}

export interface AgentRunCreated {
  run: AgentRunRecord;
  duplicate: boolean;
  queue_intents_recorded: number;
}

export interface AgentRunCancelled {
  run: AgentRunRecord;
  duplicate: boolean;
  queue_intents_recorded: number;
}

export interface ApprovalDecisionResult {
  approval: ApprovalRecord;
  run: AgentRunRecord;
  duplicate: boolean;
  queue_intents_recorded: number;
}

export interface CreateRunBody { session_id?: string | null; input_payload: Record<string, unknown> }
export interface CancelRunBody { expected_sequence: number; expected_status: "queued" | "running"; reason?: string }
export interface ApprovalDecisionBody { expected_approval_sequence: number; expected_run_sequence: number; decision: "approve" | "deny"; reason?: string }
export interface RunPageOptions { limit?: number; cursor?: string; offset?: number }
export interface RunEventsOptions { limit?: number; cursor?: string; aggregateType?: RunAggregateType; aggregateId?: string; afterSequence?: number }

export interface ApprovalCommandInput {
  item: ApprovalInboxItem;
  decision: "approve" | "deny";
  reason?: string;
  idempotencyKey: string;
}

export interface CancelRunCommandInput { run: AgentRunRecord; reason?: string; idempotencyKey: string }
export type WriteOutcome<T> =
  | { kind: "confirmed"; value: T; duplicate: boolean }
  | { kind: "conflict"; error: Error; current?: unknown; readbackError?: Error }
  | { kind: "blocked"; error: Error; readbackError?: Error }
  | { kind: "unknown"; error: Error; readbackError?: Error };
export type RunCreateResult =
  | { kind: "confirmed"; run: AgentRunRecord; duplicate: boolean; queueIntentsRecorded: number }
  | { kind: "conflict"; error: Error; current?: unknown; readbackError?: Error }
  | { kind: "blocked" | "unknown"; error: Error; readbackError?: Error };

export type RunSessionStatus = "active" | "suspended" | "closed" | "cancelled" | string;

export interface RunSession {
  id: string;
  project_id: string;
  status: RunSessionStatus;
  title?: string | null;
  provider?: string | null;
  api_kind?: string | null;
  model?: string | null;
  created_at?: string | null;
  last_message_at?: string | null;
  agent_tokens_used?: number;
  agent_requests?: number;
}

export interface RunTimelineBlock {
  kind: string;
  text?: string;
  seq?: number;
  created_at?: string;
  metadata?: Record<string, unknown>;
  id?: string;
  reason?: string;
  input?: unknown;
  output?: unknown;
  name?: string;
  [key: string]: unknown;
}

export interface RunTranscript {
  session_id: string;
  blocks: RunTimelineBlock[];
  session_status?: string;
  turn_in_progress: boolean;
  has_more?: boolean;
  oldest_seq?: number;
  newest_seq?: number;
}

export interface SessionApproval {
  id: string;
  name: string;
  input: unknown;
  expires_at?: string | null;
  reason?: string | null;
}

export interface InboxApproval extends SessionApproval {
  session_id: string;
  session_title: string;
  context?: string;
}

export interface WorkspaceFile {
  name: string;
  path: string;
  kind: "file" | "dir";
  size: number | null;
  modified_at: string | null;
}

export interface SessionApprovalDecision {
  session_id: string;
  approval_id: string;
  decision: "approve" | "deny";
  events?: Array<{ type: string; data: Record<string, unknown> }>;
}

export interface RunsService {
  listRuns(projectId: string, options?: RunPageOptions): Promise<CursorPage<AgentRunRecord>>;
  getRun(projectId: string, runId: string, signal?: AbortSignal): Promise<AgentRunRecord>;
  listRunEvents(projectId: string, runId: string, options?: RunEventsOptions, signal?: AbortSignal): Promise<CursorPage<ControlPlaneEvent>>;
  listApprovalInbox(projectId: string, options?: Omit<RunPageOptions, "offset">): Promise<CursorPage<ApprovalInboxItem>>;
  createRun(projectId: string, body: CreateRunBody, idempotencyKey: string): Promise<AgentRunCreated>;
  createRunWithReadback(projectId: string, body: CreateRunBody, idempotencyKey: string): Promise<RunCreateResult>;
  cancelRun(projectId: string, runId: string, body: CancelRunBody, idempotencyKey: string): Promise<AgentRunCancelled>;
  cancelRunWithReadback(projectId: string, input: CancelRunCommandInput): Promise<WriteOutcome<AgentRunRecord>>;
  decideAgentApproval(projectId: string, input: ApprovalCommandInput): Promise<ApprovalDecisionResult>;
  decideApprovalWithReadback(projectId: string, input: ApprovalCommandInput): Promise<WriteOutcome<ControlPlaneEvent>>;
  listSessions(projectId: string): Promise<RunSession[]>;
  getSession(projectId: string, sessionId: string): Promise<RunSession>;
  getTranscript(projectId: string, sessionId: string, options?: { beforeSeq?: number }): Promise<RunTranscript>;
  listPendingApprovals(projectId: string, sessionId: string): Promise<SessionApproval[]>;
  decideApproval(projectId: string, sessionId: string, approvalId: string, decision: "approve" | "deny"): Promise<SessionApprovalDecision>;
  cancelSession(projectId: string, sessionId: string): Promise<unknown>;
  downloadWorkspaceFile(projectId: string, path: string): Promise<Blob>;
  listWorkspaceFiles(projectId: string, path?: string): Promise<WorkspaceFile[]>;
}

export interface RunsServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}
