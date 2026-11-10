// @vitest-environment jsdom
/**
 * Поведение PluginFrame в живом React root: эффекты, ошибки src, retry,
 * происхождение `event.origin` и недоступность iframe при отказе.
 *
 * Тесты НЕ запускались (freeze верификации миграции).
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { PluginFrame, PLUGIN_SANDBOX, type PluginFrameProps } from "./PluginFrame";
import { OPAQUE_ORIGIN, PLUGIN_RPC_PROTOCOL, type PluginChannel } from "./rpc";
import { PluginFrameError } from "./errors";

const SAFE_SRC = "https://plugin-origin.agentscale.dev/tenant-co_123/mod_equipment/1.2.0/index.html";
const DIGEST = `sha256:${"a".repeat(64)}`;
const SHELL_ORIGIN = "https://agentscale.dev";
const INSTANCE_ID = "instance-1234567890";
const NONCE = "nonce-1234567890";

function base(overrides: Partial<PluginFrameProps> = {}): PluginFrameProps {
  return {
    pluginId: "mod_equipment",
    companyId: "co_123",
    version: "1.2.0",
    artifactDigest: DIGEST,
    src: SAFE_SRC,
    shellOrigin: SHELL_ORIGIN,
    ...overrides,
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root?.render(<SettingsProvider>{element}</SettingsProvider>); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

function phase(): string | null {
  return host?.querySelector<HTMLElement>(".plugin-frame")?.dataset.pluginPhase ?? null;
}
function frame(): HTMLIFrameElement | null {
  return host?.querySelector<HTMLIFrameElement>("iframe") ?? null;
}

/** Канал-заглушка: handshake успешен сразу, dispose идемпотентен. */
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
      version: "1.2.0",
      artifact_digest: DIGEST,
      instance_id: INSTANCE_ID,
      nonce: NONCE,
    }),
    request: <T,>() => Promise.resolve(undefined as T),
    dispose: () => { disposed = true; },
  };
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

