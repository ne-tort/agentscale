import { describe, expect, it } from "vitest";
import { createAdminService, AdminRequestError } from "./service";

type Call = { url: string; method: string; body?: unknown };

function fakeApi(respond: (call: Call) => unknown, status = 200) {
  const calls: Call[] = [];
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    const payload = respond({ url, method, body });
    if (payload instanceof Response) return payload;
    return new Response(status === 204 ? null : JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return { calls, service: createAdminService({ fetcher, getAccessToken: () => "t" }) };
}

describe("admin service", () => {
  it("reads companies into the shape the screens use", async () => {
    const { service } = fakeApi(() => ({
      items: [
        {
          id: "co_1",
          name: "Acme",
          username: "acme@agentscale.local",
          status: "active",
          active_cabinets: 2,
          employees_total: 5,
          cabinet_quota: { max_cabinets: 10, max_packages_per_cabinet: 20, max_bundle_import_mb: 50 },
          metrics: { agent_tokens_used: 1200, agent_requests: 30 },
          key_ids: ["aik_1"],
          module_ids: ["mod_1", "mod_2"],
        },
      ],
    }));
    const [company] = await service.listCompanies();
    expect(company).toMatchObject({
      id: "co_1",
      name: "Acme",
      login: "acme@agentscale.local",
      cabinets: 2,
      employees: 5,
      tokensUsed: 1200,
      requests: 30,
      keyIds: ["aik_1"],
      moduleIds: ["mod_1", "mod_2"],
      quota: { cabinets: 10, packagesPerCabinet: 20, importMb: 50 },
    });
  });

  it("creates a company through the identity route and returns its credentials", async () => {
    const { service, calls } = fakeApi(() => ({
      company: { id: "co_2", name: "Beta", username: "beta-123@agentscale.local" },
      credentials: { username: "beta-123@agentscale.local", password_set: true },
    }), 201);
    const created = await service.createCompany({ name: "Beta", password: "secret12345" });
    expect(calls[0]).toMatchObject({ url: "/api/v1/companies", method: "POST" });
    expect(calls[0].body).toEqual({ name: "Beta", password: "secret12345" });
    // The password is echoed back so the administrator can hand it over once;
    // the API never returns it.
    expect(created).toEqual({ id: "co_2", name: "Beta", login: "beta-123@agentscale.local", password: "secret12345" });
  });

  it("writes a key's companies as the key's own list", async () => {
    const { service, calls } = fakeApi(() => ({}));
    await service.setKeyCompanies("aik_1", ["co_1", "co_2"]);
    // There is no "give company X key Y" endpoint; the relationship lives on the
    // key, so that is where a change is written.
    expect(calls[0]).toMatchObject({ url: "/api/v1/admin/ai-keys/aik_1/companies", method: "PUT" });
    expect(calls[0].body).toEqual({ company_ids: ["co_1", "co_2"] });
  });

  it("reads a module's all-companies grant rather than treating it as a list", async () => {
    const { service } = fakeApi(() => ({
      items: [
        { id: "mod_1", name: "Prompts", company_grant_scope: "all", cabinet_bindings_count: 3, project_bindings_count: 4 },
        { id: "mod_2", name: "Files", company_grant_scope: "selected", company_ids: ["co_1"] },
      ],
    }));
    const [all, selected] = await service.listModules();
    expect(all).toMatchObject({ allCompanies: true, bindings: 7 });
    expect(selected).toMatchObject({ allCompanies: false, companyIds: ["co_1"] });
  });

  it("flattens the recycle bin and keeps the entity a row came from", async () => {
    const { service, calls } = fakeApi(() => ({
      companies: [{ id: "co_9", name: "Gone Co" }],
      employees: [{ id: "emp_9", name: "Ann", email: "ann@x.local" }],
      cabinets: [],
      projects: [{ id: "p_9", name: "Proj" }],
    }));
    const rows = await service.listDeleted();
    expect(rows.map((row) => row.entity)).toEqual(["companies", "employees", "projects"]);
    expect(rows[1]).toMatchObject({ name: "Ann", login: "ann@x.local" });

    await service.restoreDeleted("employees", "emp_9");
    expect(calls[1]).toMatchObject({ url: "/api/v1/admin/recycle/employees/emp_9/restore", method: "POST" });
  });

  it("surfaces the API's own message and code on failure", async () => {
    const { service } = fakeApi(
      () => new Response(JSON.stringify({ detail: "Company quota reached", code: "QUOTA" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const error = await service.createModule("X").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AdminRequestError);
    expect(error).toMatchObject({ message: "Company quota reached", status: 409, code: "QUOTA" });
  });

  it("reports a connection failure as such instead of an empty list", async () => {
    const service = createAdminService({
      fetcher: async () => {
        throw new Error("ECONNREFUSED");
      },
    });
    await expect(service.listCompanies()).rejects.toThrow(/connection failed/i);
  });
});
