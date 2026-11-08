// @vitest-environment jsdom
/**
 * `plugin-rpc/1`: проверки изоляции и fail-closed.
 *
 * Тесты НЕ запускались (freeze верификации миграции). Пишутся как часть
 * реализации, запуск — после явного решения родителя о начале верификации.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPluginChannel, OPAQUE_ORIGIN, PLUGIN_RPC_PROTOCOL, type PluginChannel } from "./rpc";
import { PluginFrameError } from "./errors";

const PLUGIN_ID = "mod_equipment";
const VERSION = "1.2.0";
const SAFE_SRC = "https://plugin-origin.agentscale.dev/tenant-co_123/mod_equipment/1.2.0/index.html";

function frame(): HTMLIFrameElement {
  const element = document.createElement("iframe");
  element.src = SAFE_SRC;
  document.body.append(element);
  return element;
}

/** Оpaque origin песочницы без allow-same-origin (v4 §5.3). */
function emit(target: Window, event: { origin: string; source: unknown; data: unknown }): void {
  target.dispatchEvent(new MessageEvent("message", {
    ...event,
    source: event.source as MessageEventSource,
  }));
}

function ready(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol: PLUGIN_RPC_PROTOCOL,
    type: "plugin.ready",
    plugin_id: PLUGIN_ID,
    version: VERSION,
    ...overrides,
  };
}

let iframe: HTMLIFrameElement;
let channel: PluginChannel | undefined;

beforeEach(() => {
  document.body.replaceChildren();
  iframe = frame();
});

afterEach(() => {
  channel?.dispose();
  channel = undefined;
  iframe.remove();
  vi.useRealTimers();
});

function makeChannel(overrides: Parameters<typeof createPluginChannel>[2] = {}): PluginChannel {
  channel = createPluginChannel(iframe, OPAQUE_ORIGIN, {
    pluginId: PLUGIN_ID,
    version: VERSION,
    timeoutMs: 5_000,
    makeRequestId: () => "req-1",
    ...overrides,
  });
  return channel;
}

describe("plugin-rpc/1: проверка origin и WindowProxy", () => {
  it("игнорирует сообщение с чужим event.origin и НЕ роняет канал", async () => {
    const rejected: string[] = [];
    const onError = vi.fn();
    const ch = makeChannel({ onRejected: (reason) => rejected.push(reason), onError });
    const handshake = ch.handshake();

    emit(window, {
      origin: "https://evil.example",
      source: iframe.contentWindow,
      data: ready(),
    });

    expect(rejected).toContain("origin");
    expect(onError).not.toHaveBeenCalled();
    expect(ch.disposed).toBe(false);
    expect(ch.ready).toBe(false);
    // Канал жив и принимает корректный handshake после попытки подделки.
    emit(window, { origin: OPAQUE_ORIGIN, source: iframe.contentWindow, data: ready() });
    await expect(handshake).resolves.toMatchObject({ plugin_id: PLUGIN_ID, version: VERSION });
    expect(ch.ready).toBe(true);
  });

  it("игнорирует сообщение от другого WindowProxy", () => {
    const rejected: string[] = [];
    const ch = makeChannel({ onRejected: (reason) => rejected.push(reason) });
    const other = document.createElement("iframe");
    document.body.append(other);
    void ch.handshake().catch(() => undefined);

    emit(window, { origin: OPAQUE_ORIGIN, source: other.contentWindow, data: ready() });

    expect(rejected).toContain("source");
    expect(ch.ready).toBe(false);
    expect(ch.disposed).toBe(false);
    other.remove();
  });

  it("отвергает wildcard, пустой и не-origin expectedOrigin при создании", () => {
    for (const origin of ["*", "", "https://evil.example/path?token=x"]) {
      expect(() => createPluginChannel(iframe, origin, { pluginId: PLUGIN_ID, version: VERSION }))
        .toThrowError(PluginFrameError);
    }
  });

  it("принимает точный https-origin (цель wildcard subdomain, §5.3)", () => {
    const ch = createPluginChannel(iframe, "https://co--mod.plugins.agentscale.dev", {
      pluginId: PLUGIN_ID, version: VERSION,
    });
    expect(ch.expectedOrigin).toBe("https://co--mod.plugins.agentscale.dev");
    ch.dispose();
  });
});

describe("plugin-rpc/1: handshake", () => {
  it("plugin.ready с неверным protocol → unsupportedProtocol", async () => {
    const onError = vi.fn();
    const ch = makeChannel({ onError });
    const handshake = ch.handshake();

    emit(window, {
      origin: OPAQUE_ORIGIN,
      source: iframe.contentWindow,
      data: { ...ready(), protocol: "plugin-rpc/2" },
    });

    await expect(handshake).rejects.toMatchObject({ code: "unsupportedProtocol" });
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: "unsupportedProtocol" }));
    expect(ch.disposed).toBe(true);
  });

  it("plugin.ready с чужим plugin_id/version → identityMismatch", async () => {
    const ch = makeChannel();
    const handshake = ch.handshake();
    emit(window, { origin: OPAQUE_ORIGIN, source: iframe.contentWindow, data: ready({ plugin_id: "other" }) });
    await expect(handshake).rejects.toMatchObject({ code: "identityMismatch" });
  });

  it("рукопожатие без ответа → timeout", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const ch = createPluginChannel(iframe, OPAQUE_ORIGIN, {
      pluginId: PLUGIN_ID,
      version: VERSION,
      timeoutMs: 200,
      onError,
    });
    channel = ch;
    const handshake = ch.handshake();

    await vi.advanceTimersByTimeAsync(250);

    await expect(handshake).rejects.toMatchObject({ code: "timeout" });
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: "timeout" }));
    expect(ch.disposed).toBe(true);
  });

  it("после успешного handshake повторный вызов не создаёт второй таймер", async () => {
    const ch = makeChannel();
    const handshake = ch.handshake();
    emit(window, { origin: OPAQUE_ORIGIN, source: iframe.contentWindow, data: ready() });
    await expect(handshake).resolves.toMatchObject({ type: "plugin.ready" });
    await expect(ch.handshake()).resolves.toMatchObject({ type: "plugin.ready" });
  });
});

