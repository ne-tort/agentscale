import {
  isRecord,
  parseEquipmentCatalogMetadata,
  type CatalogFileRef,
  type CatalogOperation,
  type CatalogRowBody,
  type CatalogSecretRef,
  type EquipmentCatalogMetaDocument,
  type EquipmentCatalogRow,
  type EquipmentCatalogWorkspace,
} from "./contracts";

export const EQUIPMENT_MODULE_ID = "mod_equipment";
export const EQUIPMENT_CATALOGS_TABLE = "catalogs";

export class EquipmentSourcesRequestError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "EquipmentSourcesRequestError";
  }
}

export interface EquipmentSourcesService {
  loadWorkspace(): Promise<EquipmentCatalogWorkspace>;
  createCatalog(body: CatalogRowBody): Promise<EquipmentCatalogRow>;
  patchCatalog(rowId: string, body: CatalogRowBody): Promise<EquipmentCatalogRow>;
  invoke(rowId: string, operation: CatalogOperation): Promise<unknown>;
  uploadFile(file: File): Promise<CatalogFileRef>;
  uploadSecret(secret: string, label: string): Promise<CatalogSecretRef>;
}

export interface EquipmentSourcesServiceOptions {
  readonly baseUrl?: string;
  readonly getAccessToken?: () => string | undefined;
  readonly fetcher?: typeof fetch;
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function listItems(value: unknown): Record<string, unknown>[] {
  const rows = Array.isArray(value) ? value : isRecord(value) && Array.isArray(value.items) ? value.items : undefined;
  if (!rows) throw new EquipmentSourcesRequestError("API вернул некорректный список.");
  return rows.filter(isRecord);
}

function catalogRow(value: unknown): EquipmentCatalogRow {
  if (!isRecord(value) || typeof value.row_id !== "string" || !isRecord(value.body)) {
    throw new EquipmentSourcesRequestError("API вернул некорректную строку каталога.");
  }
  return { row_id: value.row_id, body: value.body };
}

function metaDocument(value: Record<string, unknown>): EquipmentCatalogMetaDocument | undefined {
  if (typeof value.slug !== "string") return undefined;
  return { slug: value.slug, body: value.body };
}

function moduleId(value: Record<string, unknown>): string | undefined {
  if (typeof value.id === "string") return value.id;
  if (typeof value.module_id === "string") return value.module_id;
  return undefined;
}

export function createEquipmentSourcesService(options: EquipmentSourcesServiceOptions = {}): EquipmentSourcesService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = options.getAccessToken?.();
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
    } catch (error) {
      throw new EquipmentSourcesRequestError(error instanceof Error ? `API connection failed: ${error.message}` : "API connection failed.");
    }
    if (!response.ok) {
      let message = response.statusText || `API returned HTTP ${response.status}.`;
      try {
        const payload: unknown = await response.json();
        if (isRecord(payload)) {
          const details = isRecord(payload.error) ? payload.error : payload;
          if (typeof details.detail === "string") message = details.detail;
          else if (typeof details.title === "string") message = details.title;
        }
      } catch { /* Keep the HTTP fallback. */ }
      throw new EquipmentSourcesRequestError(message, response.status);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new EquipmentSourcesRequestError("API вернул некорректный JSON.", response.status); }
  }

  function modulePath(): string { return `/admin/modules/${encode(EQUIPMENT_MODULE_ID)}`; }

  async function loadWorkspace(): Promise<EquipmentCatalogWorkspace> {
    const modules = listItems(await request<unknown>("/admin/modules"));
    const equipment = modules.find((item) => moduleId(item) === EQUIPMENT_MODULE_ID);
    if (!equipment) throw new EquipmentSourcesRequestError("Модуль mod_equipment отсутствует в admin module catalog.", 404);
    const module = await request<unknown>(modulePath());
    if (!isRecord(module) || moduleId(module) !== EQUIPMENT_MODULE_ID) {
      throw new EquipmentSourcesRequestError("Admin module API не подтвердил mod_equipment.");
    }
    const [metaValue, rowsValue] = await Promise.all([
      request<unknown>(`${modulePath()}/meta/documents`),
      request<unknown>(`${modulePath()}/data/${encode(EQUIPMENT_CATALOGS_TABLE)}`),
    ]);
    const documents = listItems(metaValue).flatMap((item) => {
      const document = metaDocument(item);
      return document ? [document] : [];
    });
    const rows = listItems(rowsValue).map(catalogRow);
    return {
      module: { id: EQUIPMENT_MODULE_ID, name: typeof module.name === "string" ? module.name : "Подбор техники" },
      metadata: parseEquipmentCatalogMetadata(documents),
      rows,
    };
  }

  async function createCatalog(body: CatalogRowBody): Promise<EquipmentCatalogRow> {
    return catalogRow(await request<unknown>(`${modulePath()}/data/${encode(EQUIPMENT_CATALOGS_TABLE)}`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }));
  }

  async function patchCatalog(rowId: string, body: CatalogRowBody): Promise<EquipmentCatalogRow> {
    return catalogRow(await request<unknown>(`${modulePath()}/data/${encode(EQUIPMENT_CATALOGS_TABLE)}/${encode(rowId)}`, {
      method: "PATCH",
      body: JSON.stringify({ body }),
    }));
  }

  async function invoke(rowId: string, operation: CatalogOperation): Promise<unknown> {
    const workspace = await loadWorkspace();
    const action = {
      probe: workspace.metadata.probeAction,
      databases: workspace.metadata.databasesAction,
      tables: workspace.metadata.tablesAction,
      index: workspace.metadata.indexAction,
    }[operation];
    if (!action) throw new EquipmentSourcesRequestError(`В metadata не объявлено действие ${operation}.`);
    if (!action.enabled) throw new EquipmentSourcesRequestError(`Действие ${action.id} отключено в metadata.`);
    return request<unknown>(`${modulePath()}/actions/${encode(action.id)}/invoke`, {
      method: "POST",
      body: JSON.stringify({ row_id: rowId }),
    });
  }

  async function uploadFile(file: File): Promise<CatalogFileRef> {
    const form = new FormData();
    form.append("file", file, file.name);
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${modulePath()}/content/upload`, {
        method: "POST",
        body: form,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
    } catch (error) {
      throw new EquipmentSourcesRequestError(error instanceof Error ? `Upload connection failed: ${error.message}` : "Upload connection failed.");
    }
    if (!response.ok) throw new EquipmentSourcesRequestError(`Загрузка файла отклонена API (HTTP ${response.status}).`, response.status);
    const value: unknown = await response.json();
    if (!isRecord(value) || typeof value.asset_id !== "string" || typeof value.version_id !== "string"
      || typeof value.storage_key !== "string" || typeof value.filename !== "string") {
      throw new EquipmentSourcesRequestError("Upload API не вернул полный FileRef.");
    }
    return value as CatalogFileRef;
  }

  async function uploadSecret(secret: string, label: string): Promise<CatalogSecretRef> {
    const result = await request<unknown>(`${modulePath()}/secrets/upload`, {
      method: "POST",
      body: JSON.stringify({ secret, label }),
    });
    if (!isRecord(result) || typeof result.secret_ref !== "string" || !result.secret_ref.trim()) {
      throw new EquipmentSourcesRequestError("Secrets API не вернул secret reference.");
    }
    return { secret_ref: result.secret_ref };
  }

  return { loadWorkspace, createCatalog, patchCatalog, invoke, uploadFile, uploadSecret };
}