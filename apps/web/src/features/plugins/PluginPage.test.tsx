// @vitest-environment jsdom
/**
 * A9: страница плагина в основном web-приложении.
 *
 * Проверяется: нет `?token=`/`access_token` в src и разметке; отказ при
 * отсутствии src; `allow-same-origin` отсутствует; состояния
 * loading / notGranted / notConnected / loadError / retry; ru/en-ключи
 * согласованы; заголовок содержит pluginId; «Назад» ведёт в проект;
 * fail-closed при чужом origin и токене не рендерит iframe.
 *
 * Тесты НЕ запускались (freeze верификации миграции).
 */
import { act, Fragment, isValidElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import pluginStrings from "../../i18n/units/plugins";
import { buildCatalogue } from "../../i18n/catalogue";
import { PLUGIN_SANDBOX } from "./PluginFrame";
import {
  OPAQUE_ORIGIN,
  PLUGIN_RPC_PROTOCOL,
  type PluginChannel,
  type PluginChannelOptions,
  type PluginRequestHandler,
} from "./rpc";
import {
  PluginPage,
  pluginSrcFailure,
  resolvePluginPagePhase,
  DEFAULT_PLUGIN_BACK_PATH,
  type PluginPageProps,
} from "./PluginPage";
import type { ConnectedPluginInfo, PluginInfo, PluginInfoPort } from "./usePluginFrame";
import {
  PLUGIN_ROUTE_PATHS,
  PUBLIC_PLUGIN_ROUTE_PATHS,
  PLUGIN_ROUTE_ELEMENTS,
  PluginRoutes,
} from "../../app/plugin-routes";

const STATIC_ORIGIN = "https://plugin-origin.agentscale.dev";
const SHELL_ORIGIN = "https://agentscale.dev";
const VERSION = "1.2.0";
const DIGEST = `sha256:${"a".repeat(64)}`;
const OTHER_DIGEST = `sha256:${"b".repeat(64)}`;
const INSTANCE_ID = "instance-1234567890";
const NONCE = "nonce-1234567890";
const CAPABILITIES = ["hello.greetings@1"] as const;
const SAFE_SRC = `${STATIC_ORIGIN}/tenant-co_123/mod_equipment/${VERSION}/index.html`;

function connectedInfo(overrides: Partial<ConnectedPluginInfo> = {}): ConnectedPluginInfo {
  return {
    connected: true,
    granted: true,
    manifest_version: VERSION,
    artifact_digest: DIGEST,
    capabilities: CAPABILITIES,
    scope: { company_id: "co_123" },
    static_origin: STATIC_ORIGIN,
    plugin_shell_origin: SHELL_ORIGIN,
    frontend_entry: "index.html",
    frontend_base_path: `/tenant-co_123/mod_equipment/${VERSION}`,
    route_path: "/",
    rpc_operations: [],
    public_pages: ["/"],
    ...overrides,
  };
}

/** Порт возвращает registry identity/grant, но никогда capability token. */
function infoPort(info: PluginInfo | Error): PluginInfoPort {
  return {
    getPluginInfo: vi.fn(async () => {
      if (info instanceof Error) throw info;
      return info;
    }),
  };
}

const grantedPort = () => infoPort(connectedInfo());
const config = { staticOrigin: STATIC_ORIGIN, shellOrigin: SHELL_ORIGIN };

/**
 * Канал-заглушка: сразу «готов», без таймеров и postMessage. Иначе реальный
 * `createPluginChannel` запустил бы 10-секундный handshake и статус хука успел
 * бы стать `error` посреди проверок разметки.
 */
function inertChannel(expectedOrigin: string): PluginChannel {
  let disposed = false;
  return {
    expectedOrigin,
    ready: true,
    get disposed() { return disposed; },
    handshake: () => Promise.resolve({
      protocol: PLUGIN_RPC_PROTOCOL,
      type: "plugin.ready",
      plugin_id: "mod_equipment",
      version: VERSION,
      artifact_digest: DIGEST,
      instance_id: INSTANCE_ID,
      nonce: NONCE,
    }),
    request: <T,>() => Promise.resolve(undefined as T),
    dispose: () => { disposed = true; },
  };
}
const inertChannelFactory = (_frame: HTMLIFrameElement, origin: string): PluginChannel => inertChannel(origin ?? OPAQUE_ORIGIN);

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mountPage(
  initialPath = "/plugins/mod_equipment/co_123",
  props: Partial<PluginPageProps> = {},
): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  const pageProps: PluginPageProps = {
    infoPort: grantedPort(),
    config,
    channelFactory: inertChannelFactory,
    ...props,
  };
  const element: ReactElement = <MemoryRouter initialEntries={[initialPath]}>
    <SettingsProvider>
      <Routes>
        <Route path={PLUGIN_ROUTE_PATHS[0]} element={<PluginPage {...pageProps} />} />
        <Route path={PLUGIN_ROUTE_PATHS[1]} element={<PluginPage {...pageProps} />} />
        <Route path={PUBLIC_PLUGIN_ROUTE_PATHS[0]} element={<PluginPage {...pageProps} publicPage />} />
        <Route path={PUBLIC_PLUGIN_ROUTE_PATHS[1]} element={<PluginPage {...pageProps} publicPage />} />
        <Route path="*" element={<div data-testid="not-found">404</div>} />
      </Routes>
      <LocationProbe />
    </SettingsProvider>
  </MemoryRouter>;
  await act(async () => { root?.render(element); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

function text(): string { return host?.textContent ?? ""; }
function markup(): string { return host?.innerHTML ?? ""; }
function frame(): HTMLIFrameElement | null { return host?.querySelector<HTMLIFrameElement>("iframe") ?? null; }

function click(selector: string): void {
  const element = host?.querySelector<HTMLElement>(selector);
  if (!element) throw new Error(`Missing element ${selector}`);
  element.click();
}

afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  root = undefined;
  host?.remove();
  host = undefined;
  document.body.replaceChildren();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

beforeEach(() => {
  document.body.replaceChildren();
});

describe("plugin-routes.tsx: маршруты для родителя", () => {
  it("PLUGIN_ROUTE_PATHS ровно два требуемых пути", () => {
    expect(PLUGIN_ROUTE_PATHS).toEqual([
      "/plugins/:pluginId/:companyId",
      "/plugins/:pluginId/:companyId/*",
    ]);
  });

  it("PLUGIN_ROUTE_ELEMENTS — Fragment с двумя <Route>, а не самостоятельный <Routes>", () => {
    expect(PLUGIN_ROUTE_ELEMENTS.type).toBe(Fragment);
    const children = (PLUGIN_ROUTE_ELEMENTS.props as { children: ReactElement[] }).children;
    expect(children).toHaveLength(2);
    expect(children.map((child) => (child.props as { path: string }).path)).toEqual(PLUGIN_ROUTE_PATHS);
    // Все дети — валидные React-элементы; ни один не монтирует роутер сам
    // и не является <Routes>. Роутер остаётся единственным в AppRouter.
    expect(children.every((child) => isValidElement(child))).toBe(true);
    expect(children.every((child) => child.type !== Routes)).toBe(true);
    expect(children.every((child) => typeof child.type !== "string")).toBe(true);
  });

  it("PluginRoutes() возвращает тот же готовый фрагмент", () => {
    expect(PluginRoutes()).toBe(PLUGIN_ROUTE_ELEMENTS);
  });

  it("SPA-fallback НЕ перехватывает /plugins/... (маршруты объявлены до *)", async () => {
    await mountPage("/plugins/mod_equipment/co_123");
    expect(host?.querySelector("[data-testid='not-found']")).toBeNull();
    expect(host?.querySelector(".plugin-page")).not.toBeNull();
  });

  it("вложенный deep-link передаёт nested routePath в bootstrap", async () => {
    const port = infoPort(connectedInfo({ route_path: "/section/42" }));
    await mountPage("/plugins/mod_equipment/co_123/section/42", { infoPort: port });
    expect(host?.querySelector("[data-testid='not-found']")).toBeNull();
    expect(host?.querySelector(".plugin-page")).not.toBeNull();
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-id")).toBe("mod_equipment");
    expect(port.getPluginInfo).toHaveBeenCalledWith(expect.objectContaining({ routePath: "/section/42" }));
    expect(frame()?.getAttribute("src")).toBe(SAFE_SRC);
  });
});

describe("i18n/units/plugins.ts: ru/en согласованы", () => {
  it("наборы ключей совпадают и все строки непустые", () => {
    expect(Object.keys(pluginStrings.ru).sort()).toEqual(Object.keys(pluginStrings.en).sort());
    const catalogue = buildCatalogue();
    for (const key of Object.keys(pluginStrings.ru)) {
      expect(pluginStrings.ru[key].trim(), `ru:${key}`).not.toBe("");
      expect(pluginStrings.en[key].trim(), `en:${key}`).not.toBe("");
      expect(catalogue.ru[`plugins.${key}`]).toBe(pluginStrings.ru[key]);
      expect(catalogue.en[`plugins.${key}`]).toBe(pluginStrings.en[key]);
    }
  });

  it("все тексты состояний и retry приходят из юнита, а не хардкодом", () => {
    for (const key of ["loading", "loadError", "notGranted", "notConnected", "retry", "originMismatch", "timeout", "unsupportedProtocol"]) {
      expect(pluginStrings.ru[key], key).toBeTruthy();
      expect(pluginStrings.en[key], key).toBeTruthy();
    }
  });

  it("русские тексты UI, а не технические коды", () => {
    expect(pluginStrings.ru.loading).toContain("Загружаем");
    expect(pluginStrings.ru.notGranted).toContain("не выдан");
    expect(pluginStrings.ru.notConnected).toContain("не подключён");
    expect(pluginStrings.ru.loadError).toContain("не загрузился");
  });
});

describe("PluginPage: изоляция iframe", () => {
  it("src безопасного плагина НЕ содержит ?token= / access_token / capability_token", async () => {
    await mountPage();
    expect(frame()?.getAttribute("src")).toBe(SAFE_SRC);
    expect(frame()?.getAttribute("src")?.toLowerCase()).not.toContain("token");
    expect(markup()).not.toContain("?token=");
    expect(markup()).not.toContain("access_token");
    expect(markup()).not.toContain("capability_token");
    expect(markup()).not.toContain("authorization");
  });

  it("custom frontend_entry/base_path берутся из bootstrap, не из tenant template", async () => {
    await mountPage("/plugins/mod_equipment/co_123", {
      infoPort: infoPort(connectedInfo({
        manifest_version: "2.3.4",
        frontend_entry: "shell.html",
        frontend_base_path: "/signed/release",
      })),
    });
    expect(frame()?.getAttribute("src")).toBe(`${STATIC_ORIGIN}/signed/release/shell.html`);
    expect(frame()?.getAttribute("data-plugin-version")).toBe("2.3.4");
  });

  it("PluginFrame получает manifest-scoped onRequest, а не generic capability.call", async () => {
    const operation = {
      capability_id: "hello.greetings",
      capability_version: "1.0",
      operation: "list",
      http_method: "GET",
      backend_path: "/api/greetings/list",
      public: false,
    } as const;
    const operationTransport = vi.fn(async () => ({ items: [] }));
    let channelOptions: PluginChannelOptions | undefined;
    const channelFactory = (_frame: HTMLIFrameElement, origin: string, options: PluginChannelOptions) => {
      channelOptions = options;
      return inertChannel(origin);
    };
    await mountPage("/plugins/mod_equipment/co_123", {
      infoPort: infoPort(connectedInfo({
        capabilities: ["hello.greetings@1.0"],
        rpc_operations: [operation],
      })),
      operationTransport,
      channelFactory,
    });
    expect(channelOptions?.onRequest).toEqual(expect.any(Function));
    const request: Parameters<PluginRequestHandler>[0] = {
      protocol: PLUGIN_RPC_PROTOCOL,
      type: "plugin.rpc.request",
      plugin_id: "mod_equipment",
      version: VERSION,
      artifact_digest: DIGEST,
      instance_id: INSTANCE_ID,
      nonce: NONCE,
      request_id: "request-1",
      method: "capability.call",
      capability: { id: "hello.greetings", version: "1.0" },
      context: { company_id: "co_123" },
      idempotency_key: "request-1",
      input: { method: "list", params: {} },
    };
    await expect(channelOptions!.onRequest!(request)).resolves.toEqual({ items: [] });
    expect(operationTransport).toHaveBeenCalledWith(expect.objectContaining({ declaration: operation }));
    await expect(channelOptions!.onRequest!({
      ...request,
      input: { method: "not_signed", params: {} },
    })).rejects.toMatchObject({ reason: "OPERATION_NOT_DECLARED", rpcCode: "CAPABILITY_DENIED" });
    expect(operationTransport).toHaveBeenCalledTimes(1);
  });

  it("capability-токен НЕ попадает во frontend: registry identity есть, секретов в DOM нет", async () => {
    const info = connectedInfo({ artifact_digest: OTHER_DIGEST });
    await mountPage("/plugins/mod_equipment/co_123", { infoPort: infoPort(info) });
    // Контракт порта содержит identity/capabilities, но не capability-токен.
    expect(Object.keys(info).sort()).toEqual([
      "artifact_digest", "capabilities", "connected", "granted", "manifest_version", "scope",
      "static_origin", "plugin_shell_origin", "frontend_entry", "frontend_base_path", "route_path", "rpc_operations", "public_pages",
    ].sort());
    expect(JSON.stringify(info)).not.toMatch(/token|secret|claims|authorization/i);
    // Доверенный digest передаётся ТОЛЬКО в handoff name; URL и токены запрещены.
    expect(frame()?.getAttribute("name")).toContain(OTHER_DIGEST);
    expect(frame()?.getAttribute("src")).not.toContain(OTHER_DIGEST);
    for (const marker of ["capability_token", "plugin_claims", "access_token", "?token="]) {
      expect(markup()).not.toContain(marker);
    }
  });

  it("sandbox='allow-scripts allow-forms', allow-same-origin ОТСУТСТВУЕТ", async () => {
    await mountPage();
    expect(frame()?.getAttribute("sandbox")).toBe(PLUGIN_SANDBOX);
    expect(frame()?.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame()?.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame()?.hasAttribute("credentialless")).toBe(true);
  });

  it("при отсутствии src ошибка и iframe НЕ рендерится", async () => {
    await mountPage("/plugins/mod_equipment/co_123", { infoPort: infoPort(new Error("fail")) });
    expect(host?.querySelector(".plugin-page__error")).not.toBeNull();
    expect(host?.textContent).toContain(pluginStrings.ru.loadError);
    expect(frame()).toBeNull();
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-blocked")).toBe("true");
  });

  it("fail-closed: src с ?token= → ошибка, iframe не смонтирован", () => {
    expect(pluginSrcFailure(`${SAFE_SRC}?token=leak`, STATIC_ORIGIN)).toBe("token");
    expect(pluginSrcFailure(`${SAFE_SRC}?access_token=leak`, STATIC_ORIGIN)).toBe("token");
    expect(pluginSrcFailure(SAFE_SRC, "")).toBe("origin");
    expect(pluginSrcFailure("https://evil.example/x/index.html", STATIC_ORIGIN)).toBe("origin");
    expect(pluginSrcFailure(`https://${new URL(SHELL_ORIGIN).host}/x/index.html`, SHELL_ORIGIN, SHELL_ORIGIN)).toBe("origin");
    expect(pluginSrcFailure("", STATIC_ORIGIN)).toBe("missing");
    expect(pluginSrcFailure(SAFE_SRC, STATIC_ORIGIN)).toBeUndefined();
  });

  it("заголовок содержит pluginId, указан companyId и aria-labelledby", async () => {
    await mountPage();
    const heading = host?.querySelector<HTMLElement>("#plugin-page-title");
    expect(heading?.textContent).toContain("mod_equipment");
    expect(host?.querySelector(".plugin-page")?.getAttribute("aria-labelledby")).toBe("plugin-page-title");
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-company")).toBe("co_123");
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-id")).toBe("mod_equipment");
  });

  it("кнопка «Назад» ведёт в проект/кабинет", async () => {
    await mountPage();
    const back = host?.querySelector<HTMLAnchorElement>(".plugin-page__back");
    expect(back?.textContent).toContain("Назад");
    expect(back?.getAttribute("href")).toBe(DEFAULT_PLUGIN_BACK_PATH);
    expect(back?.getAttribute("href")).toBe("/projects");
    expect(back?.getAttribute("aria-label")).toContain("Проекты");
    await act(async () => { click(".plugin-page__back"); });
    expect(host?.querySelector("[data-testid='location']")?.textContent).toBe(DEFAULT_PLUGIN_BACK_PATH);
  });
});

describe("PluginPage: состояния и retry", () => {
  it("loading: русский текст из юнита, статус aria-live, iframe ещё не смонтирован", async () => {
    let resolveInfo!: (info: PluginInfo) => void;
    const pending: PluginInfoPort = {
      getPluginInfo: vi.fn(() => new Promise<PluginInfo>((resolve) => { resolveInfo = resolve; })),
    };
    await mountPage("/plugins/mod_equipment/co_123", { infoPort: pending });
    expect(text()).toContain(pluginStrings.ru.loading);
    expect(host?.querySelector("[role='status']")?.getAttribute("aria-live")).toBe("polite");
    expect(frame()).toBeNull();
    await act(async () => { resolveInfo(connectedInfo()); await Promise.resolve(); });
  });

  it("notGranted: «Доступ к плагину не выдан», iframe нет", async () => {
    await mountPage("/plugins/mod_equipment/co_123", {
      infoPort: infoPort(connectedInfo({ granted: false })),
    });
    expect(text()).toContain(pluginStrings.ru.notGranted);
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-status")).toBe("notGranted");
    expect(frame()).toBeNull();
  });

  it("notConnected: «Плагин не подключён к компании», iframe нет", async () => {
    await mountPage("/plugins/mod_equipment/co_123", {
      infoPort: infoPort({ connected: false, granted: false }),
    });
    expect(text()).toContain(pluginStrings.ru.notConnected);
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-status")).toBe("notConnected");
    expect(frame()).toBeNull();
  });

  it("loadError: русский текст, role=alert и aria-live=assertive", async () => {
    await mountPage("/plugins/mod_equipment/co_123", { infoPort: infoPort(new Error("registry down")) });
    expect(text()).toContain(pluginStrings.ru.loadError);
    const alert = host?.querySelector(".plugin-page__error");
    expect(alert?.getAttribute("role")).toBe("alert");
    expect(alert?.getAttribute("aria-live")).toBe("assertive");
    expect(frame()).toBeNull();
  });

  it("retry: кнопка «Повторить» перезапускает загрузку через порт", async () => {
    const port = infoPort(connectedInfo({ granted: false }));
    await mountPage("/plugins/mod_equipment/co_123", { infoPort: port });
    const retry = host?.querySelector<HTMLButtonElement>(".plugin-page__error button");
    expect(retry?.textContent).toBe(pluginStrings.ru.retry);
    expect(port.getPluginInfo).toHaveBeenCalledTimes(1);
    await act(async () => { retry?.click(); await Promise.resolve(); await Promise.resolve(); });
    expect(port.getPluginInfo).toHaveBeenCalledTimes(2);
  });

  it("originMismatch: отсутствие конфигурации static-origin → отказ без iframe", async () => {
    // Явно пустой env, чтобы тест не зависел от VITE_PLUGIN_STATIC_ORIGIN,
    // который vite.config.ts подставляет через define.
    await mountPage("/plugins/mod_equipment/co_123", {
      config: { env: {} },
      infoPort: infoPort(connectedInfo({ static_origin: "" })),
    });
    expect(host?.querySelector(".plugin-page")?.getAttribute("data-plugin-blocked")).toBe("true");
    expect(text()).toContain(pluginStrings.ru.loadError);
    expect(frame()).toBeNull();
  });
});

describe("resolvePluginPagePhase: fail-closed без моков хуков", () => {
  const ok = { status: "loading" as const, errorCode: "loadError" as const, failure: undefined };

  it("корректный src уже достаточен для iframe: status=loading до handshake НЕ блокирует", () => {
    // usePluginFrame выдаёт src, но остаётся в status="loading" до plugin.ready.
    expect(resolvePluginPagePhase(ok)).toEqual({ phase: "frame", messageKey: "" });
    expect(resolvePluginPagePhase({ ...ok, status: "ready" })).toEqual({ phase: "frame", messageKey: "" });
  });

  it("отсутствие src → loading (ожидание), а не ошибка и не iframe", () => {
    expect(resolvePluginPagePhase({ ...ok, failure: "missing" })).toEqual({
      phase: "loading", messageKey: "plugins.loading",
    });
  });

  it("src с ?token= → error даже в фазе loading", () => {
    expect(resolvePluginPagePhase({ ...ok, failure: "token" }).phase).toBe("error");
    expect(resolvePluginPagePhase({ ...ok, failure: "token" }).messageKey).toBe("plugins.loadError");
  });

  it("чужой origin → plugins.originMismatch и error", () => {
    expect(resolvePluginPagePhase({ ...ok, failure: "origin" })).toEqual({
      phase: "error", messageKey: "plugins.originMismatch",
    });
  });

  it("unsafe src → error", () => {
    expect(resolvePluginPagePhase({ ...ok, failure: "unsafe" }).phase).toBe("error");
  });

  it("notGranted / notConnected → свои тексты из юнита", () => {
    expect(resolvePluginPagePhase({ status: "notGranted", errorCode: "notGranted", failure: "missing" }).messageKey)
      .toBe("plugins.notGranted");
    expect(resolvePluginPagePhase({ status: "notConnected", errorCode: "notConnected", failure: "missing" }).messageKey)
      .toBe("plugins.notConnected");
  });

  it("ошибка загрузки хука → plugins.loadError, точный ключ по коду", () => {
    expect(resolvePluginPagePhase({ status: "error", errorCode: "timeout", failure: "missing" }).messageKey)
      .toBe("plugins.timeout");
    expect(resolvePluginPagePhase({ status: "error", errorCode: "loadError", failure: "missing" }).messageKey)
      .toBe("plugins.loadError");
  });

  it("iframe разрешён ТОЛЬКО когда failure отсутствует и хук не отказал", () => {
    expect(resolvePluginPagePhase({ status: "loading", errorCode: "loadError", failure: undefined }).phase).toBe("frame");
    expect(resolvePluginPagePhase({ status: "ready", errorCode: "loadError", failure: undefined }).phase).toBe("frame");
    for (const failure of ["missing", "token", "unsafe", "origin"] as const) {
      expect(resolvePluginPagePhase({ status: "ready", errorCode: "loadError", failure }).phase).toBe("error");
    }
    for (const status of ["error", "notGranted", "notConnected"] as const) {
      expect(resolvePluginPagePhase({ status, errorCode: "loadError", failure: undefined }).phase).toBe("error");
    }
  });
});

describe("PluginPage: локализация en (ключи согласованы)", () => {
  it("при locale=en тексты состояний берутся из en-ветки юнита", async () => {
    window.localStorage.setItem("agentscale.locale", "en");
    try {
      await mountPage("/plugins/mod_equipment/co_123", {
        infoPort: infoPort(connectedInfo({ granted: false })),
      });
      expect(text()).toContain(pluginStrings.en.notGranted);
      expect(text()).not.toContain(pluginStrings.ru.notGranted);
      expect(host?.querySelector<HTMLButtonElement>(".plugin-page__error button")?.textContent).toBe(pluginStrings.en.retry);
    } finally {
      window.localStorage.removeItem("agentscale.locale");
    }
  });
});
