export type ProjectRuntimePhase =
  | "absent"
  | "preparing"
  | "pulling"
  | "hydrating"
  | "provisioning"
  | "starting"
  | "running"
  | "paused"
  | "pausing"
  | "suspended"
  | "completed"
  | "failed"
  | "error"
  | "degraded"
  | "unknown";

export type ProjectLifecycle = "draft" | "active" | "paused" | "error" | "completed";
export type RuntimeAction = "launch" | "pause" | "resume" | "reload" | "sync" | "complete";
export type RuntimeTab = "overview" | "chat" | "files" | "settings";

export interface RuntimeMetrics {
  cpu_millicores?: number;
  memory_bytes?: number;
  timestamp?: string;
}

export interface ProjectRuntime {
  observed_state?: ProjectRuntimePhase;
  status?: string;
  orchestrator_status?: string;
  desired_state?: string;
  runtime_ref?: string | null;
  k8s_pod_name?: string | null;
  phase?: string | null;
  ready?: boolean | null;
  last_error?: string | null;
  waiting_reason?: string | null;
  metrics?: RuntimeMetrics | null;
  metrics_available?: boolean;
  metrics_fresh?: boolean;
  metrics_unavailable_reason?: string | null;
  hydrate_generation?: number;
  observed_at?: string;
  restarts?: number | null;
}

export interface ProjectRuntimeProject {
  id: string;
  company_id?: string;
  cabinet_id?: string;
  name: string;
  slug?: string;
  status: ProjectLifecycle | string;
  about?: string | null;
  agent_provider?: string | null;
  resolved_ai_key_id?: string | null;
  budget_tokens?: number | null;
  workspace_outdated_at?: string | null;
  updated_at?: string | null;
  container_ref?: string | null;
  workspace_key?: string | null;
  launch_phase?: string | null;
  runtime?: ProjectRuntime | null;
}

export interface ProjectContainer {
  project_id: string;
  project_name?: string;
  status?: string;
  observed_state?: ProjectRuntimePhase | string | null;
  container_ref?: string | null;
  workspace_key?: string | null;
  runtime?: ProjectRuntime | null;
  runtime_metrics?: RuntimeMetrics | null;
  k8s_phase?: string | null;
  last_error?: string | null;
}

export interface ProjectMetricsResponse {
  project_id?: string;
  latest?: RuntimeMetrics | null;
  series?: Array<Record<string, unknown>>;
  window?: string;
  stale?: boolean;
  metrics_available?: boolean;
  metrics_unavailable_reason?: string | null;
}

export interface WorkspaceEntry {
  name: string;
  path: string;
  kind: "file" | "dir" | string;
  size?: number | null;
  modified_at?: string | null;
}

export interface WorkspaceEntriesResponse {
  path: string;
  entries: WorkspaceEntry[];
}

export interface WorkspacePreviewResponse {
  path: string;
  name: string;
  size?: number | null;
  truncated?: boolean;
  content: string;
}

export interface ProjectAttachment {
  id: string;
  filename: string;
  content_type?: string;
  size_bytes?: number;
  created_at?: string;
}

export interface ProjectKey {
  id: string;
  name: string;
  provider?: string;
  api_kind?: string;
  status?: string;
  has_secret?: boolean;
}

export interface ProjectLiveModel {
  id: string;
  label?: string;
  catalog_matched?: boolean;
}

export interface ProjectModelsResponse {
  models: ProjectLiveModel[];
  default_model?: string | null;
  source?: string;
}

export interface ProjectRuntimeAgentSession {
  id: string;
  project_id: string;
  model?: string | null;
  status?: string;
  [key: string]: unknown;
}

export interface WorkspaceDownload extends Blob {
  readonly contentDisposition?: string | null;
  readonly workspacePath?: string | null;
  readonly workspaceSize?: string | null;
}

export interface ChatSidebarItem {
  session_id: string;
  project_id: string;
  project_name?: string;
  title?: string;
  pinned?: boolean;
  has_draft?: boolean;
  has_messages?: boolean;
  last_message_at?: string;
  status?: string;
}

export interface ChatSidebarResponse {
  cabinet_id: string;
  selected_project_id?: string;
  selected_session_id?: string;
  projects: Array<{
    project_id: string;
    project_name: string;
    active?: boolean;
    new_chat_enabled?: boolean;
    chats: ChatSidebarItem[];
  }>;
  project_chats: ChatSidebarItem[];
}

export interface ProjectRuntimeSnapshot {
  project: ProjectRuntimeProject;
  container: ProjectContainer | null;
  metrics: ProjectMetricsResponse | null;
  files: WorkspaceEntriesResponse | null;
  attachments: ProjectAttachment[];
  keys: ProjectKey[];
  models: ProjectModelsResponse;
  chats: ChatSidebarResponse | null;
}

export interface ProjectPatch {
  name?: string;
  about?: string | null;
  budget_tokens?: number | null;
  agent_provider?: string | null;
  resolved_ai_key_id?: string | null;
}

export interface ProjectRuntimeService {
  load(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectRuntimeSnapshot>;
  getProject(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectRuntimeProject>;
  getContainer(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectContainer | null>;
  getMetrics(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectMetricsResponse>;
  listWorkspace(projectId: string, path?: string, options?: { signal?: AbortSignal }): Promise<WorkspaceEntriesResponse>;
  previewWorkspace(projectId: string, path: string, options?: { signal?: AbortSignal }): Promise<WorkspacePreviewResponse>;
  downloadWorkspace(projectId: string, path: string, options?: { signal?: AbortSignal }): Promise<WorkspaceDownload>;
  listAttachments(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectAttachment[]>;
  listKeys(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectKey[]>;
  listModels(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectModelsResponse>;
  listChats(cabinetId: string, options?: { signal?: AbortSignal }): Promise<ChatSidebarResponse>;
  createProject?(cabinetId: string, name: string, agentProvider?: string | null): Promise<ProjectRuntimeProject>;
  createModelSession?(projectId: string, modelId: string): Promise<ProjectRuntimeAgentSession>;
  patchProject(projectId: string, patch: ProjectPatch): Promise<ProjectRuntimeProject>;
  runAction(projectId: string, action: RuntimeAction): Promise<ProjectRuntimeProject | Record<string, unknown>>;
}

export interface ProjectRuntimeProps {
  projectId: string;
  service?: ProjectRuntimeService;
  initialSnapshot?: ProjectRuntimeSnapshot;
  initialTab?: RuntimeTab;
  onNavigate: (tab: RuntimeTab, projectId: string, sessionId?: string) => void;
  /** Host chat surface persists the selected live model for the next session. */
  onModelChange: (projectId: string, modelId: string) => void | Promise<void>;
}
