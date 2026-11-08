// @vitest-environment jsdom
/**
 * Тесты реализации `PluginInfoPort` (`pluginInfo.ts`).
 *
 * ВАЖНО О РАСПОЛОЖЕНИИ: по правилам задачи в зоне 13 (`apps/web/src/features/plugins/`)
 * разрешено создать ОДИН новый файл — `pluginInfo.ts`, и НЕ редактировать существующие.
 * Поэтому тесты лежат в собственном каталоге субагента
 * (`apps/api/tests/unit/plugins/platform_admin/`). Родителю при снятии freeze
 * верификации нужно ПЕРЕНЕСТИ этот файл в
 * `apps/web/src/features/plugins/pluginInfo.test.ts` — тогда импорт `./pluginInfo`
 * станет корректным и vitest подхватит его (include: `src/**\/*.{test,spec}.{ts,tsx}`).
 *
 * Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze миграции).
 *
 * Импорты ниже (`./pluginInfo`, `./errors`) рассчитаны на ЦЕЛЕВОЕ расположение.
 * Перенос (делает родитель): скопировать файл в
 *   `apps/web/src/features/plugins/pluginInfo.test.ts`
 * и удалить исходник из этого каталога; править импорты НЕ нужно — они станут
 * корректными автоматически. До переноса файл лежит здесь только как текст
 * теста и не должен подхватываться pytest/vitest.
 */
import { describe, expect, it, vi } from "vitest";
import {
  assertPluginStaticOrigin,
  createPluginInfoPort,
  notConnectedPluginInfo,
  pluginSrcFor,
  type AdminPluginsApi,
  type PluginInfoClient,
} from "./pluginInfo";
import { PluginFrameError } from "./errors";

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
      const url = `/api/v1/admin/plugins/?company_id=${companyId}`;
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
  locationOrigin: SHELL_ORIGIN,
});

const QUERY = { pluginId: "mod_equipment", companyId: "co_123", version: "1.2.0" } as const;

describe("pluginInfo.ts: токен НЕ попадает в URL", () => {
  it("в URL только company_id; токен уходит заголовком Authorization", async () => {
    const { api, calls, token } = fakeApi();
    const port = createPluginInfoPort(opts(api));
    const info = await port.getPluginInfo(QUERY);
    expect(info.connected).toBe(true);
    expect(info.granted).toBe(true);
    expect(calls).toHaveLength(1); // только /admin/plugins (me() — локальный стаб)
    const listCall = calls[0];
    expect(listCall.url).toBe("/api/v1/admin/plugins/?company_id=co_123");
    expect(listCall.url).not.toContain("?token");
    expect(listCall.url).not.toContain("access_token");
    expect(listCall.url.toLowerCase()).not.toContain("bearer");
    expect(listCall.headers.Authorization).toBe(`Bearer ${token}`);
  });

  it("src плагина не содержит query, fragment и токена", () => {
    const src = pluginSrcFor(STATIC_ORIGIN, QUERY);
    expect(src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
    expect(src).not.toContain("?");
    expect(src).not.toContain("#");
    expect(src.toLowerCase()).not.toContain("token");
  });

  it("company_id кодируется перед подстановкой в query", async () => {
    const { api, calls } = fakeApi();
    const port = createPluginInfoPort(opts(api));
    await port.getPluginInfo({ ...QUERY, companyId: "co 1&x=1" });
    // Порт передаёт уже закодированное значение; «&» не должен стать новым параметром.
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

  it("неверная форма ответа / чужой company_id / чужой plugin_id / не та версия → не подключён", async () => {
    const variants: unknown[] = [
      null,
      {},
      { company_id: "other", items: [], enabled_modules: [] },
      { company_id: "co_123", items: "not-an-array", enabled_modules: [] },
      { company_id: "co_123", items: [{ plugin_id: "mod_other", manifest_version: "1.2.0", artifact_digest: DIGEST, grant_status: "active", enabled: true }], enabled_modules: [] },
      { company_id: "co_123", items: [{ plugin_id: "mod_equipment", manifest_version: "9.9.9", artifact_digest: DIGEST, grant_status: "active", enabled: true }], enabled_modules: ["mod_equipment"] },
      { company_id: "co_123", items: [{ plugin_id: "mod_equipment", manifest_version: "1.2.0", artifact_digest: "not-a-digest", grant_status: "active", enabled: true }], enabled_modules: ["mod_equipment"] },
    ];
    for (const list of variants) {
      const { api } = fakeApi({ list });
      const info = await createPluginInfoPort(opts(api)).getPluginInfo(QUERY);
      expect(info.connected).toBe(false);
    }
  });

  it("grant отозван / фича-флаг выключен → connected, но granted=false", async () => {
    const revoked = {
      company_id: "co_123",
      items: [{ plugin_id: "mod_equipment", manifest_version: "1.2.0", artifact_digest: DIGEST, grant_status: "revoked", enabled: true }],
      enabled_modules: ["mod_equipment"],
    };
    const { api: api1 } = fakeApi({ list: revoked });
    expect((await createPluginInfoPort(opts(api1)).getPluginInfo(QUERY)).granted).toBe(false);

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

  it("window.location.origin используется как shell по умолчанию", () => {
    // jsdom: window.location.origin = http://localhost:3000; статический origin https — валиден.
    expect(assertPluginStaticOrigin({ env })).toBe(STATIC_ORIGIN);
  });
});
