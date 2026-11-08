// @vitest-environment jsdom
/**
 * `usePluginFrame`: контракт порта, static-origin, lifecycle и cleanup.
 *
 * Тесты НЕ запускались (freeze верификации миграции).
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildPluginSrc,
  resolveStaticOrigin,
  usePluginFrame,
  type PluginInfo,
  type PluginInfoPort,
  type UsePluginFrameResult,
  type UsePluginFrameOptions,
} from "./usePluginFrame";
import { PluginFrameError } from "./errors";

const STATIC_ORIGIN = "https://plugin-origin.agentscale.dev";
const SHELL_ORIGIN = "https://agentscale.dev";

let root: Root | undefined;
let host: HTMLDivElement | undefined;

function infoPort(info: PluginInfo | Error): PluginInfoPort {
  return {
    getPluginInfo: vi.fn(async () => {
      if (info instanceof Error) throw info;
      return info;
    }),
  };
}

/** Маленький пробник: хук рендерится в реальный React root, результат читается в DOM. */
function Probe(props: UsePluginFrameOptions): ReactElement {
  const state = usePluginFrame(props);
  return <div data-testid="state" data-status={state.status} data-src={state.src}
    data-session={String(state.requiresSession)} data-origin={state.expectedOrigin} />;
}

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root?.render(element); });
  await act(async () => { await Promise.resolve(); });
}

function state(): { status: string | null; src: string | null; session: string | null; origin: string | null } {
  const node = host?.querySelector<HTMLElement>('[data-testid="state"]');
  return {
    status: node?.dataset.status ?? null,
    src: node?.dataset.src ?? null,
    session: node?.dataset.session ?? null,
    origin: node?.dataset.origin ?? null,
  };
}

afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  root = undefined;
  host?.remove();
  host = undefined;
  document.body.replaceChildren();
  vi.clearAllMocks();
});

beforeEach(() => {
  document.body.replaceChildren();
});

