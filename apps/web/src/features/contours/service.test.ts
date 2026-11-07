import { describe, expect, it, vi } from "vitest";
import { createContourService } from "./service";
import type { ContourRow } from "./service";

function response(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("contour service", () => {
  it("uses the confirmed company assignment and AI key routes", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toContain("/api/v1/companies/company%2F1/cabinets/cab%2F1/assignments");
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({ employee_id: "employee-1" });
      return response({ id: "assignment-1" });
    });
    const service = createContourService({ fetcher });
    await service.assignEmployee("company/1", "cab/1", "employee-1");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("maps envelope lists and catalog provider entries without inventing a provider endpoint", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toContain("/admin/catalogs/ai.http_providers/entries");
      return response({ items: [{ id: "openai", title: "OpenAI" }] });
    });
    const service = createContourService({ fetcher });
    await expect(service.listProviders()).resolves.toEqual([{ id: "openai", title: "OpenAI" }]);
  });

  it("uses the confirmed key-model binding endpoints", async () => {
    const fetcher = vi.fn(async () => response([{ id: "model-1", name: "GPT", enabled: true, is_default: true }]));
    const service = createContourService({ fetcher });
    await expect(service.listKeyModels("company-1", "key-1")).resolves.toEqual([{ id: "model-1", name: "GPT", enabled: true, is_default: true }]);

    const updateFetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("PUT");
      expect(JSON.parse(String(init?.body))).toEqual({ selections: [{ model_id: "model-1", enabled: true, is_default: true }] });
      return response([{ id: "model-1", name: "GPT", enabled: true, is_default: true }]);
    });
    const updateService = createContourService({ fetcher: updateFetcher });
    await expect(updateService.updateKeyModels("company-1", "key-1", [{ model_id: "model-1", enabled: true, is_default: true }])).resolves.toHaveLength(1);
  });

  it("returns API errors with status and server detail", async () => {
    const service = createContourService({ fetcher: vi.fn(async () => response({ detail: "forbidden", code: "FORBIDDEN" }, 403)) });
    await expect(service.listCompanies()).rejects.toMatchObject({ status: 403, code: "FORBIDDEN", message: "forbidden" });
  });

  it("flattens recycle-bin entity groups with their real entity names", async () => {
    const service = createContourService({ fetcher: vi.fn(async () => response({ companies: [{ id: "co-1", name: "Acme" }], employees: [], cabinets: [{ id: "cab-1", name: "Main" }], projects: [] })) });
    await expect(service.listRecycleBin()).resolves.toEqual([
      { id: "co-1", name: "Acme", entity: "companies" },
      { id: "cab-1", name: "Main", entity: "cabinets" },
    ]);
  });

  it("creates employees on the company route and verifies an invited row through both employee lists", async () => {
    const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = [];
    let employee: ContourRow | undefined;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ path, method, body });
      if (path === "/api/v1/companies/company%2F1/employees" && method === "POST") {
        expect(body).toEqual({ login: "sam", password: "secret-pass", display_name: "Sam", role: "company.admin", contact_email: "sam@example.test" });
        employee = { id: "employee/1", login: "sam@company", status: "invited", deleted_at: null, display_name: "Sam", role: "company.admin" };
        return response({ id: employee.id, status: "invited" }, 201);
      }
      if (path === "/api/v1/companies/company%2F1/employees?include_deleted=true") return response({ items: employee?.deleted_at ? [employee] : [] });
      if (path === "/api/v1/companies/company%2F1/employees") return response({ items: employee && !employee.deleted_at ? [employee] : [] });
      return response({ detail: `unhandled ${method} ${path}` }, 404);
    });
    const service = createContourService({ fetcher });
    await expect(service.createEmployee("company/1", { login: "sam", password: "secret-pass", display_name: "Sam", role: "company.admin", contact_email: "sam@example.test" })).resolves.toMatchObject({ id: "employee/1", status: "invited" });
    expect(requests.some((request) => request.path.endsWith("/employees?include_deleted=true"))).toBe(true);
    expect(requests[0]).toMatchObject({ path: "/api/v1/companies/company%2F1/employees", method: "POST" });
  });

  it("uses only registered lifecycle paths, encodes employee IDs, and verifies every resulting state", async () => {
    const companyId = "co/1";
    const employeeId = "emp/1";
    let employee: ContourRow = { id: employeeId, login: "sam", status: "active", deleted_at: null };
    const requests: Array<{ path: string; method: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      requests.push({ path, method });
      if (path === `/api/v1/companies/co%2F1/employees` || path === `/api/v1/companies/co%2F1/employees?include_deleted=true`) {
        const deletedView = path.endsWith("include_deleted=true");
        return response({ items: employee.deleted_at ? (deletedView ? [employee] : []) : (deletedView ? [] : [employee]) });
      }
      if (path === "/api/v1/employees/emp%2F1/disable" && method === "POST") employee = { ...employee, status: "disabled" };
      else if (path === "/api/v1/employees/emp%2F1/enable" && method === "POST") employee = { ...employee, status: "active" };
      else if (path === "/api/v1/employees/emp%2F1" && method === "DELETE") employee = { ...employee, status: "disabled", deleted_at: "2026-11-03T00:00:00Z" };
      else if (path === "/api/v1/employees/emp%2F1/restore" && method === "POST") employee = { ...employee, status: "disabled", deleted_at: null };
      else return response({ detail: `unregistered ${method} ${path}` }, 404);
      return response({ id: employeeId, status: employee.status, deleted: Boolean(employee.deleted_at) });
    });
    const service = createContourService({ fetcher });

    await expect(service.disableEmployee(companyId, employeeId)).resolves.toMatchObject({ status: "disabled", deleted_at: null });
    await expect(service.enableEmployee(companyId, employeeId)).resolves.toMatchObject({ status: "active", deleted_at: null });
    await expect(service.deleteEmployee(companyId, employeeId)).resolves.toMatchObject({ status: "disabled", deleted_at: "2026-11-03T00:00:00Z" });
    await expect(service.restoreEmployee(companyId, employeeId)).resolves.toMatchObject({ status: "disabled", deleted_at: null });

    expect(requests.filter((request) => request.method === "POST" || request.method === "DELETE").map(({ path, method }) => `${method} ${path}`)).toEqual([
      "POST /api/v1/employees/emp%2F1/disable",
      "POST /api/v1/employees/emp%2F1/enable",
      "DELETE /api/v1/employees/emp%2F1",
      "POST /api/v1/employees/emp%2F1/restore",
    ]);
    expect(requests.filter((request) => request.path.includes("include_deleted=true")).length).toBeGreaterThanOrEqual(8);
  });

  it("rejects a mismatched mutation ID and does not mutate a target outside the company list", async () => {
    const requests: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (path === "/api/v1/companies/co-1/employees?include_deleted=true") return response({ items: [] });
      if (path === "/api/v1/companies/co-1/employees") return response({ items: [{ id: "employee-1", status: "active", deleted_at: null }] });
      if (path === "/api/v1/employees/employee-1/disable") return response({ id: "other-employee", status: "disabled" });
      if (path.startsWith("/api/v1/companies/co-2/employees")) return response({ items: [] });
      return response({ detail: "unhandled" }, 404);
    });
    const service = createContourService({ fetcher });
    await expect(service.disableEmployee("co-1", "employee-1")).rejects.toThrow("Сервер вернул другого сотрудника");
    await expect(service.disableEmployee("co-2", "employee-1")).rejects.toThrow("не относится к компании");
    expect(requests.filter((path) => path === "/api/v1/employees/employee-1/disable")).toHaveLength(1);
  });

  it("fails closed on mismatched readback and permits an explicit retry after an API failure", async () => {
    let status = "active";
    let failMutation = true;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/api/v1/companies/co-1/employees?include_deleted=true") return response({ items: [] });
      if (path === "/api/v1/companies/co-1/employees") return response({ items: [{ id: "employee-1", status, deleted_at: null }] });
      if (path === "/api/v1/employees/employee-1/disable" && method === "POST") {
        if (failMutation) { failMutation = false; return response({ detail: "temporarily unavailable" }, 503); }
        status = "disabled";
        return response({ id: "employee-1", status: "disabled" });
      }
      return response({ detail: "unhandled" }, 404);
    });
    const service = createContourService({ fetcher });
    await expect(service.disableEmployee("co-1", "employee-1")).rejects.toMatchObject({ status: 503 });
    await expect(service.disableEmployee("co-1", "employee-1")).resolves.toMatchObject({ status: "disabled" });
    expect(fetcher.mock.calls.filter(([input]) => String(input) === "/api/v1/employees/employee-1/disable")).toHaveLength(2);

    const staleReadback = createContourService({ fetcher: vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/v1/companies/co-1/employees?include_deleted=true") return response({ items: [] });
      if (path === "/api/v1/companies/co-1/employees") return response({ items: [{ id: "employee-1", status: "active", deleted_at: null }] });
      if (path === "/api/v1/employees/employee-1/disable") return response({ id: "employee-1", status: "disabled" });
      return response({ detail: "unhandled" }, 404);
    }) });
    await expect(staleReadback.disableEmployee("co-1", "employee-1")).rejects.toThrow("Не удалось подтвердить изменение");
  });

  it("does not call the identity lifecycle route when the company scope becomes stale during preflight", async () => {
    let release: ((rows: ContourRow[]) => void) | undefined;
    const requests: string[] = [];
    const fetcher = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (path.endsWith("include_deleted=true")) return Promise.resolve(response({ items: [] }));
      if (path.endsWith("/employees")) return new Promise<Response>((resolve) => { release = () => resolve(response({ items: [{ id: "employee-1", status: "active", deleted_at: null }] })); });
      return Promise.resolve(response({ id: "employee-1", status: "disabled" }));
    });
    let current = true;
    const service = createContourService({ fetcher });
    const pending = service.disableEmployee("co-1", "employee-1", () => current);
    current = false;
    release?.([]);
    await expect(pending).rejects.toThrow("Контекст компании изменился");
    expect(requests.some((path) => path === "/api/v1/employees/employee-1/disable")).toBe(false);
  });

  it("rejects a delete readback without a deleted_at timestamp", async () => {
    let deleted = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/api/v1/companies/co-1/employees") {
        return response(deleted ? { items: [] } : { items: [{ id: "employee-1", status: deleted ? "disabled" : "active", deleted_at: null }] });
      }
      if (path === "/api/v1/companies/co-1/employees?include_deleted=true") {
        return response(deleted ? { items: [{ id: "employee-1", status: "disabled", deleted_at: null }] } : { items: [] });
      }
      if (path === "/api/v1/employees/employee-1" && method === "DELETE") {
        deleted = true;
        return response({ id: "employee-1", deleted: true, status: "disabled" });
      }
      return response({ detail: "unhandled" }, 404);
    });
    const service = createContourService({ fetcher });
    await expect(service.deleteEmployee("co-1", "employee-1")).rejects.toThrow("не совпадает состояние удаления");
  });

  it("preserves company-scoped contact and password routes and verifies employee state after each edit", async () => {
    let contact_email = "old@example.test";
    let passwordSet = false;
    const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ path, method, body });
      if (path === "/api/v1/companies/co-1/employees" || path === "/api/v1/companies/co-1/employees?include_deleted=true") {
        return response(path.endsWith("include_deleted=true") ? { items: [] } : { items: [{ id: "employee-1", status: "active", deleted_at: null, contact_email }] });
      }
      if (path === "/api/v1/companies/co-1/employees/employee-1/contact-email" && method === "PATCH") {
        contact_email = String(body?.contact_email ?? "");
        return response({ id: "employee-1", contact_email });
      }
      if (path === "/api/v1/companies/co-1/employees/employee-1/password" && method === "PUT") {
        passwordSet = body?.password === "new-password";
        return response({ id: "employee-1", password_set: passwordSet });
      }
      return response({ detail: `unhandled ${method} ${path}` }, 404);
    });
    const service = createContourService({ fetcher });
    await expect(service.updateEmployeeEmail("co-1", "employee-1", "new@example.test")).resolves.toMatchObject({ contact_email: "new@example.test" });
    await expect(service.setEmployeePassword("co-1", "employee-1", "new-password")).resolves.toMatchObject({ id: "employee-1" });
    expect(requests).toContainEqual({ path: "/api/v1/companies/co-1/employees/employee-1/contact-email", method: "PATCH", body: { contact_email: "new@example.test" } });
    expect(requests).toContainEqual({ path: "/api/v1/companies/co-1/employees/employee-1/password", method: "PUT", body: { password: "new-password" } });
    expect(passwordSet).toBe(true);
  });
});
