export type Id = string;

export type ProjectLifecycle = "draft" | "active" | "paused" | "error" | "completed";
export type ContainerStatus = "unknown" | "preparing" | "running" | "stopping" | "stopped" | "failed";
export type SseEventType = "run_started" | "text_delta" | "transcript_block" | "tool_call" | "tool_result" | "status" | "error" | "done" | "keepalive";

export interface ApiError {
  code: string;
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
}

export interface SessionTokens {
  access_token: string;
  refresh_token: string;
  id_token?: string;
  expires_in?: number;
  token_type?: "Bearer";
}

export interface AuthConfig {
  enabled: boolean;
  providers?: string[];
}

export interface Employee {
  id: Id;
  display_name: string;
  email?: string;
}

export interface Cabinet {
  id: Id;
  name: string;
}

export interface CurrentUserResponse {
  id: Id;
  subject: string;
  email?: string;
  display_name?: string;
  roles: string[];
  company_id?: Id;
  employee_id?: Id;
  active: boolean;
}

export type Project = ProjectSummary;

export interface ProjectSummary {
  id: Id;
  cabinet_id: Id;
  name: string;
  status: ProjectLifecycle;
  about?: string;
  selected: boolean;
  new_chat_enabled: boolean;
  observed_state?: string;
  updated_at?: string;
}

export interface ProjectsResponse {
  items: ProjectSummary[];
  selected_project_id?: Id;
}

export interface ContainerStatusResponse {
  project_id: Id;
  container_id?: Id;
  status: ContainerStatus;
  desired_state?: string;
  observed_state?: string;
  runtime_ref?: string;
  last_error?: string;
  updated_at?: string;
}

export interface ChatSidebarItem {
  session_id: Id;
  project_id: Id;
  project_name: string;
  title?: string;
  pinned: boolean;
  has_draft: boolean;
  has_messages: boolean;
  last_message_at?: string;
  status?: string;
}

export interface ChatSidebarProject {
  project_id: Id;
  project_name: string;
  active: boolean;
  new_chat_enabled: boolean;
  chats: ChatSidebarItem[];
}

export interface ChatSidebarResponse {
  cabinet_id: Id;
  selected_project_id?: Id;
  selected_session_id?: Id;
  projects: ChatSidebarProject[];
  project_chats: ChatSidebarItem[];
}

export interface TranscriptBlock {
  kind: string;
  text?: string;
  seq?: number;
  created_at?: string;
  metadata: Record<string, unknown>;
}

export interface ChatTranscriptResponse {
  session_id: Id;
  blocks: TranscriptBlock[];
  oldest_seq?: number;
  newest_seq?: number;
  has_more: boolean;
  turn_in_progress: boolean;
}

export interface ChatRunRequest {
  project_id: Id;
  session_id?: Id;
  text: string;
  turn_id?: string;
  model?: string;
  attachment_refs: string[];
}

/** Scope and durable identity required by the run-aware stream facade. */
export interface ChatRunScope {
  tenant_id: Id;
  project_id: Id;
  session_id: Id;
  turn_id: Id;
  run_id: Id;
}

export interface ChatRunCursor {
  event_id?: string;
  sequence?: number;
}

export interface ChatDurableRunBinding extends ChatRunScope {
  binding_digest: string;
}

export interface SseEventEnvelope {
  event_id: string;
  event_type: SseEventType;
  schema_version: number;
  occurred_at: string;
  project_id: Id;
  session_id?: Id;
  run_id?: Id;
  turn_id?: Id;
  tenant_id?: Id;
  sequence?: number;
  payload: Record<string, unknown>;
  resume_after?: number;
}

export interface ChatStreamOptions {
  cursor?: ChatRunCursor;
  scope: ChatRunScope;
  durableBinding?: ChatDurableRunBinding;
  signal?: AbortSignal;
  onEvent: (event: SseEventEnvelope) => void | Promise<void>;
  onCursorCommit?: (cursor: ChatRunCursor) => void | Promise<void>;
}

export interface ApiClient {
  authConfig(): Promise<AuthConfig>;
  login(username: string, password: string): Promise<SessionTokens>;
  refresh(refreshToken: string): Promise<SessionTokens>;
  logout(tokens: Partial<SessionTokens>): Promise<void>;
  me(): Promise<CurrentUserResponse>;
  listProjects(cabinetId: Id): Promise<ProjectsResponse>;
  containerStatus(projectId: Id): Promise<ContainerStatusResponse>;
  chatSidebar(cabinetId: Id): Promise<ChatSidebarResponse>;
  chatTranscript(projectId: Id, sessionId: Id, options?: { beforeSeq?: number; afterSeq?: number; limit?: number }): Promise<ChatTranscriptResponse>;
  runChat(request: ChatRunRequest, options: ChatStreamOptions): AsyncIterable<SseEventEnvelope>;
}