describe("PluginFrame: живой рендер", () => {
  it("sandbox и атрибуты изоляции заданы в DOM, src безопасен", async () => {
    await mount(<PluginFrame {...base()} channelFactory={(_frame, origin) => inertChannel(origin)} />);
    const iframe = frame()!;
    expect(iframe.getAttribute("sandbox")).toBe(PLUGIN_SANDBOX);
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(iframe.hasAttribute("credentialless")).toBe(true);
    expect(iframe.getAttribute("allow")).toBe("");
    expect(iframe.getAttribute("src")).toBe(SAFE_SRC);
    expect(iframe.src.toLowerCase()).not.toContain("token");
    expect(iframe.getAttribute("aria-label")).toBeTruthy();
    expect(iframe.hasAttribute("aria-busy")).toBe(true);
    expect(iframe.getAttribute("role")).not.toBe("alert");
  });

  it("устанавливает exact server-derived handoff до src, без window.location trust", async () => {
    let handoffAtNavigation = "";
    await mount(<PluginFrame {...base()} channelFactory={(element, origin) => {
      const setAttribute = element.setAttribute.bind(element);
      element.setAttribute = (name: string, value: string) => {
        if (name === "src") handoffAtNavigation = element.name;
        setAttribute(name, value);
      };
      return inertChannel(origin);
    }} />);
    const iframe = frame()!;
    const handoff = JSON.parse(iframe.name) as Record<string, unknown>;
    expect(handoffAtNavigation).toBe(iframe.name);
    expect(handoff).toEqual({
      protocol: "agentscale-plugin-shell/1",
      plugin_shell_origin: SHELL_ORIGIN,
      plugin_id: "mod_equipment",
      manifest_version: "1.2.0",
      artifact_digest: DIGEST,
    });
    expect(handoff.plugin_shell_origin).not.toBe(window.location.origin);
    expect(iframe.name).not.toMatch(/token|secret|authorization/i);
    expect(iframe.getAttribute("src")).toBe(SAFE_SRC);
  });

  it.each([
    "http://agentscale.dev",
    "https://*.agentscale.dev",
    "https://agentscale.dev/path",
    "https://agentscale.dev/?origin=https://evil.example",
    SAFE_SRC,
  ])("invalid/untrusted shellOrigin %s запрещает навигацию", async (shellOrigin) => {
    const onError = vi.fn();
    await mount(<PluginFrame {...base({ shellOrigin })} onError={onError} />);
    expect(phase()).toBe("error");
    expect(frame()?.hasAttribute("src")).toBe(false);
    expect(frame()?.hasAttribute("name")).toBe(false);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: "originMismatch" }));
  });

  it("unsafe src (?token=) → русская ошибка, iframe без src и скрыт", async () => {
    const onError = vi.fn();
    await mount(<PluginFrame {...base({ src: `${SAFE_SRC}?token=leak` })} onError={onError} />);
    expect(phase()).toBe("error");
    expect(host?.textContent).toContain("Плагин не загрузился");
    expect(frame()?.hasAttribute("src")).toBe(false);
    expect(frame()?.hidden).toBe(true);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: "unsafeSrc" }));
  });

  it("отказ по src нейтрализует документ, а не только скрывает кадр", async () => {
    // `hidden` не останавливает исполнение: уже загруженный плагин продолжал бы
    // жить, держать window.name-handoff и слать host-сообщения.
    await mount(<PluginFrame {...base({ src: `${SAFE_SRC}?token=leak` })} />);
    expect(frame()?.getAttribute("srcdoc")).toContain("Plugin blocked");
    expect(frame()?.dataset.pluginBlocked).toBe("true");
  });

  it("access_token в src → отказ и retry-кнопка", async () => {
    await mount(<PluginFrame {...base({ src: `${SAFE_SRC}?access_token=leak` })} />);
    expect(phase()).toBe("error");
    const retry = host?.querySelector<HTMLButtonElement>(".plugin-frame__error button");
    expect(retry?.textContent).toBe("Повторить");
    await act(async () => { retry?.click(); });
  });

  it("publicPage показывает плашку и не требует сессии", async () => {
    await mount(<PluginFrame {...base({ publicPage: true })} channelFactory={(_f, o) => inertChannel(o)} />);
    expect(host?.querySelector(".plugin-frame")?.getAttribute("data-plugin-public")).toBe("true");
    expect(host?.textContent).toContain("Публичная страница");
    expect(host?.textContent).not.toContain("Доступ к плагину не выдан");
    expect(host?.querySelector(".plugin-frame")?.getAttribute("data-plugin-public")).toBe("true");
  });

  it("таймаут handshake → plugins.timeout и русский текст", async () => {
    const channel: PluginChannel = {
      ...inertChannel(OPAQUE_ORIGIN),
      ready: false,
      handshake: () => Promise.reject(new PluginFrameError("timeout", "plugin_handshake_timeout")),
    };
    await mount(<PluginFrame {...base()} channelFactory={() => channel} />);
    expect(phase()).toBe("error");
    expect(host?.textContent).toContain("Плагин не ответил вовремя");
    expect(host?.querySelector(".plugin-frame__error")?.getAttribute("role")).toBe("alert");
    expect(frame()?.getAttribute("aria-hidden")).toBe("true");
    expect(frame()?.hidden).toBe(true);
  });

  it("unsupportedProtocol → русский текст про версию протокола", async () => {
    const channel: PluginChannel = {
      ...inertChannel(OPAQUE_ORIGIN),
      ready: false,
      handshake: () => Promise.reject(new PluginFrameError("unsupportedProtocol", "plugin_rpc_protocol_mismatch")),
    };
    await mount(<PluginFrame {...base()} channelFactory={() => channel} />);
    expect(host?.textContent).toContain("неподдерживаемую версию протокола");
  });

  it("cleanup на unmount вызывает dispose канала", async () => {
    const dispose = vi.fn();
    const channel = { ...inertChannel(OPAQUE_ORIGIN), dispose };
    const factory = (_f: HTMLIFrameElement, _o: string) => channel;
    await mount(<PluginFrame {...base()} channelFactory={factory} />);
    expect(dispose).not.toHaveBeenCalled();
    await act(async () => { root?.unmount(); });
    root = undefined;
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("plugin.ready от чужого event.origin не переводит фрейм в ready", async () => {
    // Канал проверяет origin сам; здесь проверяем, что компонент не доверяет
    // «готовности» без канала и остаётся в состоянии загрузки.
    let realChannel: PluginChannel | undefined;
    await mount(<PluginFrame {...base()} channelFactory={(element, origin) => {
      realChannel = {
        ...inertChannel(origin),
        ready: false,
        handshake: () => new Promise<never>(() => undefined), // ждём, не переходим в ready
      };
      void element;
      return realChannel;
    }} />);
    window.dispatchEvent(new MessageEvent("message", {
      origin: "https://evil.example",
      source: frame()?.contentWindow as MessageEventSource,
      data: {
        protocol: PLUGIN_RPC_PROTOCOL,
        type: "plugin.ready",
        plugin_id: "mod_equipment",
        version: "1.2.0",
        artifact_digest: DIGEST,
        instance_id: INSTANCE_ID,
        nonce: NONCE,
      },
    }));
    expect(phase()).not.toBe("ready");
    expect(realChannel?.ready).toBe(false);
  });
});
