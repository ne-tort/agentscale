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

describe("keys are an endpoint and a secret", () => {
  it("sends the endpoint instead of a provider", async () => {
    const { calls, service } = fakeApi(() => ({ id: "aik_1" }));
    await service.createKey({
      name: "Main",
      baseUrl: "https://api.example.com/v1",
      secret: "sk-test",
      companyIds: ["co_1"],
    });
    expect(calls[0]).toMatchObject({ url: "/api/v1/admin/ai-keys", method: "POST" });
    expect(calls[0].body).toEqual({
      name: "Main",
      base_url: "https://api.example.com/v1",
      secret: "sk-test",
      company_ids: ["co_1"],
      // No `provider`: the address identifies the API. Sending one would let the
      // server fall back to a catalog entry the administrator never chose.
    });
  });

  it("sends the advanced fields only when they were filled in", async () => {
    const { calls, service } = fakeApi(() => ({ id: "aik_1" }));
    await service.createKey({
      name: "Gateway",
      baseUrl: "https://gw.example.com",
      secret: "sk",
      companyIds: [],
      modelsPath: "/v1/models",
      chatCompletionsPath: "/v1/chat/completions",
      authScheme: "x-api-key",
    });
    expect(calls[0].body).toEqual({
      name: "Gateway",
      base_url: "https://gw.example.com",
      secret: "sk",
      company_ids: [],
      models_path: "/v1/models",
      chat_completions_path: "/v1/chat/completions",
      auth_scheme: "x-api-key",
    });
  });

  it("reads the endpoint back so the list can say where a key points", async () => {
    const { service } = fakeApi(() => ({
      items: [
        {
          id: "aik_1",
          name: "Main",
          status: "active",
          company_ids: ["co_1"],
          has_secret: true,
          base_url: "https://api.example.com/v1",
          models_path: "/models",
          auth_scheme: "bearer",
        },
        { id: "aik_2", name: "Legacy", provider: "cursor" },
      ],
    }));
    const [keyed, legacy] = await service.listKeys();
    expect(keyed).toMatchObject({
      baseUrl: "https://api.example.com/v1",
      modelsPath: "/models",
      authScheme: "bearer",
    });
    // A key from before endpoints were a field still lists, with no address.
    expect(legacy.baseUrl).toBeNull();
  });
});

describe("a module's company scope", () => {
  it("is read from the admin row", async () => {
    const { service } = fakeApi(() => ({
      items: [
        { id: "mod_management", name: "Управление", company_grant_scope: "all", company_ids: [] },
        { id: "mod_equipment", name: "Подбор техники", company_grant_scope: "selected", company_ids: ["co_1"] },
      ],
    }));
    const [all, selected] = await service.listModules();
    expect(all.allCompanies).toBe(true);
    expect(selected.allCompanies).toBe(false);
  });

  it("is written as an ordinary PATCH and read back", async () => {
    const { calls, service } = fakeApi(() => ({ id: "mod_1", company_grant_scope: "all" }));
    await expect(service.setModuleScope("mod_1", "all")).resolves.toBe("all");
    expect(calls[0]).toMatchObject({
      url: "/api/v1/admin/modules/mod_1",
      method: "PATCH",
      body: { company_grant_scope: "all" },
    });
  });

  it("believes the server when it answers with a different scope", async () => {
    // A module the platform refuses to widen must not leave the control showing
    // the value the administrator picked.
    const { service } = fakeApi(() => ({ id: "mod_1", company_grant_scope: "selected" }));
    await expect(service.setModuleScope("mod_1", "all")).resolves.toBe("selected");
  });
});
