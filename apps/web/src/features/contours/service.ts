export type ContourRow = Record<string, unknown> & { id: string };
export type ContourInput = Record<string, unknown>;
export type FetchLike = typeof fetch;

export interface EmployeeCreateInput extends ContourInput {
  login: string;
  password: string;
  display_name: string | null;
  role: "member" | "company.admin";
  contact_email: string | null;
}

export interface ContourService {
  listEmployees(companyId: string, includeDeleted?: boolean): Promise<ContourRow[]>;
  createEmployee(companyId: string, body: EmployeeCreateInput, isCurrent?: () => boolean): Promise<ContourRow>;
  updateEmployeeEmail(companyId: string, employeeId: string, contact_email: string | null, isCurrent?: () => boolean): Promise<ContourRow>;
  setEmployeePassword(companyId: string, employeeId: string, password: string, isCurrent?: () => boolean): Promise<ContourRow>;
  disableEmployee(companyId: string, employeeId: string, isCurrent?: () => boolean): Promise<ContourRow>;
  enableEmployee(companyId: string, employeeId: string, isCurrent?: () => boolean): Promise<ContourRow>;
  deleteEmployee(companyId: string, employeeId: string, isCurrent?: () => boolean): Promise<ContourRow>;
  restoreEmployee(companyId: string, employeeId: string, isCurrent?: () => boolean): Promise<ContourRow>;
  listCompanyCabinets(companyId: string): Promise<ContourRow[]>;
  listAssignments(companyId: string, cabinetId: string): Promise<ContourRow[]>;
  assignEmployee(companyId: string, cabinetId: string, employee_id: string): Promise<ContourRow>;
  revokeAssignment(companyId: string, cabinetId: string, employeeId: string): Promise<unknown>;
  listCompanyKeys(companyId: string): Promise<ContourRow[]>;
  createCompanyKey(companyId: string, body: ContourInput): Promise<ContourRow>;
  patchCompanyKey(companyId: string, keyId: string, body: ContourInput): Promise<ContourRow>;
  deleteCompanyKey(companyId: string, keyId: string): Promise<unknown>;
  renewCompanyKey(companyId: string, keyId: string, months: number): Promise<ContourRow>;
  rotateCompanyKey(companyId: string, keyId: string, secret: string): Promise<ContourRow>;
  listCompanyModels(companyId: string): Promise<ContourRow[]>;
  listKeyModels(companyId: string, keyId: string): Promise<ContourRow[]>;
  updateKeyModels(companyId: string, keyId: string, selections: ContourInput[]): Promise<ContourRow[]>;
  createCompanyModel(companyId: string, body: ContourInput): Promise<ContourRow>;
  patchCompanyModel(companyId: string, modelId: string, body: ContourInput): Promise<ContourRow>;
  deleteCompanyModel(companyId: string, modelId: string): Promise<unknown>;
  listCompanies(): Promise<ContourRow[]>;
  getCompany(companyId: string): Promise<ContourRow>;
  patchCompany(companyId: string, body: ContourInput): Promise<ContourRow>;
  setCompanyCabinetQuota(companyId: string, body: ContourInput): Promise<ContourRow>;
  deleteCompany(companyId: string): Promise<unknown>;
  restoreCompany(companyId: string): Promise<unknown>;
  purgeCompany(companyId: string): Promise<unknown>;
  listAdminCabinets(): Promise<ContourRow[]>;
  createAdminCabinet(body: ContourInput): Promise<ContourRow>;
  patchAdminCabinet(cabinetId: string, body: ContourInput): Promise<ContourRow>;
  deleteAdminCabinet(cabinetId: string): Promise<unknown>;
  restoreAdminCabinet(cabinetId: string): Promise<unknown>;
  purgeAdminCabinet(cabinetId: string): Promise<unknown>;
  listAdminModules(): Promise<ContourRow[]>;
  createAdminModule(body: ContourInput): Promise<ContourRow>;
  patchAdminModule(moduleId: string, body: ContourInput): Promise<ContourRow>;
  deleteAdminModule(moduleId: string): Promise<unknown>;
  listProviders(): Promise<ContourRow[]>;
  createProvider(body: ContourInput): Promise<ContourRow>;
  patchProvider(providerId: string, body: ContourInput): Promise<ContourRow>;
  deleteProvider(providerId: string): Promise<unknown>;
  listCompanyMetrics(): Promise<ContourRow[]>;
  getMetricSeries(query: { metric: string; entity_type: string; entity_id: string; window: string }): Promise<ContourInput>;
  rebuildMetrics(): Promise<ContourInput>;
  listRecycleBin(): Promise<ContourRow[]>;
  restoreRecycled(entity: string, entityId: string): Promise<unknown>;
  purgeRecycled(entity: string, entityId: string): Promise<unknown>;
}

