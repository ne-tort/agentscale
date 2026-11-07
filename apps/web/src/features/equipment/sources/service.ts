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

/**
 * Why a request to the catalog API failed.
 *
 * A code rather than a sentence: the screen turns it into words in the
 * reader's language (`equipment.srcErr<Code>`), and the same failure reads the
 * same way wherever it is reported. Before this, the service threw technical
 * Russian/English sentences that the screen printed verbatim.
 */
export type EquipmentSourcesErrorCode =
  | "network"
  | "http"
  | "invalidList"
  | "invalidRow"
  | "invalidJson"
  | "moduleMissing"
  | "moduleUnconfirmed"
  | "actionUndefined"
  | "actionDisabled"
  | "uploadRejected"
  | "uploadIncomplete"
  | "secretIncomplete"
  | "serviceUnavailable";

export interface EquipmentSourcesRequestErrorOptions {
  /** HTTP status, when the failure came from a response. */
  readonly status?: number;
  /**
   * The server's own `detail`/`title`, passed through untouched. It is data:
   * the screen shows it as a second line, never as the explanation itself, and
   * sanitises it like any other server text.
   */
  readonly detail?: string;
  /** Technical message for logs and the stack; never rendered. */
  readonly message?: string;
}

export class EquipmentSourcesRequestError extends Error {
  readonly status?: number;
  readonly detail?: string;
  constructor(
    readonly code: EquipmentSourcesErrorCode,
    options: EquipmentSourcesRequestErrorOptions = {},
  ) {
    super(options.message ?? code);
    this.name = "EquipmentSourcesRequestError";
    if (options.status !== undefined) this.status = options.status;
    if (options.detail !== undefined) this.detail = options.detail;
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
  if (!rows) throw new EquipmentSourcesRequestError("invalidList", { message: "the response is neither a list nor { items }" });
  return rows.filter(isRecord);
}

function catalogRow(value: unknown): EquipmentCatalogRow {
  if (!isRecord(value) || typeof value.row_id !== "string" || !isRecord(value.body)) {
    throw new EquipmentSourcesRequestError("invalidRow", { message: "a catalog row has no row_id/body" });
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

/** The server's own explanation of a failed response, if it sent one. */
async function responseDetail(response: Response): Promise<string | undefined> {
  try {
    const payload: unknown = await response.json();
    if (!isRecord(payload)) return undefined;
    const details = isRecord(payload.error) ? payload.error : payload;
    if (typeof details.detail === "string" && details.detail.trim()) return details.detail;
    if (typeof details.title === "string" && details.title.trim()) return details.title;
  } catch { /* No readable body: the status code is all we have. */ }
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
      throw new EquipmentSourcesRequestError("network", {
        message: error instanceof Error ? error.message : `request to ${path} failed`,
      });
    }
    if (!response.ok) {
      const detail = await responseDetail(response);
      throw new EquipmentSourcesRequestError("http", {
        status: response.status,
        ...(detail ? { detail } : {}),
        message: `${init.method ?? "GET"} ${path} responded ${response.status}`,
      });
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch {
      throw new EquipmentSourcesRequestError("invalidJson", {
        status: response.status,
        message: `${init.method ?? "GET"} ${path} responded with unreadable JSON`,
      });
    }
  }

  function modulePath(): string { return `/admin/modules/${encode(EQUIPMENT_MODULE_ID)}`; }

  async function loadWorkspace(): Promise<EquipmentCatalogWorkspace> {
    const modules = listItems(await request<unknown>("/admin/modules"));
    const equipment = modules.find((item) => moduleId(item) === EQUIPMENT_MODULE_ID);
    if (!equipment) {
      throw new EquipmentSourcesRequestError("moduleMissing", { message: `${EQUIPMENT_MODULE_ID} is absent from the admin module catalog` });
    }
    const module = await request<unknown>(modulePath());
    if (!isRecord(module) || moduleId(module) !== EQUIPMENT_MODULE_ID) {
      throw new EquipmentSourcesRequestError("moduleUnconfirmed", { message: `the admin module API did not confirm ${EQUIPMENT_MODULE_ID}` });
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
    if (!action) {
      throw new EquipmentSourcesRequestError("actionUndefined", { message: `metadata declares no ${operation} action` });
    }
    if (!action.enabled) {
      throw new EquipmentSourcesRequestError("actionDisabled", { message: `metadata disables the ${action.id} action` });
    }
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
      throw new EquipmentSourcesRequestError("network", {
        message: error instanceof Error ? error.message : "the upload request failed",
      });
    }
    if (!response.ok) {
      throw new EquipmentSourcesRequestError("uploadRejected", {
        status: response.status,
        message: `the upload endpoint responded ${response.status}`,
      });
    }
    let value: unknown;
    try { value = await response.json(); }
    catch {
      throw new EquipmentSourcesRequestError("invalidJson", {
        status: response.status,
        message: "the upload endpoint returned unreadable JSON",
      });
    }
    if (!isRecord(value) || typeof value.asset_id !== "string" || typeof value.version_id !== "string"
      || typeof value.storage_key !== "string" || typeof value.filename !== "string") {
      throw new EquipmentSourcesRequestError("uploadIncomplete", { status: response.status, message: "the upload response is not a complete FileRef" });
    }
    return value as CatalogFileRef;
  }

  async function uploadSecret(secret: string, label: string): Promise<CatalogSecretRef> {
    const result = await request<unknown>(`${modulePath()}/secrets/upload`, {
      method: "POST",
      body: JSON.stringify({ secret, label }),
    });
    if (!isRecord(result) || typeof result.secret_ref !== "string" || !result.secret_ref.trim()) {
      throw new EquipmentSourcesRequestError("secretIncomplete", { message: "the secrets endpoint returned no secret_ref" });
    }
    return { secret_ref: result.secret_ref };
  }

  return { loadWorkspace, createCatalog, patchCatalog, invoke, uploadFile, uploadSecret };
}