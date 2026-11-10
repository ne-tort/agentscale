// @vitest-environment jsdom
/** usePluginFrame registry identity/static-origin/lifecycle. НЕ запускались. */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildPluginSrc } from "./bootstrap";
import {
  resolveStaticOrigin,
  usePluginFrame,
  type ConnectedPluginInfo,
  type PluginInfo,
  type PluginInfoPort,
  type UsePluginFrameResult,
  type UsePluginFrameOptions,
} from "./usePluginFrame";
import { PluginFrameError } from "./errors";

const STATIC_ORIGIN = "https://plugin-origin.agentscale.dev";
const SHELL_ORIGIN = "https://agentscale.dev";
const VERSION = "1.2.0";
const DIGEST = `sha256:${"a".repeat(64)}`;
const CAPABILITIES = ["hello.greetings@1"] as const;

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

function infoPort(info: PluginInfo | Error): PluginInfoPort {
  return {
    getPluginInfo: vi.fn(async () => {
      if (info instanceof Error) throw info;
      return info;
    }),
  };
}

function Probe(props: UsePluginFrameOptions): ReactElement {
  const state = usePluginFrame(props);
  return <div data-testid="state" data-status={state.status} data-src={state.src}
    data-session={String(state.requiresSession)} data-origin={state.expectedOrigin} />;
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root?.render(element); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
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

beforeEach(() => { document.body.replaceChildren(); });

describe("resolveStaticOrigin / buildPluginSrc", () => {
  it("берёт точный https-origin из staticOrigin / VITE_PLUGIN_STATIC_ORIGIN", () => {
    expect(resolveStaticOrigin({ staticOrigin: STATIC_ORIGIN })).toBe(STATIC_ORIGIN);
    expect(resolveStaticOrigin({ env: { VITE_PLUGIN_STATIC_ORIGIN: STATIC_ORIGIN } })).toBe(STATIC_ORIGIN);
  });

  it("fail-closed без конфигурации и при wildcard/http/path/userinfo/shell-origin", () => {
    expect(() => resolveStaticOrigin({ env: {} })).toThrowError(PluginFrameError);
    for (const origin of [
      "https://*.agentscale.dev", "http://plugin-origin.agentscale.dev",
      `${STATIC_ORIGIN}/path`, `${STATIC_ORIGIN}/?token=x`, "https://u:p@plugin-origin.agentscale.dev",
    ]) {
      expect(() => resolveStaticOrigin({ staticOrigin: origin })).toThrowError(PluginFrameError);
    }
    expect(() => resolveStaticOrigin({ staticOrigin: SHELL_ORIGIN, shellOrigin: SHELL_ORIGIN })).toThrowError(PluginFrameError);
  });

  it("сверяется с allow-list", () => {
    expect(() => resolveStaticOrigin({
      staticOrigin: STATIC_ORIGIN,
      allowedStaticOrigins: ["https://other.example"],
    })).toThrowError(PluginFrameError);
  });

  it("buildPluginSrc использует signed custom entry/base_path без tenant template", () => {
    const src = buildPluginSrc(STATIC_ORIGIN, { entry: "assets/shell.html", basePath: "/custom/frontend" });
    expect(src).toBe(`${STATIC_ORIGIN}/custom/frontend/assets/shell.html`);
    expect(src).not.toContain("?");
    expect(src).not.toContain("#");
  });

  it("buildPluginSrc запрещает path traversal", () => {
    expect(() => buildPluginSrc(STATIC_ORIGIN, { entry: "../evil", basePath: "/custom" }))
      .toThrowError(PluginFrameError);
    expect(() => buildPluginSrc(STATIC_ORIGIN, { entry: "index.html", basePath: "/custom/../evil" }))
      .toThrowError(PluginFrameError);
  });
});

describe("usePluginFrame: PluginInfo — источник identity", () => {
  it("без порта fail-closed, версия не выдумывается", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" config={{ staticOrigin: STATIC_ORIGIN }} />);
    expect(state().status).toBe("error");
    expect(state().src).toBe("");
    expect(state().session).toBe("true");
  });

  it("not connected → notConnected", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort({ connected: false, granted: false })} />);
    expect(state().status).toBe("notConnected");
    expect(state().src).toBe("");
  });

  it("connected, но grant отсутствует → notGranted", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort(connectedInfo({ granted: false }))} />);
    expect(state().status).toBe("notGranted");
    expect(state().src).toBe("");
  });

  it("не требует version в query и строит src из manifest_version/artifact_digest", async () => {
    const port = infoPort(connectedInfo());
    await mount(<Probe pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={port} />);
    expect(state().status).toBe("loading");
    expect(state().src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
    expect(state().origin).toBe("null");
    expect(port.getPluginInfo).toHaveBeenCalledWith({
      pluginId: "mod_equipment", companyId: "co_123", publicPage: false, routePath: "/",
    });
    expect(port.getPluginInfo).not.toHaveBeenCalledWith(expect.objectContaining({ version: expect.anything() }));
  });

  it("nested routePath передаётся в bootstrap и связывается с ответом", async () => {
    const port = infoPort(connectedInfo({
      frontend_entry: "shell.html",
      frontend_base_path: "/signed/custom-base",
      route_path: "/section/42",
    }));
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" routePath="/section/42"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={port} />);
    expect(state().src).toBe(`${STATIC_ORIGIN}/signed/custom-base/shell.html`);
    expect(port.getPluginInfo).toHaveBeenCalledWith({
      pluginId: "mod_equipment", companyId: "co_123", publicPage: false, routePath: "/section/42",
    });
  });

  it("невалидный digest/scope fail-closed даже при granted=true", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort(connectedInfo({ artifact_digest: "bad" }))} />);
    expect(state().status).toBe("error");
    expect(state().src).toBe("");
  });

  it("ошибка порта не раскрывает чужой текст и не разрешает src", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort(new Error("secret in error text"))} />);
    expect(state().status).toBe("error");
    expect(state().src).toBe("");
  });

  it("publicPage не требует grant, но вызывает registry info", async () => {
    const port = infoPort(connectedInfo({ granted: false }));
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" publicPage
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={port} />);
    expect(state().session).toBe("false");
    expect(state().src).not.toBe("");
    expect(port.getPluginInfo).toHaveBeenCalledTimes(1);
  });

  it("publicPage без registry порта fail-closed: digest нельзя выдумать", async () => {
    await mount(<Probe pluginId="mod_equipment" companyId="co_123" publicPage
      config={{ staticOrigin: STATIC_ORIGIN }} />);
    expect(state().session).toBe("false");
    expect(state().status).toBe("error");
    expect(state().src).toBe("");
  });
});