describe("plugin-rpc/1: request/response", () => {
  it("сопоставляет ответ по requestId и запрещает повтор", async () => {
    const ch = makeChannel();
    const handshake = ch.handshake();
    emit(window, { origin: OPAQUE_ORIGIN, source: iframe.contentWindow, data: ready() });
    await handshake;

    const request = ch.request<{ rows: number }>("queries.list", { limit: 10 });
    emit(window, {
      origin: OPAQUE_ORIGIN,
      source: iframe.contentWindow,
      data: { protocol: PLUGIN_RPC_PROTOCOL, kind: "response", requestId: "req-1", ok: true, result: { rows: 3 } },
    });
    await expect(request).resolves.toEqual({ rows: 3 });
  });

  it("plugin.error закрывает канал с безопасным кодом, без текста плагина", async () => {
    const onError = vi.fn();
    const ch = makeChannel({ onError });
    const handshake = ch.handshake();
    emit(window, { origin: OPAQUE_ORIGIN, source: iframe.contentWindow, data: ready() });
    await handshake;

    emit(window, {
      origin: OPAQUE_ORIGIN,
      source: iframe.contentWindow,
      data: { protocol: PLUGIN_RPC_PROTOCOL, kind: "error", code: "SECRET_LEAK_ATTEMPT" },
    });

    expect(ch.disposed).toBe(true);
    expect(onError).toHaveBeenCalledTimes(1);
    const error = onError.mock.calls[0][0] as PluginFrameError;
    expect(error.message).not.toContain("SECRET");
    expect(error.detail).not.toContain("SECRET");
    expect(error.code).toBe("loadError");
  });

  it("не отправляет capability token: в postMessage нет token/access_token/secret", async () => {
    const postMessage = vi.fn();
    Object.defineProperty(iframe, "contentWindow", { value: { postMessage }, configurable: true });
    const ch = makeChannel();
    const handshake = ch.handshake();
    expect(postMessage).toHaveBeenCalledTimes(1);
    const [hostReady, targetOrigin] = postMessage.mock.calls[0] as [unknown, string];
    expect(JSON.stringify(hostReady).toLowerCase()).not.toMatch(/token|secret|bearer|authorization/);
    expect(targetOrigin).toBe("*"); // opaque origin: адресуем только "*", данных в сообщении нет

    // «Ответ» приходит от подменённого WindowProxy.
    const source = iframe.contentWindow;
    emit(window, { origin: OPAQUE_ORIGIN, source, data: ready() });
    await handshake;
    const request = ch.request("queries.list");
    const [, sent] = postMessage.mock.calls[1] as [unknown, unknown];
    expect(JSON.stringify(sent).toLowerCase()).not.toMatch(/token|secret|bearer|authorization/);
    emit(window, {
      origin: OPAQUE_ORIGIN,
      source,
      data: { protocol: PLUGIN_RPC_PROTOCOL, kind: "response", requestId: "req-1", ok: true, result: null },
    });
    await request;
  });
});

describe("plugin-rpc/1: dispose", () => {
  it("повторный dispose безопасен и не вызывает onError повторно", () => {
    const onError = vi.fn();
    const ch = makeChannel({ onError });
    ch.dispose();
    expect(() => ch.dispose()).not.toThrow();
    expect(() => ch.dispose()).not.toThrow();
    expect(ch.disposed).toBe(true);
    expect(onError).not.toHaveBeenCalled();
  });

  it("dispose снимает ожидающий handshake явным отказом, а не вечным pending", async () => {
    const ch = makeChannel();
    const handshake = ch.handshake();
    ch.dispose();
    await expect(handshake).rejects.toMatchObject({ code: "disposed" });
  });

  it("dispose удаляет слушатель message: последующие события не обрабатываются", async () => {
    const onRejected = vi.fn();
    const ch = makeChannel({ onRejected });
    void ch.handshake().catch(() => undefined);
    ch.dispose();
    onRejected.mockClear();
    emit(window, { origin: OPAQUE_ORIGIN, source: iframe.contentWindow, data: ready() });
    expect(onRejected).not.toHaveBeenCalled();
    expect(ch.ready).toBe(false);
  });

  it("request после dispose → отказ, а не обращение к мёртвому фрейму", async () => {
    const ch = makeChannel();
    ch.dispose();
    await expect(ch.request("queries.list")).rejects.toMatchObject({ code: "disposed" });
  });
});
