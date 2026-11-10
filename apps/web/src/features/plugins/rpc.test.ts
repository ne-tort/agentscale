// @vitest-environment jsdom
/** Регрессии единого plugin-rpc/1. Написаны, но НЕ запускались (freeze). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createPluginChannel,
  OPAQUE_ORIGIN,
  PLUGIN_RPC_PROTOCOL,
  type GrantReadyMessage,
  type HostReadyMessage,
  type PluginChannel,
  type PluginRpcRequest,
} from "./rpc";
import { PluginFrameError } from "./errors";
import { PluginOperationError } from "./operationBroker";

const PLUGIN_ID = "hello_plugin";
const VERSION = "0.1.0";
const DIGEST = `sha256:${"a".repeat(64)}`;
const OTHER_DIGEST = `sha256:${"b".repeat(64)}`;
const SCOPE = { company_id: "co_123" } as const;
const CAPABILITIES = ["hello.greetings@1"] as const;
const NOW = Date.parse("2026-01-01T00:00:00.000Z");
const SAFE_SRC = "https://plugins.example/tenant-co_123/hello_plugin/0.1.0/index.html";

let iframe: HTMLIFrameElement;
let source: { postMessage: ReturnType<typeof vi.fn> };
let channel: PluginChannel | undefined;

beforeEach(() => {
  iframe = document.createElement("iframe");
  iframe.setAttribute("src", SAFE_SRC);
  source = { postMessage: vi.fn() };
  Object.defineProperty(iframe, "contentWindow", { value: source, configurable: true });
});

afterEach(() => {
  channel?.dispose();
  channel = undefined;
  iframe.remove();
  vi.useRealTimers();
});

function emit(data: unknown, origin = OPAQUE_ORIGIN, eventSource: unknown = source): void {
  window.dispatchEvent(new MessageEvent("message", {
    origin,
    source: eventSource as MessageEventSource,
    data,
  }));
}

function makeChannel(overrides: Parameters<typeof createPluginChannel>[2] = {}): PluginChannel {
  channel = createPluginChannel(iframe, OPAQUE_ORIGIN, {
    pluginId: PLUGIN_ID,
    version: VERSION,
    artifactDigest: DIGEST,
    capabilities: CAPABILITIES,
    scope: SCOPE,
    now: () => NOW,
    grantTtlMs: 60_000,
    timeoutMs: 5_000,
    requestTimeoutMs: 1_000,
    makeRequestId: () => "req-1",
    ...overrides,
  });
  return channel;
}

function begin(ch: PluginChannel): { handshake: Promise<unknown>; hostReady: HostReadyMessage } {
  const handshake = ch.handshake();
  expect(source.postMessage).not.toHaveBeenCalled();
  iframe.dispatchEvent(new Event("load"));
  expect(source.postMessage).toHaveBeenCalledTimes(1);
  const [hostReady, targetOrigin] = source.postMessage.mock.calls[0] as [HostReadyMessage, string];
  expect(targetOrigin).toBe("*"); // transport opaque origin, не доверие
  return { handshake, hostReady };
}

function pluginReady(hostReady: HostReadyMessage): Record<string, unknown> {
  return { ...hostReady, type: "plugin.ready" };
}

async function complete(ch: PluginChannel): Promise<{
  hostReady: HostReadyMessage;
  grant: GrantReadyMessage;
}> {
  const { handshake, hostReady } = begin(ch);
  emit(pluginReady(hostReady));
  await handshake;
  expect(source.postMessage).toHaveBeenCalledTimes(2);
  const grant = source.postMessage.mock.calls[1][0] as GrantReadyMessage;
  return { hostReady, grant };
}

function incomingRequest(hostReady: HostReadyMessage, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...hostReady,
    type: "plugin.rpc.request",
    request_id: "incoming-1",
    method: "capability.call",
    capability: { id: "hello.greetings", version: "1" },
    context: SCOPE,
    idempotency_key: "incoming-1",
    input: { method: "list", params: {} },
    ...overrides,
  };
}

describe("plugin-rpc/1 first-load handshake", () => {
  it("не отправляет host.ready до load; после echo отправляет bounded grant.ready", async () => {
    const onReady = vi.fn();
    const ch = makeChannel({ onReady });
    const { handshake, hostReady } = begin(ch);

    expect(hostReady).toMatchObject({
      protocol: PLUGIN_RPC_PROTOCOL,
      type: "host.ready",
      plugin_id: PLUGIN_ID,
      version: VERSION,
      artifact_digest: DIGEST,
    });
    expect(hostReady.instance_id).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(hostReady.nonce).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(hostReady).not.toHaveProperty("kind");
    expect(hostReady).not.toHaveProperty("digest");

    emit(pluginReady(hostReady));
    await expect(handshake).resolves.toMatchObject({
      type: "plugin.ready",
      artifact_digest: DIGEST,
      instance_id: hostReady.instance_id,
      nonce: hostReady.nonce,
    });
    const grant = source.postMessage.mock.calls[1][0] as GrantReadyMessage;
    expect(grant).toEqual({
      ...hostReady,
      type: "grant.ready",
      capabilities: CAPABILITIES,
      scope: SCOPE,
      expires_at: new Date(NOW + 60_000).toISOString(),
    });
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(ch.ready).toBe(true);
  });

  it("second load/navigation уничтожает канал и блокирует frame", async () => {
    const onError = vi.fn<(error: PluginFrameError) => void>();
    const ch = makeChannel({ onError });
    await complete(ch);

    iframe.dispatchEvent(new Event("load"));

    expect(ch.disposed).toBe(true);
    expect(ch.ready).toBe(false);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ detail: "plugin_frame_reloaded" }));
    expect(iframe.hasAttribute("src")).toBe(false);
    expect(iframe.getAttribute("srcdoc")).toContain("Plugin blocked");
    expect(iframe.hidden).toBe(true);
    expect(iframe.dataset.pluginBlocked).toBe("true");
  });

  it.each([
    ["nonce", "missing"],
    ["nonce", "wrong"],
    ["artifact_digest", "missing"],
    ["artifact_digest", "wrong"],
    ["instance_id", "missing"],
    ["instance_id", "wrong"],
  ] as const)("обязательное echo: %s/%s → identityMismatch + blocked frame", async (field, mode) => {
    const ch = makeChannel();
    const { handshake, hostReady } = begin(ch);
    const message = pluginReady(hostReady);
    if (mode === "missing") delete message[field];
    else message[field] = field === "artifact_digest" ? OTHER_DIGEST : "stale-token-123456";

    emit(message);

    await expect(handshake).rejects.toMatchObject({ code: "identityMismatch" });
    expect(ch.disposed).toBe(true);
    expect(iframe.dataset.pluginBlocked).toBe("true");
  });

  it("чужой plugin_id/version тоже закрывает канал", async () => {
    const ch = makeChannel();
    const { handshake, hostReady } = begin(ch);
    emit({ ...pluginReady(hostReady), plugin_id: "other_plugin", version: "9.9.9" });
    await expect(handshake).rejects.toMatchObject({ code: "identityMismatch" });
    expect(ch.disposed).toBe(true);
  });

  it("чужой origin/WindowProxy игнорируется, но не заменяет exact source", async () => {
    const rejected: string[] = [];
    const ch = makeChannel({ onRejected: (reason) => rejected.push(reason) });
    const { handshake, hostReady } = begin(ch);
    emit(pluginReady(hostReady), "https://evil.example");
    emit(pluginReady(hostReady), OPAQUE_ORIGIN, {});
    expect(rejected).toEqual(["origin", "source"]);
    expect(ch.disposed).toBe(false);
    emit(pluginReady(hostReady));
    await handshake;
    expect(ch.ready).toBe(true);
  });

  it("timeout до plugin.ready закрывает и блокирует frame", async () => {
    vi.useFakeTimers();
    const ch = makeChannel({ timeoutMs: 200 });
    const { handshake } = begin(ch);
    await vi.advanceTimersByTimeAsync(250);
    await expect(handshake).rejects.toMatchObject({ code: "timeout" });
    expect(ch.disposed).toBe(true);
    expect(iframe.dataset.pluginBlocked).toBe("true");
  });
});

describe("plugin-rpc/1 host-side incoming request router", () => {
  it("capability.call → typed onRequest → snake_case bounded response", async () => {
    const onRequest = vi.fn(async (_request: PluginRpcRequest) => ({ items: [{ message: "hello" }] }));
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);
    const request = incomingRequest(hostReady);

    emit(request);
    await vi.waitFor(() => expect(source.postMessage).toHaveBeenCalledTimes(3));

    expect(onRequest).toHaveBeenCalledWith(expect.objectContaining({
      type: "plugin.rpc.request",
      method: "capability.call",
      request_id: "incoming-1",
      capability: { id: "hello.greetings", version: "1" },
    }));
    expect(source.postMessage.mock.calls[2][0]).toEqual({
      ...hostReady,
      type: "plugin.rpc.response",
      request_id: "incoming-1",
      ok: true,
      result: { items: [{ message: "hello" }] },
    });
  });

  it("duplicate request_id отклоняется и typed handler вызывается один раз", async () => {
    const onRequest = vi.fn((_request: PluginRpcRequest) => ({ ok: true }));
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);
    const request = incomingRequest(hostReady);

    emit(request);
    emit(request);
    await vi.waitFor(() => expect(source.postMessage).toHaveBeenCalledTimes(4));

    expect(onRequest).toHaveBeenCalledTimes(1);
    const duplicate = source.postMessage.mock.calls[2][0] as Record<string, unknown>;
    expect(duplicate).toMatchObject({ ok: false, request_id: "incoming-1" });
    expect(duplicate.error).toMatchObject({ code: "DUPLICATE_REQUEST_ID", trace_id: "incoming-1" });
    expect(ch.disposed).toBe(false);
  });

  it("oversize incoming request получает PAYLOAD_TOO_LARGE без вызова handler", async () => {
    const onRequest = vi.fn();
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);
    emit(incomingRequest(hostReady, { input: { blob: "x".repeat(70 * 1024) } }));

    expect(source.postMessage).toHaveBeenCalledTimes(3);
    const response = source.postMessage.mock.calls[2][0] as Record<string, unknown>;
    expect(response).toMatchObject({ ok: false, request_id: "incoming-1" });
    expect(response.error).toMatchObject({ code: "PAYLOAD_TOO_LARGE" });
    expect(onRequest).not.toHaveBeenCalled();
    expect(ch.disposed).toBe(false);
  });

  it("oversize handler result заменяется bounded PAYLOAD_TOO_LARGE response", async () => {
    const onRequest = vi.fn(() => ({ blob: "x".repeat(64 * 1024) }));
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);
    emit(incomingRequest(hostReady));

    await vi.waitFor(() => expect(source.postMessage).toHaveBeenCalledTimes(3));
    const response = source.postMessage.mock.calls[2][0] as Record<string, unknown>;
    expect(response).toMatchObject({ ok: false });
    expect(response.error).toMatchObject({ code: "PAYLOAD_TOO_LARGE" });
    expect(JSON.stringify(response).length).toBeLessThan(64 * 1024);
  });

  it("unknown method, чужая capability и широкий context deny by allowlist/scope", async () => {
    const onRequest = vi.fn();
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);

    emit(incomingRequest(hostReady, { request_id: "unknown-1", idempotency_key: "unknown-1", method: "db.direct" }));
    emit(incomingRequest(hostReady, {
      request_id: "cap-2", idempotency_key: "cap-2", capability: { id: "hello.greetings", version: "2" },
    }));
    emit(incomingRequest(hostReady, {
      request_id: "scope-3", idempotency_key: "scope-3", context: { company_id: "other" },
    }));

    expect(source.postMessage).toHaveBeenCalledTimes(5);
    const codes = source.postMessage.mock.calls.slice(2).map((call) =>
      (call[0] as Record<string, unknown>).error as Record<string, unknown>);
    expect(codes.map((error) => error.code)).toEqual(["UNKNOWN_METHOD", "CAPABILITY_DENIED", "SCOPE_DENIED"]);
    expect(onRequest).not.toHaveBeenCalled();
  });

  it("manifest broker denial сохраняет typed CAPABILITY_DENIED", async () => {
    const onRequest = vi.fn(() => {
      throw new PluginOperationError("OPERATION_NOT_DECLARED", "CAPABILITY_DENIED");
    });
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);
    emit(incomingRequest(hostReady));
    await vi.waitFor(() => expect(source.postMessage).toHaveBeenCalledTimes(3));
    const response = source.postMessage.mock.calls[2][0] as Record<string, unknown>;
    expect(response.error).toMatchObject({ code: "CAPABILITY_DENIED" });
  });

  it("host.navigate принимает только typed internal path, не generic URL", async () => {
    const onRequest = vi.fn();
    const ch = makeChannel({ onRequest });
    const { hostReady } = await complete(ch);
    const navigation = incomingRequest(hostReady, {
      request_id: "nav-1",
      method: "host.navigate",
      input: { path: "https://evil.example/steal" },
    });
    delete navigation.capability;
    delete navigation.idempotency_key;
    emit(navigation);
    const response = source.postMessage.mock.calls[2][0] as Record<string, unknown>;
    expect(response.error).toMatchObject({ code: "INVALID_ENVELOPE" });
    expect(onRequest).not.toHaveBeenCalled();
  });
});

describe("plugin-rpc/1 reverse request/response и dispose", () => {
  it("host request использует тот же envelope и сопоставляет response по request_id", async () => {
    const ch = makeChannel();
    const { hostReady } = await complete(ch);
    const request = ch.request<{ applied: boolean }>("host.resize", { height: 240 });
    const outgoing = source.postMessage.mock.calls[2][0] as Record<string, unknown>;
    expect(outgoing).toEqual({
      ...hostReady,
      type: "plugin.rpc.request",
      request_id: "req-1",
      method: "host.resize",
      context: SCOPE,
      input: { height: 240 },
    });
    emit({
      ...hostReady,
      type: "plugin.rpc.response",
      request_id: "req-1",
      ok: true,
      result: { applied: true },
    });
    await expect(request).resolves.toEqual({ applied: true });
  });

  it("plugin.error закрывает канал без копирования текста плагина", async () => {
    const onError = vi.fn<(error: PluginFrameError) => void>();
    const ch = makeChannel({ onError });
    const { hostReady } = await complete(ch);
    emit({
      ...hostReady,
      type: "plugin.error",
      error: { code: "CAPABILITY_DENIED", message: "SECRET must not leak", trace_id: "trace-1" },
    });
    expect(ch.disposed).toBe(true);
    const error = onError.mock.calls[0][0] as PluginFrameError;
    expect(error.message).not.toContain("SECRET");
    expect(error.detail).not.toContain("SECRET");
  });

  it("dispose идемпотентен, reject'ит pending handshake и блокирует frame", async () => {
    const ch = makeChannel();
    const handshake = ch.handshake();
    ch.dispose();
    ch.dispose();
    await expect(handshake).rejects.toMatchObject({ code: "disposed" });
    expect(iframe.dataset.pluginBlocked).toBe("true");
    await expect(ch.request("host.resize", { height: 100 })).rejects.toMatchObject({ code: "disposed" });
  });

  it("без обязательного artifactDigest канал не создаётся", () => {
    expect(() => createPluginChannel(iframe, OPAQUE_ORIGIN, {
      pluginId: PLUGIN_ID,
      version: VERSION,
    })).toThrowError(PluginFrameError);
  });
});
