export type ModuleId = string;
export type ModuleViewKind = "hub" | "profile_hub" | "collection" | "form" | "detail";
export type ModuleBindKind = "local" | "global";
export type ModulePlacement = "rail" | "management" | "data" | "none";
export type ModuleChatScope = "all" | "current";
export type JsonRecord = Record<string, unknown>;

export interface ModuleRecord {
  readonly id: ModuleId;
  readonly name: string;
  readonly status?: string;
  readonly bind_kind: ModuleBindKind;
  readonly child_may_edit: boolean;
  readonly instance_id?: string | null;
}

export interface ModuleTableDefinition {
  readonly slug: string;
  readonly label?: MetaLabel;
  readonly enabled?: boolean;
  readonly storage_kind?: string;
  readonly scope?: { readonly projects?: string; readonly chats?: ModuleChatScope };
  readonly [key: string]: unknown;
}

export interface ModuleColumnDefinition {
  readonly table_slug: string;
  readonly name: string;
  readonly label?: MetaLabel;
  readonly type: "text" | "number" | "bool" | "datetime" | "json" | "enum" | "ref" | "file_ref" | "secret_ref";
  readonly required?: boolean;
  readonly read_only?: boolean;
  readonly default?: unknown;
  readonly enum?: { readonly values?: readonly string[]; readonly labels?: Readonly<Record<string, MetaLabel>> };
  readonly ui?: JsonRecord;
  readonly [key: string]: unknown;
}

export type MetaLabel = string | { readonly ru?: string; readonly en?: string; readonly [key: string]: unknown };

export interface ModuleViewDefinition {
  readonly slug: string;
  readonly table_slug?: string;
  readonly kind: ModuleViewKind;
  readonly label?: MetaLabel;
  readonly enabled?: boolean;
  readonly ui_json: JsonRecord;
  readonly [key: string]: unknown;
}

export interface ModuleTabDefinition {
  readonly id: string;
  readonly title: MetaLabel;
  readonly subtitle?: MetaLabel;
  readonly order?: number;
  readonly icon?: string;
  readonly view_slug: string;
  readonly table_slug?: string;
  readonly enabled?: boolean;
  readonly default_project_bind?: ModuleBindKind;
  readonly nav?: { readonly contour?: string; readonly placement?: ModulePlacement };
  readonly [key: string]: unknown;
}

export interface ModuleActionDefinition {
  readonly id: string;
  readonly label?: MetaLabel;
  readonly kind: string;
  readonly enabled?: boolean;
  readonly params?: JsonRecord;
  readonly [key: string]: unknown;
}

export interface ModuleManifest {
  readonly syntax_version: number;
  readonly tables: readonly ModuleTableDefinition[];
  readonly columns: readonly ModuleColumnDefinition[];
  readonly views: readonly ModuleViewDefinition[];
  readonly tabs: readonly ModuleTabDefinition[];
  readonly actions: readonly ModuleActionDefinition[];
  readonly invalid_views?: Readonly<Record<string, string>>;
  readonly warnings?: readonly string[];
}

export interface ModuleRuntimeScope {
  readonly projectId: string;
  readonly cabinetId: string;
  readonly moduleId: ModuleId;
  readonly moduleName: string;
  readonly bindKind: ModuleBindKind;
  readonly childMayEdit: boolean;
  readonly readOnly: boolean;
  readonly sessionId?: string;
}

export interface ModuleRow {
  readonly row_id: string;
  readonly body: JsonRecord;
  readonly session_id?: string | null;
  readonly [key: string]: unknown;
}

export interface ModulePage {
  readonly items: readonly ModuleRow[];
  readonly total: number;
  readonly page: number;
  readonly page_size: number;
}

export interface ModuleListQuery {
  readonly page?: number;
  readonly pageSize?: number;
  readonly search?: string;
  readonly chats?: "all";
  readonly filters?: Readonly<Record<string, unknown>>;
}

export interface ModuleProjectOption {
  readonly project_id: string;
  readonly name: string;
}

export interface ModuleFileRef {
  readonly asset_id?: string | null;
  readonly version_id?: string | null;
  readonly blob_version_id?: string | null;
  readonly storage_key?: string | null;
  readonly filename?: string | null;
  readonly size?: number | null;
  readonly sha256?: string | null;
  readonly [key: string]: unknown;
}

