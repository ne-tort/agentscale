export type Id = string;
export type JsonObject = Record<string, unknown>;
export type ProjectLifecycle = "draft" | "active" | "paused" | "error" | "completed" | "deleted";
export type ContainerStatus = "unknown" | "preparing" | "running" | "stopping" | "stopped" | "failed" | string;
export type AgentSessionStatus = "active" | "suspended" | "closed" | "cancelled" | string;
export type SseEventType = string;

export interface ApiError { code: string; title: string; status: number; detail?: string; trace_id?: string; type?: string; [key: string]: unknown }
export interface SessionTokens { access_token: string; refresh_token: string; id_token?: string; expires_in?: number; token_type?: "Bearer" | string }
/** GET /auth/config uses auth_mode/features/brokers. */
export interface AuthConfig { auth_mode?: string; brokers?: string[]; features?: Record<string, boolean>; enabled?: boolean; providers?: string[]; [key: string]: unknown }
export interface Employee { id: Id; display_name: string; email?: string; contact_email?: string; status?: string; memberships?: Array<{ company_id: Id; company_name?: string; role?: string }> }
export interface Cabinet { id: Id; name: string; company_id?: Id; owner_employee_id?: Id; owner_scope?: string; status?: string; schema_name?: string; projects_count?: number; [key: string]: unknown }
export interface CurrentUserMembership { company_id: Id; company_name?: string; role?: string }
export interface CurrentUserCompany { id: Id; name?: string; username?: string; contact_email?: string | null }
export interface CurrentUserEmployee {
  id: Id; display_name?: string; email?: string | null; contact_email?: string | null; status?: string;
  memberships?: CurrentUserMembership[];
}
export interface CurrentUserWorkContext { cabinet_id?: Id | null; project_id?: Id | null }
/** GET /me wire shape. `active` is always a top-level principal-state value. */
export interface CurrentUserWireResponse {
  active: boolean; id?: Id; subject?: string; sub: string; email?: string | null; display_name?: string;
  roles: string[]; company_id?: Id; employee_id?: Id; memberships?: CurrentUserMembership[]; contours: string[];
  company: CurrentUserCompany | null;
  employee: CurrentUserEmployee | null;
  work_context: CurrentUserWorkContext;
}
/** `me()` normalizes the raw identity response to this canonical client projection. */
export interface CurrentUserResponse {
  id: Id; subject: string; email?: string; display_name?: string; roles: string[]; company_id?: Id; employee_id?: Id; active: boolean;
  sub?: string; company?: CurrentUserCompany | null; employee?: CurrentUserEmployee | null;
  memberships?: CurrentUserMembership[]; contours?: string[]; work_context?: CurrentUserWorkContext;
}
export type Project = ProjectSummary;
/** The project service returns richer fields than the skeletal OpenAPI DTO. */
export interface ProjectSummary {
  id: Id; cabinet_id: Id; name: string; status: ProjectLifecycle; company_id?: Id; owner_employee_id?: Id; created_by_employee_id?: Id; created_by_login?: string | null;
  slug?: string; visibility_mode?: string; workspace_key?: string; container_ref?: string | null; agent_provider?: string | null; about?: string | null;
  resolved_ai_key_id?: Id | null; budget_tokens?: number | null; workspace_outdated_at?: string | null; selected?: boolean; new_chat_enabled?: boolean;
  observed_state?: string | null; updated_at?: string | null; created_at?: string | null; limits?: JsonObject; company_subscription?: JsonObject; [key: string]: unknown;
}
export interface ProjectsResponse { items: ProjectSummary[]; selected_project_id?: Id | null; [key: string]: unknown }
export interface ProjectSelection { cabinet_id: Id; project_id?: Id | null; chat_session_id?: Id | null }
export interface ContainerStatusResponse {
  project_id: Id; project_name?: string; container_id?: Id; status: ContainerStatus; desired_state?: string; observed_state?: string; runtime_ref?: string;
  container_ref?: string | null; workspace_key?: string | null; runtime?: JsonObject | null; runtime_metrics?: JsonObject | null; k8s_phase?: string | null;
  last_error?: string | null; updated_at?: string | null; [key: string]: unknown;
}
export interface WorkspaceEntry { path: string; name: string; kind: "file" | "dir" | string; size?: number | null; modified_at?: string | null; content_type?: string | null; [key: string]: unknown }
export interface WorkspaceListing { path: string; entries: WorkspaceEntry[] }
export interface WorkspacePreview { path?: string; text?: string; content?: string; truncated?: boolean; [key: string]: unknown }
export interface WorkspaceMutationResponse extends JsonObject { ok?: boolean; path?: string }
export interface DownloadedFile { blob: Blob; filename: string; contentType: string; size: number }
export interface ChatSidebarItem {
  session_id: Id; project_id: Id; project_name: string; title?: string | null; pinned: boolean; has_draft: boolean; has_messages: boolean;
  last_message_at?: string | null; created_at?: string | null; status?: string; [key: string]: unknown;
}
export interface ChatSidebarProject { project_id: Id; project_name: string; active?: boolean; status?: string; new_chat_enabled: boolean; chats: ChatSidebarItem[]; [key: string]: unknown }
export interface ChatSidebarResponse {
  cabinet_id: Id; selected_project_id?: Id | null; selected_session_id?: Id | null; new_chat_enabled?: boolean; observed_state?: string | null;
  pinned?: ChatSidebarItem[]; projects: ChatSidebarProject[]; project_chats: ChatSidebarItem[]; [key: string]: unknown;
}
export interface AgentSession {
  id: Id; project_id: Id; resolved_key_id?: Id | null; provider?: string | null; api_kind?: string | null; vendor_agent_id?: string | null; model?: string | null;
  cwd?: string | null; status: AgentSessionStatus; title?: string | null; pinned?: boolean; max_turns?: number | null; last_message_at?: string | null;
  created_at?: string | null; agent_tokens_used?: number; agent_requests?: number; agent_messages?: number; [key: string]: unknown;
}
export interface AgentSessionsResponse { items: AgentSession[]; [key: string]: unknown }
export interface TranscriptBlock { kind: string; text?: string | null; seq?: number; created_at?: string | null; metadata: JsonObject; [key: string]: unknown }
export interface ChatTranscriptResponse {
  session_id: Id; session_status?: string; blocks: TranscriptBlock[]; pending_approvals?: ToolApproval[]; oldest_seq?: number | null; newest_seq?: number | null;
  has_more: boolean; turn_in_progress: boolean; [key: string]: unknown;
}
export interface ChatRunRequest { project_id: Id; session_id?: Id; text: string; turn_id?: string; model?: string; attachment_refs: string[] }
/** Existing backend stream format is {type,data}; keepalive is an SSE comment. */
export interface ChatStreamEvent { type: SseEventType; data: JsonObject; at?: string | null; seq?: number; created_at?: string | null; [key: string]: unknown }
export interface ChatStreamOptions { signal?: AbortSignal; onEvent?: (event: ChatStreamEvent) => void | Promise<void> }
export interface ChatSendResponse { session_id: Id; events?: ChatStreamEvent[]; assistant_text?: string; pending_approvals?: ToolApproval[]; [key: string]: unknown }
export interface ComposerDraft { text: string; session_id?: Id | null; updated_at?: string | null; has_draft: boolean; [key: string]: unknown }
export interface ChatAttachment { id: Id; filename: string; content_type?: string | null; size_bytes?: number; storage_ref?: string; content_asset_id?: Id | null; created_at?: string | null; [key: string]: unknown }
/** POST /projects/{id}/attachments accepts JSON base64; it is not multipart. */
export interface AttachmentUpload { filename: string; contentBase64: string; contentType?: string }
export interface ToolApproval { id: string; name: string; input?: unknown; [key: string]: unknown }
export interface ToolApprovalDecisionResponse extends JsonObject { session_id?: Id; approval_id?: string; decision?: "approve" | "deny"; events?: ChatStreamEvent[]; assistant_text?: string }
export interface ProjectTrigger { id: Id; project_id: Id; kind: string; payload: JsonObject; status: string; attempts?: number; lease_until?: string | null; leased_by?: string | null; available_at?: string | null; last_error?: string | null; created_at?: string | null; [key: string]: unknown }
export interface TriggerListResponse { items: ProjectTrigger[] }
export interface ChatErrorPolicy { interval_sec: number; max_attempts: number; fallback_models: string[]; [key: string]: unknown }
export interface ModelCatalogEntry extends JsonObject { id?: Id; name?: string; model_ids?: string[]; provider?: string; reasoning_level?: string }
export interface ProjectModelsLiveResponse extends JsonObject { models: unknown[]; default_model?: string | null; source?: string }