describe("resolveStaticOrigin: отдельный origin из конфигурации", () => {
  it("берёт точный https-origin из staticOrigin / VITE_PLUGIN_STATIC_ORIGIN", () => {
    expect(resolveStaticOrigin({ staticOrigin: STATIC_ORIGIN })).toBe(STATIC_ORIGIN);
    expect(resolveStaticOrigin({ env: { VITE_PLUGIN_STATIC_ORIGIN: STATIC_ORIGIN } })).toBe(STATIC_ORIGIN);
  });

  it("fail-closed: без конфигурации origin не выбирается", () => {
    expect(() => resolveStaticOrigin({ env: {} })).toThrowError(PluginFrameError);
  });

  it("отвергает wildcard, http, path/query/fragment, userinfo и origin оболочки", () => {
    const code = (origin: string, shellOrigin?: string): string => {
      try { resolveStaticOrigin({ staticOrigin: origin, ...(shellOrigin ? { shellOrigin } : {}) }); return "allowed"; }
      catch (error) { return error instanceof PluginFrameError ? error.detail : "unknown"; }
    };
    expect(code("https://*.agentscale.dev")).toContain("plugin_wildcard_origin_denied");
    expect(code("http://plugin-origin.agentscale.dev")).toContain("insecure");
    expect(code(`${STATIC_ORIGIN}/path`)).toContain("not_exact");
    expect(code(`${STATIC_ORIGIN}/?token=x`)).toContain("not_exact");
    expect(code("https://u:p@plugin-origin.agentscale.dev")).toContain("not_exact");
    expect(code(SHELL_ORIGIN, SHELL_ORIGIN)).toContain("is_shell");
  });

  it("сверяется с allow-list", () => {
    let detail = "";
    try { resolveStaticOrigin({ staticOrigin: STATIC_ORIGIN, allowedStaticOrigins: ["https://other.example"] }); }
    catch (error) { detail = error instanceof PluginFrameError ? error.detail : "unknown"; }
    expect(detail).toContain("not_allowlisted");
  });

  it("buildPluginSrc даёт версионированный путь без query/fragment/токена", () => {
    const src = buildPluginSrc(STATIC_ORIGIN, { companyId: "co_123", pluginId: "mod_equipment", version: "1.2.0" });
    expect(src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
    expect(src).not.toContain("?");
    expect(src).not.toContain("#");
    expect(src.toLowerCase()).not.toContain("token");
  });

  it("buildPluginSrc не даёт path traversal через идентификаторы", () => {
    expect(() => buildPluginSrc(STATIC_ORIGIN, { companyId: "../evil", pluginId: "mod", version: "1" }))
      .toThrowError(PluginFrameError);
  });
});

describe("usePluginFrame: grant/состояние через PluginInfoPort", () => {
  it("без порта и без publicPage — fail-closed ошибка, src пуст", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0"
      config={{ staticOrigin: STATIC_ORIGIN }} />);
    expect(state().status).toBe("error");
    expect(state().src).toBe("");
    expect(state().session).toBe("true");
  });

  it("not connected → notConnected, src не рендерится", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort({ connected: false, granted: false })} />);
    expect(state().status).toBe("notConnected");
    expect(state().src).toBe("");
  });

  it("connected, но grant отсутствует → notGranted", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort({ connected: true, granted: false })} />);
    expect(state().status).toBe("notGranted");
    expect(state().src).toBe("");
  });

  it("connected + granted → безопасный src и opaque expectedOrigin", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort({ connected: true, granted: true })} />);
    expect(state().status).toBe("loading");
    expect(state().src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
    expect(state().origin).toBe("null");
  });

  it("ошибка порта → error, а не молчаливое разрешение", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort(new Error("secret in error text"))} />);
    expect(state().status).toBe("error");
    expect(state().src).toBe("");
  });

  it("publicPage не требует сессии и НЕ вызывает PluginInfoPort", async () => {
    const port = infoPort({ connected: false, granted: false });
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0" publicPage
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={port} />);
    expect(state().session).toBe("false");
    expect(state().src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
    expect(port.getPluginInfo).not.toHaveBeenCalled();
  });

  it("publicPage без порта работает (сессия не нужна)", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" version="1.2.0" publicPage
      config={{ staticOrigin: STATIC_ORIGIN }} />);
    expect(state().session).toBe("false");
    expect(state().src).not.toBe("");
  });
});

describe("usePluginFrame: lifecycle канала и cleanup", () => {
  it("dispose() идемпотентен; cleanup на unmount не бросает", async () => {
    let result: UsePluginFrameResult | undefined;
    function Capture(props: UsePluginFrameOptions) {
      result = usePluginFrame(props);
      return <span data-status={result.status} />;
    }
    await mount(<Capture pluginId="mod_equipment" companyId="co_123" version="1.2.0" publicPage
      config={{ staticOrigin: STATIC_ORIGIN }} />);
    expect(() => { result?.dispose(); result?.dispose(); }).not.toThrow();
    await act(async () => { root?.unmount(); });
    root = undefined;
    expect(host?.textContent ?? "").toBe("");
  });

  it("channelFactory создаёт канал с ожидаемым origin и retry перезапускает загрузку", async () => {
    let result: UsePluginFrameResult | undefined;
    function Capture(props: UsePluginFrameOptions) {
      result = usePluginFrame(props);
      return <span data-status={result.status} data-src={result.src} />;
    }
    const port = infoPort({ connected: true, granted: true });
    await mount(<Capture pluginId="mod_equipment" companyId="co_123" version="1.2.0"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={port} />);
    const frame = document.createElement("iframe");
    document.body.append(frame);
    const channel = result!.channelFactory(frame, result!.expectedOrigin, {
      pluginId: "mod_equipment", version: "1.2.0", timeoutMs: 1000,
    });
    expect(channel.expectedOrigin).toBe("null");
    expect(channel.disposed).toBe(false);
    channel.dispose();
    await act(async () => { result!.retry(); });
    expect(port.getPluginInfo).toHaveBeenCalledTimes(2);
    frame.remove();
  });
});
