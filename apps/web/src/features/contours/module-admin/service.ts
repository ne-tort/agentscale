export type ModuleAdminRow = Record<string, unknown> & { id?: string };
export type ModuleAdminInput = Record<string, unknown>;
export type ModuleAdminMode = "admin" | "company";

export interface ModuleAdminService {
  listAdminModules(): Promise<ModuleAdminRow[]>;
  getAdminModule(moduleId: string): Promise<ModuleAdminRow>;
  listCompanyModules(companyId: string): Promise<ModuleAdminRow[]>;
  getCompanyModule(companyId: string, moduleId: string): Promise<ModuleAdminRow>;
  createAdminModule(name: string): Promise<ModuleAdminRow>;
  createCompanyModule(companyId: string, name: string): Promise<ModuleAdminRow>;
  patchAdminModule(moduleId: string, body: ModuleAdminInput): Promise<ModuleAdminRow>;
  patchCompanyModule(companyId: string, moduleId: string, body: ModuleAdminInput): Promise<ModuleAdminRow>;
  listMetaDocuments(mode: ModuleAdminMode, moduleId: string, companyId?: string): Promise<ModuleAdminRow[]>;
  listCabinetBindings(mode: ModuleAdminMode, moduleId: string, companyId?: string): Promise<ModuleAdminRow[]>;
  listProjectBindings(mode: ModuleAdminMode, moduleId: string, companyId?: string): Promise<ModuleAdminRow[]>;
  listCompanies(): Promise<ModuleAdminRow[]>;
  listCabinetInstallations(cabinetId: string): Promise<ModuleAdminRow[]>;
  listCabinetModuleData(cabinetId: string, moduleId: string, tableSlug: string): Promise<ModuleAdminRow[]>;
}

export interface ModuleAdminServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export class ModuleAdminRequestError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ModuleAdminRequestError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function listItems(value: unknown): ModuleAdminRow[] {
  if (Array.isArray(value)) return value.filter(isRecord) as ModuleAdminRow[];
  if (isRecord(value) && Array.isArray(value.items)) return value.items.filter(isRecord) as ModuleAdminRow[];
  throw new ModuleAdminRequestError("Сервер вернул неожиданный ответ. Обновите страницу.");
}

export function createModuleAdminService(options: ModuleAdminServiceOptions = {}): ModuleAdminService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function request<T = ModuleAdminRow>(path: string, method = "GET", body?: ModuleAdminInput): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        method,
        headers: {
          Accept: "application/json",
          ...(body ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    } catch {
      throw new ModuleAdminRequestError("Не удалось связаться с сервером. Проверьте подключение.");
    }
    if (!response.ok) {
      let message = response.statusText || `Запрос не выполнен (код ${response.status}).`;
      try {
        const payload: unknown = await response.json();
        if (isRecord(payload)) {
          const detail = isRecord(payload.error) ? payload.error : payload;
          if (typeof detail.detail === "string") message = detail.detail;
          else if (typeof detail.title === "string") message = detail.title;
        }
      } catch { /* retain HTTP fallback */ }
      throw new ModuleAdminRequestError(message, response.status);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new ModuleAdminRequestError("Сервер вернул неожиданный ответ. Обновите страницу.", response.status); }
  }

  const modulePath = (moduleId: string) => `/admin/modules/${encode(moduleId)}`;
  const companyModulePath = (companyId: string, moduleId: string) => `/companies/${encode(companyId)}/modules/${encode(moduleId)}`;
  const list = (path: string) => request<unknown>(path).then(listItems);
  const companyRequired = (companyId?: string) => {
    if (!companyId) throw new ModuleAdminRequestError("Не выбрана компания — действие невозможно.");
    return companyId;
  };

  return {
    listAdminModules: () => list("/admin/modules"),
    getAdminModule: (moduleId) => request(modulePath(moduleId)),
    listCompanyModules: (companyId) => list(`/companies/${encode(companyId)}/modules`),
    getCompanyModule: (companyId, moduleId) => request(companyModulePath(companyId, moduleId)),
    createAdminModule: (name) => request("/admin/modules", "POST", { name }),
    createCompanyModule: (companyId, name) => request(`/companies/${encode(companyId)}/modules`, "POST", { name }),
    patchAdminModule: (moduleId, body) => request(modulePath(moduleId), "PATCH", body),
    patchCompanyModule: (companyId, moduleId, body) => request(companyModulePath(companyId, moduleId), "PATCH", body),
    listMetaDocuments: (mode, moduleId, companyId) => {
      const root = mode === "admin"
        ? modulePath(moduleId)
        : `${companyModulePath(companyRequired(companyId), moduleId)}`;
      return list(`${root}/meta/documents`);
    },
    listCabinetBindings: (mode, moduleId, companyId) => {
      const root = mode === "admin"
        ? modulePath(moduleId)
        : companyModulePath(companyRequired(companyId), moduleId);
      return list(`${root}/cabinets`);
    },
    listProjectBindings: (mode, moduleId, companyId) => {
      const root = mode === "admin"
        ? modulePath(moduleId)
        : companyModulePath(companyRequired(companyId), moduleId);
      return list(`${root}/projects`);
    },
    listCompanies: () => list("/admin/companies"),
    listCabinetInstallations: (cabinetId) => list(`/cabinets/${encode(cabinetId)}/modules`),
    listCabinetModuleData: (cabinetId, moduleId, tableSlug) => list(`/cabinets/${encode(cabinetId)}/modules/${encode(moduleId)}/data/${encode(tableSlug)}`),
  };
}