export interface ProjectSchedule extends JsonObject {
  id: Id; project_id: Id; name: string; cron: string; timezone: string; payload: JsonObject; enabled: boolean;
  next_run_at?: string | null; last_run_at?: string | null; created_at?: string | null; updated_at?: string | null;
}
export interface ProjectScheduleInput { name: string; cron: string; timezone?: string; payload?: JsonObject; enabled?: boolean }
export type ProjectSchedulePatch = Partial<ProjectScheduleInput>;
export interface OutgoingWebhook extends JsonObject {
  id: Id; url: string; events: string[]; project_id?: Id | null; enabled: boolean; fail_count?: number; created_at?: string | null;
}
export interface CreateOutgoingWebhook { url: string; events: string[]; project_id?: Id }
export interface CreatedOutgoingWebhook extends JsonObject {
  id: Id; url: string; events: string[]; project_id?: Id | null; secret: string;
}
export interface WebhookDelivery extends JsonObject {
  id: Id; subscription_id: Id; url: string; subscription_enabled: boolean; event_type: string; status: string; attempts: number;
  retry_history: JsonObject[]; response_status?: number | null; last_error?: string | null;
  created_at?: string | null; next_attempt_at?: string | null; delivered_at?: string | null;
}
export interface ProjectRuntimeModule extends JsonObject { id: Id; module_id?: Id; name?: string; [key: string]: unknown }
export interface ProjectModuleSettings extends JsonObject {
  module_id: Id; name: string; enabled: boolean; bind_kind?: "local" | "global" | null; child_may_edit?: boolean | null;
  has_profiles: boolean; profile_table?: string | null; profile_id?: Id | null; profile_name?: string | null;
  profiles?: Array<{ profile_id: Id; name: string; selected: boolean }>;
}
export interface ProjectModulesSettingsResponse extends JsonObject { module_ids: Id[]; items: ProjectModuleSettings[] }
export interface ModuleMetaDocument extends JsonObject { slug: string; body: unknown }
export interface CabinetBoundModule extends JsonObject { id: Id; name: string; status: string; bind_kind: "local" | "global"; child_may_edit: boolean }
export interface ModuleDataQuery { page?: number; page_size?: number; search?: string; in_stock_only?: boolean; chats?: "all" | string }
export interface ModuleDataRows extends JsonObject { items: JsonObject[] }
export interface ModuleActionInvocation { row_id?: Id | null; project_id?: Id; params?: JsonObject }
export interface ModuleSecretUpload { secret: string; label?: string }
export interface ModuleFileReference { asset_id: Id; blob_version_id?: Id; version_id?: Id; storage_key?: string | null; filename?: string }
export interface UploadedModuleFile extends ModuleFileReference { size: number; sha256: string }
export interface ContentAsset extends JsonObject { id: Id; title?: string | null; mime?: string | null; visibility?: string; tags?: string[] }
export interface ContentAssetVersion extends JsonObject { id: Id; asset_id?: Id; version?: number; storage_key?: string; size?: number; sha256?: string | null }
export interface ContentAssetVersionUpload extends JsonObject { version: ContentAssetVersion; upload_url: string; method: "PUT" | string }
export interface AdminProfile extends JsonObject { sub?: string; username?: string; password_set?: boolean }
export interface CompanyModule extends JsonObject { id: Id; name: string; company_id?: Id; [key: string]: unknown }
export interface AdminModule extends JsonObject { id: Id; name: string; [key: string]: unknown }
export interface ModuleBinding extends JsonObject { module_id?: Id; project_id: Id; cabinet_id?: Id; bind_kind?: "local" | "global"; status?: string }

