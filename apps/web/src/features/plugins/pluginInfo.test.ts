// @vitest-environment jsdom
/** PluginInfo/listAdminPlugins registry identity tests. НЕ запускались (freeze). */
import { describe, expect, it, vi } from "vitest";
import {
  assertPluginStaticOrigin,
  createPluginInfoPort,
  notConnectedPluginInfo,
  type AdminPluginsApi,
  type PluginBootstrapApi,
  type PluginInfoClient,
} from "./pluginInfo";
import { PluginFrameError } from "./errors";
import { buildPluginSrc } from "./bootstrap";

const STATIC_ORIGIN = "https://plugin-origin.agentscale.dev";
const SHELL_ORIGIN = "https://agentscale.dev";
const DIGEST = `sha256:${"a".repeat(64)}`;

interface Call {
  readonly url: string;
  readonly headers: Record<string, string>;
}

/** Фейк typed-клиента: повторяет форму `createApiClient` (client.ts:305-325). */
function fakeApi(options: {
  readonly list?: unknown | Error;
  readonly me?: unknown | Error;
  readonly token?: string;
} = {}) {
  const calls: Call[] = [];
  const token = options.token ?? "access-token-secret";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = Object.fromEntries(
      Object.entries(init?.headers ?? {}).map(([key, value]) => [key, String(value)]),
    );
    calls.push({ url, headers });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  // `me()` и `listAdminPlugins()` в проде добавляет родитель в ApiClient;
  // здесь они смоделированы поверх того же fetcher'а с заголовком Authorization.
  const api = {
    async me() {
      if (options.me instanceof Error) throw options.me;
      return options.me ?? {
        active: true,
        roles: ["platform.admin"],
        contours: ["platform_admin"],
        id: "admin",
        subject: "admin",
        display_name: "admin",
      };
    },
    async authConfig() {
      return { auth_mode: "test" };
    },
    async listAdminPlugins(companyId: string) {
      if (options.list instanceof Error) throw options.list;
      const url = `/api/v1/admin/plugins/?company_id=${encodeURIComponent(companyId)}`;
      await fetcher(url, { headers: { Authorization: `Bearer ${token}`, Accept: "application/json" } });
      if (options.list === undefined) {
        return {
          company_id: "co_123",
          items: [{
            plugin_id: "mod_equipment",
            manifest_version: "1.2.0",
            artifact_digest: DIGEST,
            grant_status: "active",
            enabled: true,
            capabilities: ["hello.greetings@1"],
          }],
          enabled_modules: ["mod_equipment"],
        };
      }
      return options.list;
    },
  } as unknown as PluginInfoClient & AdminPluginsApi;

  return { api, calls, fetcher, token };
}

const env = { VITE_PLUGIN_STATIC_ORIGIN: STATIC_ORIGIN };
const opts = (api: PluginInfoClient & AdminPluginsApi) => ({
  api,
  env,
  shellOrigin: SHELL_ORIGIN,
});

const QUERY = { pluginId: "mod_equipment", companyId: "co_123" } as const;

