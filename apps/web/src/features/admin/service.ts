/**
 * The platform administrator's API surface.
 *
 * Deliberately narrow: four things an administrator actually does — see
 * companies, connect keys to them, grant modules, empty the bin. Everything the
 * platform does under the hood (provider catalogs, bind kinds, cabinet grants,
 * metric series) is either read for a picker or not exposed at all.
 */

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface AdminServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: FetchLike;
}

export interface AdminCompany {
  id: string;
  name: string;
  login: string;
  description: string | null;
  contactEmail: string | null;
  phone: string | null;
  status: string;
  createdAt: string | null;
  keyIds: string[];
  moduleIds: string[];
  cabinets: number;
  employees: number;
  tokensUsed: number;
  requests: number;
  quota: { cabinets: number; packagesPerCabinet: number; importMb: number };
}

export interface CreatedCompany {
  id: string;
  name: string;
  login: string;
  password: string;
}

export interface AdminKey {
  id: string;
  name: string;
  provider: string;
  status: string;
  companyIds: string[];
  hasSecret: boolean;
  createdAt: string | null;
  /**
   * Where the key points. Resolved on the server from the endpoint record the
   * key is linked to; `null` for a key created before endpoints were a field.
   */
  baseUrl: string | null;
  modelsPath: string | null;
  authScheme: string | null;
}

/** What an administrator types to add a key: an address and a secret. */
export interface NewKeyInput {
  name: string;
  baseUrl: string;
  secret: string;
  companyIds: string[];
  modelsPath?: string;
  chatCompletionsPath?: string;
  authScheme?: "bearer" | "x-api-key";
}

export interface AdminModule {
  id: string;
  name: string;
  status: string;
  companyIds: string[];
  /** `all` means every company, present and future, already has it. */
  allCompanies: boolean;
  bindings: number;
}


export interface DeletedEntity {
  id: string;
  entity: "companies" | "employees" | "cabinets" | "projects";
  name: string;
  login: string;
  deletedAt: string | null;
}

export class AdminRequestError extends Error {
  constructor(message: string, readonly status?: number, readonly code?: string) {
    super(message);
    this.name = "AdminRequestError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === "string" ? value : "";
}

function number(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function listOf(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter(isRecord);
  if (isRecord(value) && Array.isArray(value.items)) return value.items.filter(isRecord);
  return [];
}

function toCompany(row: Record<string, unknown>): AdminCompany {
  const quota = isRecord(row.cabinet_quota) ? row.cabinet_quota : {};
  const metrics = isRecord(row.metrics) ? row.metrics : {};
  return {
    id: text(row, "id"),
    name: text(row, "name"),
    login: text(row, "username") || text(row, "id"),
    description: text(row, "description") || null,
    contactEmail: text(row, "contact_email") || null,
    phone: text(row, "phone") || null,
    status: text(row, "status") || "active",
    createdAt: text(row, "created_at") || null,
    keyIds: strings(row.key_ids ?? row.ai_key_ids),
    moduleIds: strings(row.module_ids),
    cabinets: number(row, "active_cabinets"),
    employees: number(row, "employees_total"),
    tokensUsed: number(metrics, "agent_tokens_used"),
    requests: number(metrics, "agent_requests"),
    quota: {
      cabinets: number(quota, "max_cabinets") || number(row, "cabinets_quota"),
      packagesPerCabinet: number(quota, "max_packages_per_cabinet"),
      importMb: number(quota, "max_bundle_import_mb"),
    },
  };
}

export interface AdminService {
  listCompanies(): Promise<AdminCompany[]>;
  getCompany(id: string): Promise<AdminCompany>;
  createCompany(input: { name: string; password: string; contactEmail?: string }): Promise<CreatedCompany>;
  deleteCompany(id: string): Promise<void>;
  setCompanyQuota(id: string, quota: AdminCompany["quota"]): Promise<void>;

  listKeys(): Promise<AdminKey[]>;
  createKey(input: NewKeyInput): Promise<void>;
  setKeyCompanies(id: string, companyIds: string[]): Promise<void>;
  deleteKey(id: string): Promise<void>;

  listModules(): Promise<AdminModule[]>;
  createModule(name: string): Promise<void>;
  setModuleCompanies(id: string, companyIds: string[]): Promise<void>;
  /**
   * Grant a module to every company (present and future) or back to a chosen
   * list. Returns the scope the server settled on.
   */
  setModuleScope(id: string, scope: "all" | "selected"): Promise<"all" | "selected">;
  deleteModule(id: string): Promise<void>;

  listDeleted(): Promise<DeletedEntity[]>;
  restoreDeleted(entity: DeletedEntity["entity"], id: string): Promise<void>;
  purgeDeleted(entity: DeletedEntity["entity"], id: string): Promise<void>;
}

const DELETED_ENTITIES = ["companies", "employees", "cabinets", "projects"] as const;

export function createAdminService(options: AdminServiceOptions = {}): AdminService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function request<T = unknown>(path: string, method = "GET", body?: unknown): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        method,
        headers: {
          Accept: "application/json",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (error) {
      throw new AdminRequestError(
        error instanceof Error ? `API connection failed: ${error.message}` : "API connection failed.",
      );
    }
    if (!response.ok) {
      let message = response.statusText || `Request failed (${response.status})`;
      let code: string | undefined;
      try {
        const payload: unknown = await response.json();
        if (isRecord(payload)) {
          const detail = isRecord(payload.error) ? payload.error : payload;
          if (typeof detail.detail === "string") message = detail.detail;
          else if (typeof detail.title === "string") message = detail.title;
          if (typeof detail.code === "string") code = detail.code;
        }
      } catch {
        // keep the HTTP status text
      }
      throw new AdminRequestError(message, response.status, code);
    }
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      return undefined as T;
    }
  }

