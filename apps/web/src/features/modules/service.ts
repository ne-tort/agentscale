import type {
  JsonRecord,
  ModuleActionResult,
  ModuleFileRef,
  ModuleListQuery,
  ModuleManifest,
  ModulePage,
  ModuleProjectOption,
  ModuleRecord,
  ModuleRow,
  ModuleRuntimeScope,
  ModuleRuntimeService,
  ModuleRuntimeServiceOptions,
} from "./contracts";
import { AUTH_STORAGE_KEY } from "../../app/auth/lifecycle";
import { validateManifestDocuments, unwrapMetaDocument } from "./manifest";

export class ModuleRuntimeRequestError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly traceId?: string;

  constructor(message: string, details: { status?: number; code?: string; trace_id?: string } = {}) {
    super(message);
    this.name = "ModuleRuntimeRequestError";
    this.status = details.status;
    this.code = details.code;
    this.traceId = details.trace_id;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function normalizeRow(value: unknown): ModuleRow {
  if (!isRecord(value) || typeof value.row_id !== "string" || !value.row_id) {
    throw new ModuleRuntimeRequestError("Backend returned a malformed module row.", { code: "INVALID_MODULE_ROW" });
  }
  if ("body" in value) {
    if (!isRecord(value.body)) throw new ModuleRuntimeRequestError("Backend returned a malformed module row body.", { code: "INVALID_MODULE_ROW" });
    return value as unknown as ModuleRow;
  }
  // Virtual OpenSearch tables return row_id alongside flat column values.
  const { row_id, session_id, module_id, instance_id, ...body } = value;
  return { row_id, ...(typeof session_id === "string" || session_id === null ? { session_id } : {}), body, ...(module_id ? { module_id } : {}), ...(instance_id ? { instance_id } : {}) } as ModuleRow;
}

interface ParsedModulePage {
  readonly items: unknown[];
  readonly pagination?: { readonly total: number; readonly page: number; readonly page_size: number };
}

function parseModulePage(value: unknown, errorMessage: string): ParsedModulePage {
  if (Array.isArray(value)) return { items: value };
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new ModuleRuntimeRequestError(errorMessage, { code: "INVALID_MODULE_PAGE" });
  }

  const paginationFields = ["total", "page", "page_size"] as const;
  const hasPagination = paginationFields.some((field) => field in value);
  if (!hasPagination) return { items: value.items };

  const { total, page, page_size: pageSize } = value;
  if (
    !Number.isSafeInteger(total) || (total as number) < 0 ||
    !Number.isSafeInteger(page) || (page as number) < 1 ||
    !Number.isSafeInteger(pageSize) || (pageSize as number) < 1 || (pageSize as number) > 200 ||
    value.items.length > (total as number) || value.items.length > (pageSize as number)
  ) {
    throw new ModuleRuntimeRequestError(errorMessage, { code: "INVALID_MODULE_PAGE" });
  }
  return {
    items: value.items,
    pagination: { total: total as number, page: page as number, page_size: pageSize as number },
  };
}

function normalizePage(value: unknown, query: ModuleListQuery = {}): ModulePage {
  const page = Math.max(1, query.page ?? 1);
  const pageSize = Math.max(1, Math.min(query.pageSize ?? 50, 200));
  const parsed = parseModulePage(value, "Backend returned a malformed module row list.");
  let rows = parsed.items.map(normalizeRow);
  const search = (query.search ?? "").trim().toLocaleLowerCase();
  if (!parsed.pagination || query.filters) {
    if (query.filters) rows = rows.filter((row) => Object.entries(query.filters!).every(([key, expected]) => row.body[key] === expected));
    if (search) rows = rows.filter((row) => JSON.stringify(row.body).toLocaleLowerCase().includes(search));
  }
  if (parsed.pagination && !query.filters) {
    if (parsed.pagination.page !== page || parsed.pagination.page_size !== pageSize) {
      throw new ModuleRuntimeRequestError("Backend returned pagination metadata that does not match the requested page.", { code: "INVALID_MODULE_PAGE" });
    }
    return {
      items: rows,
      total: parsed.pagination.total,
      page: parsed.pagination.page,
      page_size: parsed.pagination.page_size,
    };
  }

  // The project route currently pages virtual tables; ordinary JSON tables return all rows.
  const start = (page - 1) * pageSize;
  return { items: rows.slice(start, start + pageSize), total: rows.length, page, page_size: pageSize };
}

function fileNameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded.replace(/^"|"$/g, "")); } catch { /* use standard filename */ }
  }
  return header.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

function storedAccessToken(): string | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const tokens: unknown = JSON.parse(window.localStorage.getItem(AUTH_STORAGE_KEY) ?? "null");
    return isRecord(tokens) && typeof tokens.access_token === "string" ? tokens.access_token : undefined;
  } catch { return undefined; }
}

export function createModuleRuntimeService(options: ModuleRuntimeServiceOptions = {}): ModuleRuntimeService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;
  const getToken = options.getAccessToken ?? storedAccessToken;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = getToken();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (cause) {
      if (init.signal?.aborted) throw cause;
      throw new ModuleRuntimeRequestError(cause instanceof Error ? `API connection failed: ${cause.message}` : "API connection failed.", { code: "NETWORK_ERROR" });
    }
    if (!response.ok) {
      let details: { status?: number; code?: string; title?: string; detail?: string; trace_id?: string } = { status: response.status, title: response.statusText };
      try {
        const body: unknown = await response.json();
        if (isRecord(body)) details = { ...details, ...body };
      } catch { /* Keep HTTP error details. */ }
      throw new ModuleRuntimeRequestError(details.detail ?? details.title ?? `API returned HTTP ${response.status}.`, details);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new ModuleRuntimeRequestError("API returned invalid JSON.", { status: response.status, code: "INVALID_JSON" }); }
  }

  function projectPath(projectId: string): string { return `/projects/${encode(projectId)}`; }
  function modulePath(scope: ModuleRuntimeScope): string {
    return `${projectPath(scope.projectId)}/runtime-modules/${encode(scope.moduleId)}`;
  }
  function sessionHeaders(scope: ModuleRuntimeScope): HeadersInit {
    return scope.sessionId ? { "X-Prodavan-Session-Id": scope.sessionId } : {};
  }
  function assertWritable(scope: ModuleRuntimeScope): void {
    if (scope.readOnly || !scope.childMayEdit) {
      throw new ModuleRuntimeRequestError("This module is bound globally and is read-only in this project.", { status: 403, code: "MODULE_READ_ONLY" });
    }
  }

  async function listProjectModules(projectId: string, requestOptions?: { signal?: AbortSignal }): Promise<readonly ModuleRecord[]> {
    const result = await request<unknown>(`${projectPath(projectId)}/runtime-modules`, { signal: requestOptions?.signal });
    if (!isRecord(result) || !Array.isArray(result.items)) throw new ModuleRuntimeRequestError("Backend returned a malformed project module list.", { code: "INVALID_MODULE_LIST" });
    return result.items.map((item) => {
      if (!isRecord(item)) throw new ModuleRuntimeRequestError("Backend returned a malformed project module entry.", { code: "INVALID_MODULE_BINDING" });
      const id = typeof item.module_id === "string" ? item.module_id : typeof item.id === "string" ? item.id : undefined;
      const bindKind = item.bind_kind === "local" || item.bind_kind === "global" ? item.bind_kind : undefined;
      if (!id || typeof item.name !== "string" || !bindKind || typeof item.child_may_edit !== "boolean") throw new ModuleRuntimeRequestError("Backend returned incomplete project binding flags.", { code: "INVALID_MODULE_BINDING" });
      return { id, name: item.name, bind_kind: bindKind, child_may_edit: item.child_may_edit, ...(typeof item.status === "string" ? { status: item.status } : {}), ...(typeof item.instance_id === "string" ? { instance_id: item.instance_id } : {}) };
    });
  }

  async function loadModule(projectId: string, moduleId: string, sessionId?: string, requestOptions?: { signal?: AbortSignal }): Promise<{ scope: ModuleRuntimeScope; manifest: ModuleManifest }> {
    const modules = await listProjectModules(projectId, requestOptions);
    const rawModule = modules.find((item) => item.id === moduleId);
    if (!rawModule) throw new ModuleRuntimeRequestError("This module is not bound to the selected project.", { status: 404, code: "MODULE_NOT_BOUND" });
    const bindKind = rawModule.bind_kind;
    const childMayEdit = rawModule.child_may_edit;
    const project = await request<unknown>(projectPath(projectId), { signal: requestOptions?.signal });
    if (!isRecord(project)) throw new ModuleRuntimeRequestError("Backend returned a malformed project.", { code: "INVALID_PROJECT_BINDING" });
    if (project.id !== projectId) throw new ModuleRuntimeRequestError("Backend returned a project different from the requested project.", { code: "PROJECT_MISMATCH" });
    if (typeof project.cabinet_id !== "string" || !project.cabinet_id) throw new ModuleRuntimeRequestError("Project is missing its cabinet binding.", { code: "INVALID_PROJECT_BINDING" });
    const scope: ModuleRuntimeScope = {
      projectId,
      cabinetId: project.cabinet_id,
      moduleId,
      moduleName: typeof rawModule.name === "string" ? rawModule.name : moduleId,
      bindKind,
      childMayEdit,
      readOnly: bindKind === "global" && !childMayEdit,
      ...(sessionId ? { sessionId } : {}),
    };
    const slugs = ["tables", "columns", "views", "tabs", "actions"] as const;
    const documents: Record<string, unknown> = {};
    await Promise.all(slugs.map(async (slug) => {
      try {
        documents[slug] = await request<unknown>(`${modulePath(scope)}/meta/documents/${slug}`, { signal: requestOptions?.signal });
      } catch (error) {
        if (slug === "actions" && error instanceof ModuleRuntimeRequestError && error.status === 404) {
          documents[slug] = [];
          return;
        }
        throw error;
      }
    }));
    const checked = validateManifestDocuments(documents);
    if (!checked.ok || !checked.manifest) throw new ModuleRuntimeRequestError(checked.error ?? "Module manifest is invalid.", { code: "INVALID_MODULE_META" });
    return { scope, manifest: checked.manifest };
  }

  async function listRows(scope: ModuleRuntimeScope, tableSlug: string, query: ModuleListQuery = {}, requestOptions?: { signal?: AbortSignal }): Promise<ModulePage> {
    const pageSize = Math.max(1, Math.min(query.pageSize ?? 50, 200));
    const baseParams = new URLSearchParams({ page: String(query.filters ? 1 : Math.max(1, query.page ?? 1)), page_size: String(query.filters ? 200 : pageSize) });
    if (query.search?.trim()) baseParams.set("search", query.search.trim());
    if (query.chats === "all") baseParams.set("chats", "all");
    const path = `${modulePath(scope)}/data/${encode(tableSlug)}`;
    if (!query.filters) {
      const result = await request<unknown>(`${path}?${baseParams}`, { signal: requestOptions?.signal, headers: sessionHeaders(scope) });
      return normalizePage(result, query);
    }

    const accumulated: unknown[] = [];
    let page = 1;
    let total: number | undefined;
    let backendPageSize: number | undefined;
    while (page <= 100) {
      const params = new URLSearchParams(baseParams);
      params.set("page", String(page));
      const result = await request<unknown>(`${path}?${params}`, { signal: requestOptions?.signal, headers: sessionHeaders(scope) });
      const parsed = parseModulePage(result, "Backend returned a malformed filtered row page.");
      if (!parsed.pagination) {
        if (page > 1) throw new ModuleRuntimeRequestError("Backend omitted pagination metadata while listing filtered rows.", { code: "INVALID_MODULE_PAGE" });
        accumulated.push(...parsed.items);
        break;
      }
      if (parsed.pagination.page !== page || (backendPageSize !== undefined && parsed.pagination.page_size !== backendPageSize)) {
        throw new ModuleRuntimeRequestError("Backend returned inconsistent pagination metadata for filtered rows.", { code: "INVALID_MODULE_PAGE" });
      }
      if (total !== undefined && parsed.pagination.total !== total) {
        throw new ModuleRuntimeRequestError("Backend changed the filtered row total between pages.", { code: "INVALID_MODULE_PAGE" });
      }
      backendPageSize = parsed.pagination.page_size;
      total = parsed.pagination.total;
      accumulated.push(...parsed.items);
      if (accumulated.length > total) {
        throw new ModuleRuntimeRequestError("Backend returned more filtered rows than its reported total.", { code: "INVALID_MODULE_PAGE" });
      }
      if (accumulated.length >= total) break;
      if (parsed.items.length < parsed.pagination.page_size) {
        throw new ModuleRuntimeRequestError("Backend ended filtered pagination before reaching its reported total.", { code: "INVALID_MODULE_PAGE" });
      }
      page += 1;
    }
    if (total !== undefined && accumulated.length < total && page > 100) {
      throw new ModuleRuntimeRequestError("Filtered result exceeds the 20,000-row client filter limit. Narrow the search first.", { code: "MODULE_FILTER_LIMIT", status: 413 });
    }
    return normalizePage({ items: accumulated }, query);
  }

  async function createRow(scope: ModuleRuntimeScope, tableSlug: string, body: JsonRecord): Promise<ModuleRow> {
    assertWritable(scope);
    const row = normalizeRow(await request<unknown>(`${modulePath(scope)}/data/${encode(tableSlug)}`, {
      method: "POST", body: JSON.stringify({ body }), headers: sessionHeaders(scope),
    }));
    assertMutationScope(scope, row);
    return row;
  }

  async function patchRow(scope: ModuleRuntimeScope, tableSlug: string, rowId: string, body: JsonRecord): Promise<ModuleRow> {
    assertWritable(scope);
    const row = normalizeRow(await request<unknown>(`${modulePath(scope)}/data/${encode(tableSlug)}/${encode(rowId)}`, {
      method: "PATCH", body: JSON.stringify({ body }), headers: sessionHeaders(scope),
    }));
    if (row.row_id !== rowId) throw new ModuleRuntimeRequestError("Backend updated a different module row.", { code: "ROW_MISMATCH" });
    assertMutationScope(scope, row);
    return row;
  }

  async function deleteRow(scope: ModuleRuntimeScope, tableSlug: string, rowId: string): Promise<void> {
    assertWritable(scope);
    const result = await request<unknown>(`${modulePath(scope)}/data/${encode(tableSlug)}/${encode(rowId)}`, { method: "DELETE", headers: sessionHeaders(scope) });
    if (!isRecord(result) || result.deleted !== true || result.row_id !== rowId) throw new ModuleRuntimeRequestError("Backend did not confirm deletion of the requested module row.", { code: "INVALID_DELETE_RESULT" });
  }

  async function invokeAction(scope: ModuleRuntimeScope, actionId: string, input: { rowId?: string; params?: JsonRecord } = {}): Promise<ModuleActionResult> {
    const body: JsonRecord = {
      project_id: scope.projectId,
      ...(input.rowId ? { row_id: input.rowId } : {}),
      ...(input.params ? { params: input.params } : {}),
    };
    return request<ModuleActionResult>(`/cabinets/${encode(scope.cabinetId)}/modules/${encode(scope.moduleId)}/actions/${encode(actionId)}/invoke`, {
      method: "POST", body: JSON.stringify(body), headers: sessionHeaders(scope),
    });
  }

  async function uploadContent(scope: ModuleRuntimeScope, file: File): Promise<ModuleFileRef> {
    assertWritable(scope);
    const form = new FormData();
    form.append("file", file, file.name);
    const token = getToken();
    const response = await fetcher(`${baseUrl}/cabinets/${encode(scope.cabinetId)}/content/upload`, {
      method: "POST", body: form, headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new ModuleRuntimeRequestError(`Module content upload failed (HTTP ${response.status}).`, { status: response.status });
    const body: unknown = await response.json();
    const ref = isRecord(body) && isRecord(body.file_ref) ? body.file_ref : body;
    if (!isRecord(ref) || ["asset_id", "version_id", "storage_key", "filename"].some((key) => typeof ref[key] !== "string" || !ref[key])) throw new ModuleRuntimeRequestError("Upload did not return a canonical FileRef.", { code: "INVALID_FILE_REF" });
    return ref as ModuleFileRef;
  }

  async function uploadSecret(scope: ModuleRuntimeScope, secret: string, label?: string): Promise<JsonRecord> {
    assertWritable(scope);
    const result = await request<unknown>(`/cabinets/${encode(scope.cabinetId)}/modules/${encode(scope.moduleId)}/secrets/upload`, {
      method: "POST",
      body: JSON.stringify({ secret, ...(label ? { label } : {}) }),
    });
    if (!isRecord(result) || typeof result.secret_ref !== "string" || !result.secret_ref) {
      throw new ModuleRuntimeRequestError("Secret upload did not return a canonical secret_ref.", { code: "INVALID_SECRET_REF" });
    }
    return result as JsonRecord;
  }

  async function downloadFile(_scope: ModuleRuntimeScope, ref: ModuleFileRef): Promise<{ blob: Blob; filename: string }> {
    if (typeof ref.asset_id !== "string" || !ref.asset_id) throw new ModuleRuntimeRequestError("FileRef has no asset_id.", { code: "INVALID_FILE_REF" });
    const params = new URLSearchParams({ proxy: "1" });
    const version = ref.blob_version_id ?? ref.version_id;
    if (typeof version === "string" && version) params.set("blob_version_id", version);
    const token = getToken();
    const response = await fetcher(`${baseUrl}/content/assets/${encode(ref.asset_id)}/download${params.size ? `?${params}` : ""}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new ModuleRuntimeRequestError(`File download failed (HTTP ${response.status}).`, { status: response.status });
    return { blob: await response.blob(), filename: fileNameFromDisposition(response.headers.get("Content-Disposition"), ref.filename || "download") };
  }

  async function listBoundProjects(scope: ModuleRuntimeScope, requestOptions?: { signal?: AbortSignal }): Promise<ModuleProjectOption[]> {
    const result = await request<unknown>(`/cabinets/${encode(scope.cabinetId)}/modules/${encode(scope.moduleId)}/bound-projects`, { signal: requestOptions?.signal });
    if (!isRecord(result) || !Array.isArray(result.items)) throw new ModuleRuntimeRequestError("Backend returned malformed bound projects.", { code: "INVALID_PROJECT_OPTIONS" });
    return result.items.map((item) => {
      if (!isRecord(item) || typeof item.project_id !== "string" || !item.project_id || typeof item.name !== "string") {
        throw new ModuleRuntimeRequestError("Backend returned a malformed bound project entry.", { code: "INVALID_PROJECT_OPTIONS" });
      }
      return { project_id: item.project_id, name: item.name };
    });
  }

  function assertMutationScope(scope: ModuleRuntimeScope, row: ModuleRow): void {
    const value = row as ModuleRow & { module_id?: unknown; project_id?: unknown };
    if (value.module_id !== undefined && value.module_id !== scope.moduleId) throw new ModuleRuntimeRequestError("Backend returned a row from a different module.", { code: "MODULE_MISMATCH" });
    if (value.project_id !== undefined && value.project_id !== scope.projectId) throw new ModuleRuntimeRequestError("Backend returned a row from a different project.", { code: "PROJECT_MISMATCH" });
  }

  async function setProfile(scope: ModuleRuntimeScope, profileId: string): Promise<JsonRecord> {
    assertWritable(scope);
    return request<JsonRecord>(`${projectPath(scope.projectId)}/modules/${encode(scope.moduleId)}/profile`, {
      method: "PATCH", body: JSON.stringify({ profile_id: profileId }),
    });
  }

  return { listProjectModules, loadModule, listRows, createRow, patchRow, deleteRow, invokeAction, uploadContent, uploadSecret, downloadFile, listBoundProjects, setProfile };
}

export function createDefaultModuleRuntimeService(): ModuleRuntimeService {
  return createModuleRuntimeService();
}