describe("pluginInfo.ts: токен НЕ попадает в URL", () => {
  it("в URL только company_id; токен уходит заголовком Authorization", async () => {
    const { api, calls, token } = fakeApi();
    const port = createPluginInfoPort(opts(api));
    const info = await port.getPluginInfo(QUERY);
    expect(info.connected).toBe(true);
    expect(info.granted).toBe(true);
    expect(info).toMatchObject({
      manifest_version: "1.2.0",
      artifact_digest: DIGEST,
      capabilities: ["hello.greetings@1"],
      scope: { company_id: "co_123" },
    });
    expect(calls).toHaveLength(1); // только /admin/plugins (me() — локальный стаб)
    const listCall = calls[0];
    expect(listCall.url).toBe("/api/v1/admin/plugins/?company_id=co_123");
    expect(listCall.url).not.toContain("?token");
    expect(listCall.url).not.toContain("access_token");
    expect(listCall.url.toLowerCase()).not.toContain("bearer");
    expect(listCall.headers.Authorization).toBe(`Bearer ${token}`);
  });

  it("src плагина не содержит query, fragment и токена", () => {
    const src = buildPluginSrc(STATIC_ORIGIN, {
      entry: "index.html",
      basePath: "/tenant-co_123/mod_equipment/1.2.0",
    });
    expect(src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
    expect(src).not.toContain("?");
    expect(src).not.toContain("#");
    expect(src.toLowerCase()).not.toContain("token");
  });

  it("company_id кодируется перед подстановкой в query", async () => {
    const { api, calls } = fakeApi();
    const port = createPluginInfoPort(opts(api));
    await port.getPluginInfo({ ...QUERY, companyId: "co 1&x=1" });
    // Typed client кодирует один раз; порт передаёт raw companyId без double encoding.
    expect(calls[0].url).toContain("company_id=co%201%26x%3D1");
  });
});

describe("pluginInfo.ts: fail-closed «не подключён»", () => {
  it("ошибка API → не подключён, без молчаливого src", async () => {
    const { api } = fakeApi({ list: new Error("upstream 500") });
    const port = createPluginInfoPort(opts(api));
    const info = await port.getPluginInfo(QUERY);
    expect(info).toEqual(notConnectedPluginInfo());
    expect(info.connected).toBe(false);
    expect(info.granted).toBe(false);
    expect("src" in info).toBe(false);
  });

  it("ошибка /me → не подключён", async () => {
    const { api } = fakeApi({ me: new Error("unauthorized") });
    const info = await createPluginInfoPort(opts(api)).getPluginInfo(QUERY);
    expect(info.connected).toBe(false);
  });

  it("нет клиента / нет метода listAdminPlugins → не подключён", async () => {
    expect((await createPluginInfoPort({ env }).getPluginInfo(QUERY)).connected).toBe(false);
    const withoutMethod = { me: async () => ({ active: true, roles: ["platform.admin"] }) } as unknown as PluginInfoClient & AdminPluginsApi;
    expect((await createPluginInfoPort(opts(withoutMethod)).getPluginInfo(QUERY)).connected).toBe(false);
  });

  it("неверная форма ответа / чужой company_id / чужой plugin_id / digest → не подключён", async () => {
    const variants: unknown[] = [
      null,
      {},
      { company_id: "other", items: [], enabled_modules: [] },
      { company_id: "co_123", items: "not-an-array", enabled_modules: [] },
      { company_id: "co_123", items: [{ plugin_id: "mod_other", manifest_version: "1.2.0", artifact_digest: DIGEST, grant_status: "active", enabled: true, capabilities: [] }], enabled_modules: [] },
      { company_id: "co_123", items: [{ plugin_id: "mod_equipment", manifest_version: "1.2.0", artifact_digest: "not-a-digest", grant_status: "active", enabled: true, capabilities: [] }], enabled_modules: ["mod_equipment"] },
    ];
    for (const list of variants) {
      const { api } = fakeApi({ list });
      const info = await createPluginInfoPort(opts(api)).getPluginInfo(QUERY);
      expect(info.connected).toBe(false);
    }
  });

  it("не требует версию заранее и возвращает manifest_version из выбранной записи", async () => {
    const list = {
      company_id: "co_123",
      items: [{
        plugin_id: "mod_equipment", manifest_version: "9.9.9", artifact_digest: DIGEST,
        grant_status: "active", enabled: true, capabilities: [],
      }],
      enabled_modules: ["mod_equipment"],
    };
    const { api } = fakeApi({ list });
    const info = await createPluginInfoPort(opts(api)).getPluginInfo(QUERY);
    expect(info).toMatchObject({ connected: true, manifest_version: "9.9.9", artifact_digest: DIGEST });
  });

  it("grant отозван / фича-флаг выключен → connected, но granted=false", async () => {
    const revoked = {
      company_id: "co_123",
      items: [{ plugin_id: "mod_equipment", manifest_version: "1.2.0", artifact_digest: DIGEST, grant_status: "revoked", enabled: true, capabilities: ["hello.greetings@1"] }],
      enabled_modules: ["mod_equipment"],
    };
    const { api: api1 } = fakeApi({ list: revoked });
    const revokedInfo = await createPluginInfoPort(opts(api1)).getPluginInfo(QUERY);
    expect(revokedInfo.connected).toBe(true);
    expect(revokedInfo.granted).toBe(false);

    const disabled = { ...revoked, enabled_modules: [] as string[] };
    const { api: api2 } = fakeApi({ list: disabled });
    expect((await createPluginInfoPort(opts(api2)).getPluginInfo(QUERY)).granted).toBe(false);
  });

  it("обычный сотрудник (не platform.admin) не получает «подключён»", async () => {
    const { api } = fakeApi({ me: { active: true, roles: ["employee"], contours: ["employee"] } });
    const info = await createPluginInfoPort(opts(api)).getPluginInfo(QUERY);
    expect(info.connected).toBe(false);
  });

  it("невалидный VITE_PLUGIN_STATIC_ORIGIN внутри порта → «не подключён»", async () => {
    const { api } = fakeApi();
    const port = createPluginInfoPort({
      api,
      env: { VITE_PLUGIN_STATIC_ORIGIN: "http://insecure.example" },
      shellOrigin: SHELL_ORIGIN,
    });
    expect((await port.getPluginInfo(QUERY)).connected).toBe(false);
  });
});

describe("assertPluginStaticOrigin: точный https-origin, не shell", () => {
  it("валидный origin из import.meta.env принимается", () => {
    expect(assertPluginStaticOrigin({ env, shellOrigin: SHELL_ORIGIN })).toBe(STATIC_ORIGIN);
  });

  it("пустой/отсутствующий env → ошибка (fail-closed)", () => {
    expect(() => assertPluginStaticOrigin({ env: {}, shellOrigin: SHELL_ORIGIN })).toThrowError(PluginFrameError);
  });

  it("http, wildcard, path/query/fragment, userinfo → ошибка", () => {
    const invalid = [
      "http://plugin-origin.agentscale.dev",
      "https://*.agentscale.dev",
      `${STATIC_ORIGIN}/path`,
      `${STATIC_ORIGIN}/?token=x`,
      `${STATIC_ORIGIN}/#frag`,
      "https://u:p@plugin-origin.agentscale.dev",
    ];
    for (const raw of invalid) {
      expect(() => assertPluginStaticOrigin({
        env: { VITE_PLUGIN_STATIC_ORIGIN: raw },
        shellOrigin: SHELL_ORIGIN,
      })).toThrowError(PluginFrameError);
    }
  });

  it("origin не совпадает с shell: совпадение → ошибка", () => {
    expect(() => assertPluginStaticOrigin({
      env: { VITE_PLUGIN_STATIC_ORIGIN: SHELL_ORIGIN },
      shellOrigin: SHELL_ORIGIN,
    })).toThrowError(PluginFrameError);
    // И наоборот: отдельный origin не равен оболочке.
    expect(assertPluginStaticOrigin({ env, shellOrigin: SHELL_ORIGIN })).not.toBe(SHELL_ORIGIN);
  });

  it("legacy static-origin check не использует window.location как trust", () => {
    // jsdom location остаётся http://localhost:3000 и не меняет решение.
    expect(assertPluginStaticOrigin({ env })).toBe(STATIC_ORIGIN);
  });
});

describe("pluginInfo.ts: employee/public bootstrap (task01)", () => {
  const payload = {
    plugin_id: "mod_equipment",
    manifest_version: "1.2.0",
    artifact_digest: DIGEST,
    static_origin: STATIC_ORIGIN,
    plugin_shell_origin: SHELL_ORIGIN,
    frontend_entry: "index.html",
    frontend_base_path: "/tenant-co_123/mod_equipment/1.2.0",
    route_path: "/",
    capabilities: ["hello.greetings@1"],
    rpc_operations: [],
    public_pages: ["/"],
    scope: { company_id: "co_123" },
    grant_expires_at: "2027-01-01T00:00:00Z",
  };

  function bootstrapApi(body: unknown = payload) {
    return {
      me: vi.fn(async () => { throw new Error("me() не должен вызываться для bootstrap"); }),
      getPluginBootstrap: vi.fn(async () => body),
      getPublicPluginBootstrap: vi.fn(async () => body),
    } as unknown as PluginInfoClient & AdminPluginsApi & PluginBootstrapApi;
  }

  it("private bootstrap не требует platform.admin и не вызывает me()", async () => {
    const api = bootstrapApi();
    const info = await createPluginInfoPort(opts(api)).getPluginInfo(QUERY);
    expect(info).toMatchObject({
      connected: true,
      granted: true,
      manifest_version: "1.2.0",
      artifact_digest: DIGEST,
      capabilities: ["hello.greetings@1"],
      scope: { company_id: "co_123" },
      grant_expires_at: "2027-01-01T00:00:00Z",
      static_origin: STATIC_ORIGIN,
      plugin_shell_origin: SHELL_ORIGIN,
      frontend_entry: "index.html",
      frontend_base_path: "/tenant-co_123/mod_equipment/1.2.0",
      route_path: "/",
      rpc_operations: [],
      public_pages: ["/"],
    });
    expect(api.me).not.toHaveBeenCalled();
    expect(api.getPluginBootstrap).toHaveBeenCalledWith("co_123", "mod_equipment", "/");
  });

  it("public bootstrap использует public endpoint и не выдаёт grant", async () => {
    const api = bootstrapApi();
    const info = await createPluginInfoPort(opts(api)).getPluginInfo({ ...QUERY, publicPage: true });
    expect(info.connected).toBe(true);
    expect(info.granted).toBe(false);
    expect(api.getPublicPluginBootstrap).toHaveBeenCalledWith("co_123", "mod_equipment", "/");
    expect(api.getPluginBootstrap).not.toHaveBeenCalled();
  });

  it("nested public route использует custom entry/base_path и сохраняет signed @1.0 operations", async () => {
    const operation = {
      capability_id: "hello.greetings",
      capability_version: "1.0",
      operation: "list",
      http_method: "GET",
      backend_path: "/api/greetings/list",
      public: true,
    };
    const api = bootstrapApi({
      ...payload,
      frontend_entry: "shell.html",
      frontend_base_path: "/signed/custom-base",
      route_path: "/docs/welcome",
      public_pages: ["/docs/welcome"],
      capabilities: ["hello.greetings@1.0"],
      rpc_operations: [operation],
    });
    const info = await createPluginInfoPort(opts(api)).getPluginInfo({
      ...QUERY, publicPage: true, routePath: "/docs/welcome",
    });
    expect(info).toMatchObject({
      connected: true,
      static_origin: STATIC_ORIGIN,
      plugin_shell_origin: SHELL_ORIGIN,
      frontend_entry: "shell.html",
      frontend_base_path: "/signed/custom-base",
      route_path: "/docs/welcome",
      rpc_operations: [operation],
    });
    expect(api.getPublicPluginBootstrap).toHaveBeenCalledWith("co_123", "mod_equipment", "/docs/welcome");
  });

  it("public без public endpoint не использует admin/me fallback", async () => {
    const api = {
      me: vi.fn(async () => ({ active: true, roles: ["platform.admin"] })),
      listAdminPlugins: vi.fn(async () => ({})),
      getPluginBootstrap: vi.fn(async () => payload),
    } as unknown as PluginInfoClient & AdminPluginsApi & PluginBootstrapApi;
    const info = await createPluginInfoPort(opts(api)).getPluginInfo({ ...QUERY, publicPage: true });
    expect(info.connected).toBe(false);
    expect(api.me).not.toHaveBeenCalled();
    expect(api.listAdminPlugins).not.toHaveBeenCalled();
    expect(api.getPluginBootstrap).not.toHaveBeenCalled();
  });

  it("отсутствие trusted frontend/route/operations fields или небезопасный path → fail-closed", async () => {
    for (const field of ["static_origin", "plugin_shell_origin", "frontend_entry", "frontend_base_path", "route_path", "rpc_operations", "public_pages"]) {
      const body = { ...payload } as Record<string, unknown>;
      delete body[field];
      expect((await createPluginInfoPort(opts(bootstrapApi(body))).getPluginInfo(QUERY)).connected).toBe(false);
    }
    for (const body of [
      { ...payload, static_origin: "http://insecure.example" },
      { ...payload, static_origin: SHELL_ORIGIN },
      { ...payload, plugin_shell_origin: "http://agentscale.dev" },
      { ...payload, plugin_shell_origin: "https://*.agentscale.dev" },
      { ...payload, plugin_shell_origin: STATIC_ORIGIN },
      { ...payload, frontend_base_path: "/tenant/../admin" },
      { ...payload, frontend_entry: "../index.html" },
      { ...payload, route_path: "/other" },
    ]) {
      expect((await createPluginInfoPort(opts(bootstrapApi(body))).getPluginInfo(QUERY)).connected).toBe(false);
    }
  });

  it("невалидный digest или scope mismatch → fail-closed", async () => {
    expect((await createPluginInfoPort(opts(bootstrapApi({ ...payload, artifact_digest: "bad" })))
      .getPluginInfo(QUERY)).connected).toBe(false);
    expect((await createPluginInfoPort(opts(bootstrapApi({ ...payload, scope: { company_id: "co_other" } })))
      .getPluginInfo(QUERY)).connected).toBe(false);
    expect((await createPluginInfoPort(opts(bootstrapApi({ ...payload, plugin_id: "other" })))
      .getPluginInfo(QUERY)).connected).toBe(false);
  });
});