  const company = (id: string) => `/admin/companies/${encodeURIComponent(id)}`;
  const key = (id: string) => `/admin/ai-keys/${encodeURIComponent(id)}`;
  const module = (id: string) => `/admin/modules/${encodeURIComponent(id)}`;

  async function companies(): Promise<AdminCompany[]> {
    return listOf(await request("/admin/companies")).map(toCompany).filter((row) => row.id);
  }

  async function keys(): Promise<AdminKey[]> {
    return listOf(await request("/admin/ai-keys")).map((row) => ({
      id: text(row, "id"),
      name: text(row, "name"),
      provider: text(row, "provider"),
      status: text(row, "status") || "active",
      companyIds: strings(row.company_ids),
      hasSecret: row.has_secret === true,
      createdAt: text(row, "created_at") || null,
      baseUrl: text(row, "base_url") || null,
      modelsPath: text(row, "models_path") || null,
      authScheme: text(row, "auth_scheme") || null,
    }));
  }

  async function modules(): Promise<AdminModule[]> {
    return listOf(await request("/admin/modules")).map((row) => ({
      id: text(row, "id"),
      name: text(row, "name"),
      status: text(row, "status") || "active",
      companyIds: strings(row.company_ids),
      allCompanies: text(row, "company_grant_scope") === "all",
      bindings: number(row, "cabinet_bindings_count") + number(row, "project_bindings_count"),
    }));
  }

  return {
    listCompanies: companies,
    async getCompany(id) {
      return toCompany(await request(company(id)));
    },
    async createCompany({ name, password, contactEmail }) {
      const created = await request<unknown>("/companies", "POST", {
        name,
        password,
        ...(contactEmail ? { contact_email: contactEmail } : {}),
      });
      const record = isRecord(created) ? created : {};
      const createdCompany = isRecord(record.company) ? record.company : record;
      const credentials = isRecord(record.credentials) ? record.credentials : {};
      return {
        id: text(createdCompany, "id"),
        name: text(createdCompany, "name") || name,
        login: text(credentials, "username") || text(createdCompany, "username"),
        password,
      };
    },
    deleteCompany: (id) => request(company(id), "DELETE").then(() => undefined),
    async setCompanyQuota(id, quota) {
      await request(`${company(id)}/cabinet-quotas`, "PUT", {
        max_cabinets: quota.cabinets,
        max_packages_per_cabinet: quota.packagesPerCabinet,
        max_bundle_import_mb: quota.importMb,
      });
    },

    listKeys: keys,
    async createKey({ name, baseUrl, secret, companyIds, modelsPath, chatCompletionsPath, authScheme }) {
      // The server turns `base_url` into (or reuses) an endpoint record and links
      // the key to it. No provider is sent: the address is the identity.
      await request("/admin/ai-keys", "POST", {
        name,
        base_url: baseUrl,
        secret,
        company_ids: companyIds,
        ...(modelsPath ? { models_path: modelsPath } : {}),
        ...(chatCompletionsPath ? { chat_completions_path: chatCompletionsPath } : {}),
        ...(authScheme ? { auth_scheme: authScheme } : {}),
      });
    },
    async setKeyCompanies(id, companyIds) {
      await request(`${key(id)}/companies`, "PUT", { company_ids: companyIds });
    },
    deleteKey: (id) => request(key(id), "DELETE").then(() => undefined),

    listModules: modules,
    async createModule(name) {
      await request("/admin/modules", "POST", { name });
    },
    async setModuleCompanies(id, companyIds) {
      await request(module(id), "PATCH", { company_ids: companyIds });
    },
    async setModuleScope(id, scope) {
      const updated = await request<unknown>(module(id), "PATCH", { company_grant_scope: scope });
      const record = isRecord(updated) ? updated : {};
      const applied = text(record, "company_grant_scope");
      return applied === "all" || applied === "selected" ? applied : scope;
    },
    deleteModule: (id) => request(module(id), "DELETE").then(() => undefined),

    async listDeleted() {
      const payload = await request<unknown>("/admin/recycle");
      if (!isRecord(payload)) return [];
      const rows: DeletedEntity[] = [];
      for (const entity of DELETED_ENTITIES) {
        for (const row of listOf(payload[entity])) {
          const id = text(row, "id");
          if (!id) continue;
          rows.push({
            id,
            entity,
            name: text(row, "name") || text(row, "display_name") || id,
            login: text(row, "username") || text(row, "email") || text(row, "contact_email"),
            deletedAt: text(row, "deleted_at") || null,
          });
        }
      }
      return rows;
    },
    async restoreDeleted(entity, id) {
      await request(`/admin/recycle/${entity}/${encodeURIComponent(id)}/restore`, "POST");
    },
    async purgeDeleted(entity, id) {
      await request(`/admin/recycle/${entity}/${encodeURIComponent(id)}/purge`, "DELETE");
    },
  };
}