describe("usePluginFrame: bootstrap shell handoff", () => {
  it("прокидывает server DTO и не доверяет client shell config", async () => {
    const port = infoPort(connectedInfo());
    await mount(<Probe pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN, shellOrigin: "https://evil.example" }}
      infoPort={port} />);
    expect(state().status).toBe("loading");
    expect(state().src).toBe(`${STATIC_ORIGIN}/tenant-co_123/mod_equipment/1.2.0/index.html`);
  });

  it.each([undefined, "", "https://agentscale.dev/path", STATIC_ORIGIN])(
    "missing/non-exact bootstrap shell origin %s = fail-closed",
    async (plugin_shell_origin) => {
      await mount(<Probe pluginId="mod_equipment" companyId="co_123"
        config={{ staticOrigin: STATIC_ORIGIN }}
        infoPort={infoPort(connectedInfo({ plugin_shell_origin }))} />);
      expect(state().status).toBe("error");
      expect(state().src).toBe("");
    },
  );
});

describe("usePluginFrame: lifecycle канала", () => {
  it("dispose идемпотентен и cleanup не бросает", async () => {
    let result: UsePluginFrameResult | undefined;
    function Capture(props: UsePluginFrameOptions) {
      result = usePluginFrame(props);
      return <span data-status={result.status} />;
    }
    await mount(<Capture pluginId="mod_equipment" companyId="co_123" publicPage
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={infoPort(connectedInfo({ granted: false }))} />);
    expect(() => { result?.dispose(); result?.dispose(); }).not.toThrow();
    await act(async () => { root?.unmount(); });
    root = undefined;
  });

  it("channelFactory получает registry identity; retry перечитывает info", async () => {
    let result: UsePluginFrameResult | undefined;
    function Capture(props: UsePluginFrameOptions) {
      result = usePluginFrame(props);
      return <span data-status={result.status} data-src={result.src} />;
    }
    const port = infoPort(connectedInfo());
    await mount(<Capture pluginId="mod_equipment" companyId="co_123"
      config={{ staticOrigin: STATIC_ORIGIN }} infoPort={port} />);
    const frame = document.createElement("iframe");
    const fakeWindow = { postMessage: vi.fn() };
    Object.defineProperty(frame, "contentWindow", { value: fakeWindow, configurable: true });

    const channel = result!.channelFactory(frame, result!.expectedOrigin, {
      pluginId: "mod_equipment",
      version: VERSION,
      artifactDigest: DIGEST,
      capabilities: CAPABILITIES,
      scope: { company_id: "co_123" },
      timeoutMs: 1000,
    });
    expect(channel.expectedOrigin).toBe("null");
    expect(result!.shellOrigin).toBe(SHELL_ORIGIN);
    expect(channel.disposed).toBe(false);
    channel.dispose();
    await act(async () => { result!.retry(); });
    expect(port.getPluginInfo).toHaveBeenCalledTimes(2);
  });
});