export interface ApiClient {
  authConfig(): Promise<AuthConfig>; login(username: string, password: string): Promise<SessionTokens>; refresh(refreshToken: string): Promise<SessionTokens>; logout(tokens: Partial<SessionTokens>): Promise<void>; me(): Promise<CurrentUserResponse>;
  listCabinets(): Promise<Cabinet[]>; getCabinet(cabinetId: Id): Promise<Cabinet>; createCabinet(body: { name: string; company_id: Id; base_template?: string }): Promise<Cabinet>;
  renameCabinet(cabinetId: Id, name: string): Promise<Cabinet>; archiveCabinet(cabinetId: Id): Promise<JsonObject>; deleteCabinet(cabinetId: Id): Promise<JsonObject>;
  restoreCabinet(cabinetId: Id): Promise<JsonObject>; purgeCabinet(cabinetId: Id): Promise<JsonObject>; uploadCabinetContent(cabinetId: Id, file: Blob, filename?: string): Promise<UploadedModuleFile>;
  getSelection(cabinetId: Id): Promise<ProjectSelection>; setSelection(cabinetId: Id, selection: { project_id?: Id | null; chat_session_id?: Id | null }): Promise<ProjectSelection>; chatSidebar(cabinetId: Id): Promise<ChatSidebarResponse>;
  listProjects(cabinetId: Id): Promise<ProjectsResponse>; createProject(cabinetId: Id, body: { name: string; agent_provider?: string | null }): Promise<ProjectSummary>;
  getProject(projectId: Id): Promise<ProjectSummary>; patchProject(projectId: Id, patch: { name?: string; about?: string | null; budget_tokens?: number | null; agent_provider?: string | null; resolved_ai_key_id?: Id | null }): Promise<ProjectSummary>;
  updateProject(projectId: Id, patch: { name?: string; about?: string | null; budget_tokens?: number | null; agent_provider?: string | null; resolved_ai_key_id?: Id | null }): Promise<ProjectSummary>;
  deleteProject(projectId: Id): Promise<JsonObject>; restoreProject(projectId: Id): Promise<JsonObject>; purgeProject(projectId: Id): Promise<JsonObject>;
  projectLifecycle(projectId: Id, action: "launch" | "pause" | "resume" | "complete" | "reload" | "sync" | "materialize" | "dismiss-workspace-outdated" | "reset-agent"): Promise<JsonObject>;
  launchProject(projectId: Id): Promise<JsonObject>; pauseProject(projectId: Id): Promise<JsonObject>; resumeProject(projectId: Id): Promise<JsonObject>; completeProject(projectId: Id): Promise<JsonObject>; reloadProject(projectId: Id): Promise<JsonObject>;
  syncProject(projectId: Id): Promise<JsonObject>; materializeProject(projectId: Id): Promise<JsonObject>; dismissWorkspaceOutdated(projectId: Id): Promise<JsonObject>; resetProjectAgent(projectId: Id): Promise<JsonObject>;
  containerStatus(projectId: Id): Promise<ContainerStatusResponse>; projectMetrics(projectId: Id): Promise<JsonObject>; containerMetrics(projectId: Id, window?: string): Promise<JsonObject>; listWorkspaceEntries(projectId: Id, path?: string): Promise<WorkspaceListing>; previewWorkspace(projectId: Id, path: string): Promise<WorkspacePreview>; uploadWorkspace(projectId: Id, path: string, body: BodyInit): Promise<WorkspaceMutationResponse>;
  downloadWorkspace(projectId: Id, path: string): Promise<DownloadedFile>; deleteWorkspaceEntry(projectId: Id, path: string): Promise<WorkspaceMutationResponse>; moveWorkspaceEntry(projectId: Id, src: string, dst: string): Promise<WorkspaceMutationResponse>; copyWorkspaceEntry(projectId: Id, src: string, dst: string): Promise<WorkspaceMutationResponse>;
  setProjectVisibility(projectId: Id, visibility_mode: string): Promise<JsonObject>; listProjectModules(projectId: Id): Promise<ProjectModulesSettingsResponse>; getProjectModule(projectId: Id, moduleId: Id): Promise<ProjectModuleSettings>; setProjectModuleProfile(projectId: Id, moduleId: Id, profile_id: string): Promise<ProjectModuleSettings>; setProjectModules(projectId: Id, module_ids: Id[]): Promise<JsonObject>;
  listProjectWorkspaceEntries(projectId: Id, path?: string): Promise<WorkspaceListing>; previewProjectWorkspace(projectId: Id, path: string): Promise<WorkspacePreview>; downloadProjectWorkspace(projectId: Id, path: string): Promise<DownloadedFile>;
  listSessions(projectId: Id): Promise<AgentSessionsResponse>; createSession(projectId: Id, body?: { model?: string; title?: string }): Promise<AgentSession>; getSession(projectId: Id, sessionId: Id): Promise<AgentSession>;
  patchSession(projectId: Id, sessionId: Id, patch: { title?: string | null; pin?: boolean; max_turns?: number | null }): Promise<AgentSession>; deleteSession(projectId: Id, sessionId: Id): Promise<JsonObject>; forkSession(projectId: Id, sessionId: Id): Promise<AgentSession>; cancelSession(projectId: Id, sessionId: Id): Promise<AgentSession>;
  sendMessage(projectId: Id, sessionId: Id, body: { text: string; attachment_refs?: string[]; model?: string }): Promise<ChatSendResponse>; getTranscript(projectId: Id, sessionId: Id, options?: { beforeSeq?: number; afterSeq?: number; limit?: number }): Promise<ChatTranscriptResponse>; chatTranscript(projectId: Id, sessionId: Id, options?: { beforeSeq?: number; afterSeq?: number; limit?: number }): Promise<ChatTranscriptResponse>;
  runChat(request: ChatRunRequest, options?: ChatStreamOptions): AsyncIterable<ChatStreamEvent>; getSessionTranscript(projectId: Id, sessionId: Id): Promise<ChatTranscriptResponse>;
  listPendingApprovals(projectId: Id, sessionId: Id): Promise<{ items: ToolApproval[] }>; resolveApproval(projectId: Id, sessionId: Id, id: string, decision: "approve" | "deny"): Promise<ToolApprovalDecisionResponse>;
  getDraft(projectId: Id, sessionId?: Id): Promise<ComposerDraft>; saveDraft(projectId: Id, text: string, sessionId?: Id): Promise<ComposerDraft>; clearDraft(projectId: Id, sessionId?: Id): Promise<JsonObject>;
  listSessionEvents(projectId: Id, sessionId: Id): Promise<{ items: ChatStreamEvent[] }>; appendSessionEvent(projectId: Id, sessionId: Id, event: { type: string; data?: JsonObject; at?: string }): Promise<ChatStreamEvent>;
  listAttachments(projectId: Id): Promise<{ items: ChatAttachment[] }>; uploadAttachment(projectId: Id, upload: AttachmentUpload): Promise<ChatAttachment>; deleteAttachment(projectId: Id, attachmentId: Id): Promise<JsonObject>; downloadAttachment(projectId: Id, attachmentId: Id): Promise<DownloadedFile>;
  listProjectAttachments(projectId: Id): Promise<{ items: ChatAttachment[] }>; uploadProjectAttachment(projectId: Id, upload: AttachmentUpload): Promise<ChatAttachment>; downloadProjectAttachment(projectId: Id, attachmentId: Id): Promise<DownloadedFile>;
  enqueueTrigger(projectId: Id, body: { kind: string; payload?: JsonObject }): Promise<ProjectTrigger>; enqueueScheduleTrigger(projectId: Id, payload?: JsonObject): Promise<ProjectTrigger>; listTriggers(projectId: Id): Promise<TriggerListResponse>; dispatchTriggers(projectId: Id, max?: number): Promise<JsonObject>; postTrigger(projectId: Id, body: { kind: string; payload?: JsonObject }): Promise<ProjectTrigger>;
  getChatErrorPolicy(projectId: Id): Promise<ChatErrorPolicy>; setChatErrorPolicy(projectId: Id, patch: Partial<ChatErrorPolicy>): Promise<ChatErrorPolicy>;
  listProjectModels(projectId: Id): Promise<ProjectModelsLiveResponse>; listProjectModelsLive(projectId: Id): Promise<ProjectModelsLiveResponse>; listProjectAiKeys(projectId: Id): Promise<{ items: JsonObject[] }>;
  listCompanyModels(companyId: Id): Promise<ModelCatalogEntry[]>; listCompanyKeys(companyId: Id): Promise<JsonObject[]>; listKeyModels(companyId: Id, keyId: Id): Promise<JsonObject[]>;
  listProjectSchedules(projectId: Id): Promise<{ items: ProjectSchedule[] }>; createProjectSchedule(projectId: Id, body: ProjectScheduleInput): Promise<ProjectSchedule>;
  patchProjectSchedule(projectId: Id, scheduleId: Id, patch: ProjectSchedulePatch): Promise<ProjectSchedule>; deleteProjectSchedule(projectId: Id, scheduleId: Id): Promise<void>;
  listOutgoingWebhooks(projectId?: Id): Promise<{ items: OutgoingWebhook[] }>; createOutgoingWebhook(body: CreateOutgoingWebhook): Promise<CreatedOutgoingWebhook>;
  setOutgoingWebhookEnabled(subscriptionId: Id, enabled: boolean): Promise<Pick<OutgoingWebhook, "id" | "enabled">>; deleteOutgoingWebhook(subscriptionId: Id): Promise<JsonObject>;
  listWebhookDeliveries(limit?: number): Promise<{ items: WebhookDelivery[] }>; retryWebhookDelivery(deliveryId: Id): Promise<JsonObject>;
  listProjectRuntimeModules(projectId: Id): Promise<{ items: ProjectRuntimeModule[] }>; getProjectRuntimeModuleMeta(projectId: Id, moduleId: Id, slug: string): Promise<ModuleMetaDocument>;
  listProjectRuntimeModuleData(projectId: Id, moduleId: Id, tableSlug: string, query?: ModuleDataQuery, sessionId?: Id): Promise<ModuleDataRows>;
  createProjectRuntimeModuleData(projectId: Id, moduleId: Id, tableSlug: string, body: JsonObject, sessionId?: Id): Promise<JsonObject>;
  patchProjectRuntimeModuleData(projectId: Id, moduleId: Id, tableSlug: string, rowId: Id, body: JsonObject, sessionId?: Id): Promise<JsonObject>;
  deleteProjectRuntimeModuleData(projectId: Id, moduleId: Id, tableSlug: string, rowId: Id): Promise<JsonObject>;
  bindProjectModule(projectId: Id, moduleId: Id, body?: { bind_kind?: "local" | "global"; child_may_edit?: boolean }): Promise<ModuleBinding>;
  unbindProjectModule(projectId: Id, moduleId: Id): Promise<JsonObject>;
  invokeProjectModuleAction(cabinetId: Id, projectId: Id, moduleId: Id, actionId: Id, body?: ModuleActionInvocation, sessionId?: Id): Promise<JsonObject>;
  uploadProjectModuleContent(cabinetId: Id, file: Blob, filename?: string): Promise<UploadedModuleFile>; downloadProjectModuleFile(fileRef: ModuleFileReference): Promise<DownloadedFile>;
  listCabinetModules(cabinetId: Id): Promise<{ items: CabinetBoundModule[] }>;
  listCabinetModuleBoundProjects(cabinetId: Id, moduleId: Id): Promise<{ items: Array<{ project_id: Id; name: string }> }>;
  getCabinetModuleMeta(cabinetId: Id, moduleId: Id, slug: string): Promise<ModuleMetaDocument>;
  listCabinetModuleData(cabinetId: Id, moduleId: Id, tableSlug: string, query?: ModuleDataQuery, sessionId?: Id): Promise<ModuleDataRows>;
  createCabinetModuleData(cabinetId: Id, moduleId: Id, tableSlug: string, body: JsonObject, sessionId?: Id): Promise<JsonObject>;
  patchCabinetModuleData(cabinetId: Id, moduleId: Id, tableSlug: string, rowId: Id, body: JsonObject, sessionId?: Id): Promise<JsonObject>;
  deleteCabinetModuleData(cabinetId: Id, moduleId: Id, tableSlug: string, rowId: Id, sessionId?: Id): Promise<JsonObject>;
  invokeCabinetModuleAction(cabinetId: Id, moduleId: Id, actionId: Id, body: ModuleActionInvocation, sessionId?: Id): Promise<JsonObject>;
  uploadCabinetModuleSecret(cabinetId: Id, moduleId: Id, body: ModuleSecretUpload): Promise<JsonObject>;
  listCompanyModules(companyId: Id): Promise<{ items: CompanyModule[] }>; createCompanyModule(companyId: Id, name: string): Promise<CompanyModule>;
  getCompanyModule(companyId: Id, moduleId: Id): Promise<CompanyModule>; patchCompanyModule(companyId: Id, moduleId: Id, patch: { name?: string; cabinet_ids?: Id[] }): Promise<CompanyModule>;
  deleteCompanyModule(companyId: Id, moduleId: Id): Promise<JsonObject>; copyCompanyModule(companyId: Id, moduleId: Id, name?: string): Promise<CompanyModule>;
  listCompanyModuleProjects(companyId: Id, moduleId: Id): Promise<{ items: ModuleBinding[] }>; listCompanyModuleCabinets(companyId: Id, moduleId: Id): Promise<{ items: JsonObject[] }>;
  listCompanyModuleMeta(companyId: Id, moduleId: Id): Promise<{ items: ModuleMetaDocument[] }>; getCompanyModuleMeta(companyId: Id, moduleId: Id, slug: string): Promise<ModuleMetaDocument>;
  putCompanyModuleMeta(companyId: Id, moduleId: Id, slug: string, body: unknown): Promise<ModuleMetaDocument>;
  listCompanyModuleData(companyId: Id, moduleId: Id, tableSlug: string): Promise<ModuleDataRows>; createCompanyModuleData(companyId: Id, moduleId: Id, tableSlug: string, body: JsonObject): Promise<JsonObject>;
  patchCompanyModuleData(companyId: Id, moduleId: Id, tableSlug: string, rowId: Id, body: JsonObject): Promise<JsonObject>; deleteCompanyModuleData(companyId: Id, moduleId: Id, tableSlug: string, rowId: Id): Promise<JsonObject>;
  invokeCompanyModuleAction(companyId: Id, moduleId: Id, actionId: Id, body: ModuleActionInvocation): Promise<JsonObject>;
  uploadCompanyModuleContent(companyId: Id, moduleId: Id, file: Blob, filename?: string): Promise<UploadedModuleFile>; uploadCompanyModuleSecret(companyId: Id, moduleId: Id, body: ModuleSecretUpload): Promise<JsonObject>;
  listAdminModules(): Promise<{ items: AdminModule[] }>; createAdminModule(name: string): Promise<AdminModule>; getAdminModule(moduleId: Id): Promise<AdminModule>;
  patchAdminModule(moduleId: Id, patch: { name?: string; company_ids?: Id[]; cabinet_ids?: Id[] }): Promise<AdminModule>; deleteAdminModule(moduleId: Id): Promise<JsonObject>; copyAdminModule(moduleId: Id, name?: string): Promise<AdminModule>;
  listAdminModuleMeta(moduleId: Id): Promise<{ items: ModuleMetaDocument[] }>; getAdminModuleMeta(moduleId: Id, slug: string): Promise<ModuleMetaDocument>; putAdminModuleMeta(moduleId: Id, slug: string, body: unknown): Promise<ModuleMetaDocument>; deleteAdminModuleMeta(moduleId: Id, slug: string): Promise<JsonObject>;
  listAdminModuleProjects(moduleId: Id): Promise<{ items: ModuleBinding[] }>; listAdminModuleCabinets(moduleId: Id): Promise<{ items: JsonObject[] }>;
  bindAdminProjectModule(moduleId: Id, projectId: Id, body?: { bind_kind?: "local" | "global"; child_may_edit?: boolean }): Promise<ModuleBinding>; unbindAdminProjectModule(moduleId: Id, projectId: Id): Promise<JsonObject>;
  listAdminModuleData(moduleId: Id, tableSlug: string): Promise<ModuleDataRows>; createAdminModuleData(moduleId: Id, tableSlug: string, body: JsonObject): Promise<JsonObject>; patchAdminModuleData(moduleId: Id, tableSlug: string, rowId: Id, body: JsonObject): Promise<JsonObject>; deleteAdminModuleData(moduleId: Id, tableSlug: string, rowId: Id): Promise<JsonObject>;
  invokeAdminModuleAction(moduleId: Id, actionId: Id, body: ModuleActionInvocation): Promise<JsonObject>; uploadAdminModuleContent(moduleId: Id, file: Blob, filename?: string): Promise<UploadedModuleFile>; uploadAdminModuleSecret(moduleId: Id, body: ModuleSecretUpload): Promise<JsonObject>;
  getAdminProfile(): Promise<AdminProfile>; setAdminLogin(login: string): Promise<AdminProfile>; setAdminPassword(password: string): Promise<JsonObject>;
  listContentAssets(companyId: Id, limit?: number): Promise<{ items: ContentAsset[] }>; createContentAsset(body: { owner_company_id?: Id; visibility?: string; mime?: string; title?: string; tags?: string[] }): Promise<ContentAsset>;
  getContentAsset(assetId: Id): Promise<ContentAsset>; patchContentAsset(assetId: Id, patch: { title?: string | null; tags?: string[]; visibility?: string }): Promise<ContentAsset>; deleteContentAsset(assetId: Id): Promise<JsonObject>;
  beginContentAssetVersion(assetId: Id, body?: { mime?: string; ttl_seconds?: number }): Promise<ContentAssetVersionUpload>; uploadContentAssetVersion(assetId: Id, versionId: Id, file: BodyInit, contentType?: string): Promise<ContentAssetVersion>;
  finalizeContentAssetVersion(assetId: Id, versionId: Id): Promise<JsonObject>; downloadContentAsset(assetId: Id, options?: { blob_version_id?: Id; proxy?: boolean }): Promise<DownloadedFile>;
  getProjectChatErrorPolicy(projectId: Id): Promise<ChatErrorPolicy>; setProjectChatErrorPolicy(projectId: Id, patch: Partial<ChatErrorPolicy>): Promise<ChatErrorPolicy>;
  /** Compatibility alias used by the existing project overview feature. */ lifecycle(projectId: Id, action: "launch" | "pause" | "resume" | "complete" | "reload"): Promise<void>;
}