export interface ModuleActionResult {
  readonly kind?: string;
  readonly ok?: boolean;
  readonly status?: string;
  readonly message?: string;
  readonly detail?: string;
  readonly [key: string]: unknown;
  readonly file_ref?: ModuleFileRef;
}

export interface ModuleRuntimeService {
  listProjectModules(projectId: string, options?: { signal?: AbortSignal }): Promise<readonly ModuleRecord[]>;
  loadModule(projectId: string, moduleId: string, sessionId?: string, options?: { signal?: AbortSignal }): Promise<{ scope: ModuleRuntimeScope; manifest: ModuleManifest }>;
  listRows(scope: ModuleRuntimeScope, tableSlug: string, query?: ModuleListQuery, options?: { signal?: AbortSignal }): Promise<ModulePage>;
  createRow(scope: ModuleRuntimeScope, tableSlug: string, body: JsonRecord): Promise<ModuleRow>;
  patchRow(scope: ModuleRuntimeScope, tableSlug: string, rowId: string, body: JsonRecord): Promise<ModuleRow>;
  deleteRow(scope: ModuleRuntimeScope, tableSlug: string, rowId: string): Promise<void>;
  invokeAction(scope: ModuleRuntimeScope, actionId: string, input?: { rowId?: string; params?: JsonRecord }): Promise<ModuleActionResult>;
  uploadContent(scope: ModuleRuntimeScope, file: File): Promise<ModuleFileRef>;
  uploadSecret(scope: ModuleRuntimeScope, secret: string, label?: string): Promise<JsonRecord>;
  downloadFile(scope: ModuleRuntimeScope, ref: ModuleFileRef): Promise<{ blob: Blob; filename: string }>;
  listBoundProjects(scope: ModuleRuntimeScope, options?: { signal?: AbortSignal }): Promise<ModuleProjectOption[]>;
  setProfile(scope: ModuleRuntimeScope, profileId: string): Promise<JsonRecord>;
}

export interface ModuleRuntimeServiceOptions {
  readonly baseUrl?: string;
  readonly getAccessToken?: () => string | undefined;
  readonly fetcher?: typeof fetch;
}

export interface ModuleRuntimeProps {
  readonly projectId: string;
  readonly moduleId: string;
  readonly sessionId?: string;
  readonly service?: ModuleRuntimeService;
  readonly initialRuntime?: ModuleRuntimeLoadResult;
  readonly initialViewSlug?: string;
  readonly onBack?: () => void;
  readonly onOpenProject?: (projectId: string) => void;
  readonly slots?: ModuleSlotRegistry;
  readonly showNavigation?: boolean;
}

export interface ModuleSlotProps {
  readonly scope: ModuleRuntimeScope;
  readonly manifest: ModuleManifest;
  readonly view: ModuleViewDefinition;
  readonly row?: ModuleRow;
  readonly onRefresh: () => void;
}

export type ModuleSlotRenderer = (props: ModuleSlotProps) => import("react").ReactNode;

/** Domain-specific UI can only extend named slots in an allowlisted view. */
export interface ModuleSlotRegistry {
  resolve(moduleId: string, viewSlug: string, slot: string): ModuleSlotRenderer | undefined;
}

export interface ModuleRuntimeLoadResult {
  readonly scope: ModuleRuntimeScope;
  readonly manifest: ModuleManifest;
}

export type ModuleRuntimeRouteState =
  | { readonly viewSlug: string; readonly mode: "view"; readonly contextRowId?: string; readonly row?: ModuleRow; readonly returnTo?: { readonly viewSlug: string; readonly contextRowId?: string }; readonly notice?: string; readonly noticeUnknown?: boolean }
  | { readonly viewSlug: string; readonly mode: "create"; readonly contextRowId?: string; readonly initialBody?: JsonRecord; readonly row?: undefined; readonly returnTo?: { readonly viewSlug: string; readonly contextRowId?: string }; readonly notice?: string; readonly noticeUnknown?: boolean }
  | { readonly viewSlug: string; readonly mode: "edit"; readonly contextRowId?: string; readonly row: ModuleRow; readonly returnTo?: { readonly viewSlug: string; readonly contextRowId?: string }; readonly notice?: string; readonly noticeUnknown?: boolean };

export interface ModuleRuntimeErrorShape {
  readonly code?: string;
  readonly title?: string;
  readonly detail?: string;
  readonly status?: number;
  readonly trace_id?: string;
}