export interface ContourServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: FetchLike;
}

export class ContourRequestError extends Error {
  constructor(message: string, readonly status?: number, readonly code?: string) {
    super(message);
    this.name = "ContourRequestError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function encode(value: string): string { return encodeURIComponent(value); }

function items<T extends ContourRow>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (isRecord(value) && Array.isArray(value.items)) return value.items as T[];
  throw new ContourRequestError("API returned an invalid list response.");
}

export function createContourService(options: ContourServiceOptions = {}): ContourService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function request<T = ContourRow>(path: string, method = "GET", body?: ContourInput): Promise<T> {
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
    } catch (error) {
      throw new ContourRequestError(error instanceof Error ? `API connection failed: ${error.message}` : "API connection failed.");
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
      } catch { /* retain HTTP fallback */ }
      throw new ContourRequestError(message, response.status, code);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new ContourRequestError("API returned an invalid JSON response.", response.status); }
  }
  const company = (id: string) => `/companies/${encode(id)}`;
  const adminCompany = (id: string) => `/admin/companies/${encode(id)}`;
  const adminCabinet = (id: string) => `/admin/cabinets/${encode(id)}`;
  const adminModule = (id: string) => `/admin/modules/${encode(id)}`;
  const providers = "/admin/catalogs/ai.http_providers/entries";
  const list = <T extends ContourRow>(path: string) => request<unknown>(path).then(items<T>);

  async function companyEmployeeSnapshot(companyId: string, employeeId: string): Promise<{ row: ContourRow; deleted: boolean }> {
    const [active, deleted] = await Promise.all([
      listEmployees(companyId),
      listEmployees(companyId, true),
    ]);
    const activeRow = active.find((row) => row.id === employeeId);
    const deletedRow = deleted.find((row) => row.id === employeeId);
    if (activeRow && deletedRow) throw new ContourRequestError("Employee appears in both company employee lists.");
    const row = deletedRow ?? activeRow;
    if (!row) throw new ContourRequestError("Employee is not linked to this company.");
    if (row.status !== "active" && row.status !== "disabled" && row.status !== "invited") {
      throw new ContourRequestError("Employee status is invalid in the company employee list.");
    }
    // The live include_deleted=true list contains only tombstones; the active list omits deleted_at.
    const deletedAt = row.deleted_at;
    if (deletedAt !== undefined && deletedAt !== null && (typeof deletedAt !== "string" || deletedAt.trim().length === 0)) {
      throw new ContourRequestError("Employee deletion timestamp is invalid in the company employee list.");
    }
    const isDeleted = typeof deletedAt === "string" && deletedAt.trim().length > 0;
    if (isDeleted !== Boolean(deletedRow)) throw new ContourRequestError("Employee deletion state is inconsistent in the company employee list.");
    return { row: { ...row, deleted_at: isDeleted ? deletedAt : null }, deleted: isDeleted };
  }

  async function verifyEmployeeState(
    companyId: string,
    employeeId: string,
    expected: { status: string; deleted: boolean },
  ): Promise<ContourRow> {
    const snapshot = await companyEmployeeSnapshot(companyId, employeeId);
    const deletedAt = snapshot.row.deleted_at;
    const deletionStateMatches = expected.deleted
      ? typeof deletedAt === "string" && deletedAt.trim().length > 0
      : deletedAt === null;
    if (snapshot.row.status !== expected.status || snapshot.deleted !== expected.deleted || !deletionStateMatches) {
      throw new ContourRequestError("Employee change could not be verified from the company employee list.");
    }
    return snapshot.row;
  }

  function assertMutationEmployee(response: ContourRow, employeeId: string): void {
    if (response.id !== employeeId) throw new ContourRequestError("Employee mutation returned a mismatched employee ID.");
  }

  async function mutateEmployeeLifecycle(
    companyId: string,
    employeeId: string,
    action: "disable" | "enable" | "delete" | "restore",
    isCurrent?: () => boolean,
  ): Promise<ContourRow> {
    const current = await companyEmployeeSnapshot(companyId, employeeId);
    const expected = action === "disable"
      ? { status: "disabled", deleted: false }
      : action === "enable"
        ? { status: "active", deleted: false }
        : action === "delete"
          ? { status: "disabled", deleted: true }
          : { status: "disabled", deleted: false };
    if (action === "restore" && !current.deleted) {
      throw new ContourRequestError("Employee is not deleted in this company.");
    }
    if (current.row.status === expected.status && current.deleted === expected.deleted) return current.row;
    if (action !== "restore" && current.deleted) {
      throw new ContourRequestError("Deleted employees cannot use this action.");
    }
    if (isCurrent && !isCurrent()) throw new ContourRequestError("Company scope changed before the employee action could run.");
    const path = `/employees/${encode(employeeId)}`;
    const response = await request<ContourRow>(
      action === "delete" ? path : `${path}/${action}`,
      action === "delete" ? "DELETE" : "POST",
    );
    assertMutationEmployee(response, employeeId);
    return verifyEmployeeState(companyId, employeeId, expected);
  }

  async function updateEmployee(
    companyId: string,
    employeeId: string,
    action: () => Promise<ContourRow>,
    isCurrent?: () => boolean,
  ): Promise<ContourRow> {
    const current = await companyEmployeeSnapshot(companyId, employeeId);
    if (current.deleted) throw new ContourRequestError("Deleted employees cannot be edited.");
    if (isCurrent && !isCurrent()) throw new ContourRequestError("Company scope changed before the employee update could run.");
    const response = await action();
    assertMutationEmployee(response, employeeId);
    return verifyEmployeeState(companyId, employeeId, { status: String(current.row.status ?? ""), deleted: false });
  }

  async function listEmployees(companyId: string, includeDeleted = false): Promise<ContourRow[]> {
    const suffix = includeDeleted ? "?include_deleted=true" : "";
    return list(`${company(companyId)}/employees${suffix}`);
  }

  return {
    listEmployees,
    createEmployee: async (id, body, isCurrent) => {
      if (isCurrent && !isCurrent()) throw new ContourRequestError("Company scope changed before the employee could be created.");
      const created = await request<ContourRow>(`${company(id)}/employees`, "POST", { ...body });
      if (!created.id) throw new ContourRequestError("Employee creation returned no employee ID.");
      if (created.status !== "invited") throw new ContourRequestError("Created employee has an unexpected status.");
      return verifyEmployeeState(id, created.id, { status: "invited", deleted: false });
    },
    updateEmployeeEmail: (id, employeeId, contact_email, isCurrent) => updateEmployee(id, employeeId, () => request(`${company(id)}/employees/${encode(employeeId)}/contact-email`, "PATCH", { contact_email }), isCurrent),
    setEmployeePassword: (id, employeeId, password, isCurrent) => updateEmployee(id, employeeId, () => request(`${company(id)}/employees/${encode(employeeId)}/password`, "PUT", { password }), isCurrent),
    disableEmployee: (id, employeeId, isCurrent) => mutateEmployeeLifecycle(id, employeeId, "disable", isCurrent),
    enableEmployee: (id, employeeId, isCurrent) => mutateEmployeeLifecycle(id, employeeId, "enable", isCurrent),
    deleteEmployee: (id, employeeId, isCurrent) => mutateEmployeeLifecycle(id, employeeId, "delete", isCurrent),
    restoreEmployee: (id, employeeId, isCurrent) => mutateEmployeeLifecycle(id, employeeId, "restore", isCurrent),
    listCompanyCabinets: (id) => list(`${company(id)}/cabinets`),
    listAssignments: (id, cabinetId) => list(`${company(id)}/cabinets/${encode(cabinetId)}/assignments`),
    assignEmployee: (id, cabinetId, employee_id) => request(`${company(id)}/cabinets/${encode(cabinetId)}/assignments`, "POST", { employee_id }),
    revokeAssignment: (id, cabinetId, employeeId) => request(`${company(id)}/cabinets/${encode(cabinetId)}/assignments/${encode(employeeId)}`, "DELETE"),
    listCompanyKeys: (id) => list(`${company(id)}/ai-keys`),
    createCompanyKey: (id, body) => request(`${company(id)}/ai-keys`, "POST", body),
    patchCompanyKey: (id, keyId, body) => request(`${company(id)}/ai-keys/${encode(keyId)}`, "PATCH", body),
    deleteCompanyKey: (id, keyId) => request(`${company(id)}/ai-keys/${encode(keyId)}`, "DELETE"),
    renewCompanyKey: (id, keyId, months) => request(`${company(id)}/ai-keys/${encode(keyId)}/renew`, "POST", { months }),
    rotateCompanyKey: (id, keyId, secret) => request(`${company(id)}/ai-keys/${encode(keyId)}/rotate-secret`, "POST", { secret }),
    listCompanyModels: (id) => list(`${company(id)}/ai-models`),
    listKeyModels: (id, keyId) => list(`${company(id)}/ai-keys/${encode(keyId)}/models`),
    updateKeyModels: async (id, keyId, selections) => items<ContourRow>(await request<unknown>(`${company(id)}/ai-keys/${encode(keyId)}/models`, "PUT", { selections })),
    createCompanyModel: (id, body) => request(`${company(id)}/ai-models`, "POST", body),
    patchCompanyModel: (id, modelId, body) => request(`${company(id)}/ai-models/${encode(modelId)}`, "PATCH", body),
    deleteCompanyModel: (id, modelId) => request(`${company(id)}/ai-models/${encode(modelId)}`, "DELETE"),
    listCompanies: () => list("/admin/companies"),
    getCompany: (id) => request(adminCompany(id)),
    patchCompany: (id, body) => request(adminCompany(id), "PATCH", body),
    setCompanyCabinetQuota: (id, body) => request(`${adminCompany(id)}/cabinet-quotas`, "PUT", body),
    deleteCompany: (id) => request(adminCompany(id), "DELETE"),
    restoreCompany: (id) => request(`${adminCompany(id)}/restore`, "POST"),
    purgeCompany: (id) => request(`${adminCompany(id)}/purge`, "DELETE"),
    listAdminCabinets: () => list("/admin/cabinets"),
    createAdminCabinet: (body) => request("/admin/cabinets", "POST", body),
    patchAdminCabinet: (id, body) => request(adminCabinet(id), "PATCH", body),
    deleteAdminCabinet: (id) => request(adminCabinet(id), "DELETE"),
    restoreAdminCabinet: (id) => request(`${adminCabinet(id)}/restore`, "POST"),
    purgeAdminCabinet: (id) => request(`${adminCabinet(id)}/purge`, "DELETE"),
    listAdminModules: () => list("/admin/modules"),
    createAdminModule: (body) => request("/admin/modules", "POST", body),
    patchAdminModule: (id, body) => request(adminModule(id), "PATCH", body),
    deleteAdminModule: (id) => request(adminModule(id), "DELETE"),
    listProviders: () => list(providers),
    createProvider: (body) => request(providers, "POST", body),
    patchProvider: (id, body) => request(`${providers}/${encode(id)}`, "PATCH", body),
    deleteProvider: (id) => request(`${providers}/${encode(id)}`, "DELETE"),
    listCompanyMetrics: async () => {
      const result = await request<unknown>("/admin/metrics/companies");
      if (!isRecord(result) || !Array.isArray(result.items)) throw new ContourRequestError("API returned an invalid metrics response.");
      return result.items.map((row, index) => {
        if (!isRecord(row)) throw new ContourRequestError("API returned an invalid metric row.");
        return { ...row, id: String(row.company_id ?? row.id ?? `metric-${index}`) };
      });
    },
    getMetricSeries: (query) => {
      const params = new URLSearchParams(query);
      return request(`/admin/metrics/series?${params.toString()}`);
    },
    rebuildMetrics: () => request("/admin/metrics/rebuild", "POST"),
    listRecycleBin: async () => {
      const result = await request<unknown>("/admin/recycle");
      if (!isRecord(result)) throw new ContourRequestError("API returned an invalid recycle response.");
      const rows: ContourRow[] = [];
      for (const entity of ["companies", "employees", "cabinets", "projects"]) {
        if (Array.isArray(result[entity])) rows.push(...(result[entity] as ContourInput[]).map((row) => ({ ...row, id: String(row.id ?? ""), entity })));
      }
      return rows;
    },
    restoreRecycled: (entity, id) => request(`/admin/recycle/${encode(entity)}/${encode(id)}/restore`, "POST"),
    purgeRecycled: (entity, id) => request(`/admin/recycle/${encode(entity)}/${encode(id)}/purge`, "DELETE"),
  };
}
