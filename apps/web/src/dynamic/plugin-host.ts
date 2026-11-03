import { createCapabilityBroker, BROKER_PROTOCOL, type BrokerPolicy } from "./broker";

export interface PluginIdentity { readonly pluginId: string; readonly version: string; readonly digest: string; }
export interface PluginHostOptions {
  readonly document: Document;
  readonly container: HTMLElement;
  readonly src: string;
  readonly allowedOrigins: ReadonlySet<string>;
  readonly identity: PluginIdentity;
  readonly broker: BrokerPolicy;
  readonly capabilityVersions?: ReadonlyMap<string, string>;
  readonly timeoutMs?: number;
  readonly maxMessageBytes?: number;
  readonly onFailure?: (code: string) => void;
  readonly onReady?: () => void;
}
export interface IsolatedPluginHandle {
  readonly frame: HTMLIFrameElement;
  readonly instanceId: string;
  readonly active: boolean;
  destroy(): void;
  kill(): void;
}
const MAX_BYTES = 64 * 1024;
const MAX_ACTIVE = 16;
const METHOD_ID = /^[a-z][a-z0-9._-]{0,127}$/;
function isRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function exact(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  const allowed = new Set([...required, ...optional]);
  return Object.getOwnPropertySymbols(value).length === 0 && Object.getOwnPropertyNames(value).every((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return allowed.has(key) && !["__proto__", "prototype", "constructor"].includes(key) && Boolean(descriptor && "value" in descriptor && descriptor.enumerable);
  }) && required.every((key) => Object.hasOwn(value, key));
}
function messageBytes(value: unknown): number {
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength; } catch { return MAX_BYTES + 1; }
}
function safeError(error: unknown): string {
  return (error instanceof Error ? error.message : "request_failed").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 80) || "request_failed";
}
function randomId(): string {
  const bytes = new Uint8Array(18);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

export function mountIsolatedPlugin(options: PluginHostOptions): IsolatedPluginHandle {
  let parsed: URL;
  try { parsed = new URL(options.src); } catch { throw new TypeError("plugin_origin_not_allowlisted"); }
  if (!options.allowedOrigins.has(parsed.origin) || parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) throw new TypeError("plugin_origin_not_allowlisted");
  if (!options.identity.pluginId || !options.identity.version || !/^sha256:[a-f0-9]{64}$/.test(options.identity.digest)) throw new TypeError("plugin_identity_invalid");
  const instanceId = randomId();
  const broker = createCapabilityBroker(options.broker);
  const hostWindow = options.document.defaultView;
  if (!hostWindow) throw new TypeError("plugin_host_window_unavailable");
  const frame = options.document.createElement("iframe");
  frame.title = `Plugin ${options.identity.pluginId}`;
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.setAttribute("allow", "");
  frame.setAttribute("aria-label", frame.title);
  frame.src = parsed.href;

  let closed = false;
  let ready = false;
  let loadCount = 0;
  const active = new Map<string, AbortController>();
  const seenRequests = new Set<string>();
  const timeoutMs = Math.max(100, Math.min(options.timeoutMs ?? 10_000, 60_000));
  const maxBytes = Math.max(1024, Math.min(options.maxMessageBytes ?? MAX_BYTES, MAX_BYTES));
  const nonce = randomId();
  const appHandle = randomId();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const fail = (code: string) => { if (closed) return; try { options.onFailure?.(code); } catch { /* Diagnostic hooks cannot affect isolation. */ } destroy(); };
  const send = (message: Record<string, unknown>) => frame.contentWindow?.postMessage(message, "*");
  const respond = (requestId: string, ok: boolean, result?: unknown, code?: string) => {
    const response = { protocol: BROKER_PROTOCOL, kind: "response", requestId, instanceId, ok,
      ...(ok ? { result } : { error: { code: code ?? "REQUEST_FAILED", message: code ?? "REQUEST_FAILED", traceId: "unavailable" } }) };
    if (messageBytes(response) <= maxBytes) send(response);
  };
  const destroy = () => {
    if (closed) return;
    closed = true;
    if (timer !== undefined) clearTimeout(timer);
    for (const controller of active.values()) controller.abort();
    active.clear();
    hostWindow.removeEventListener("message", onWindowMessage);
    frame.removeEventListener("load", onLoad);
    frame.removeEventListener("error", onFrameError);
    broker.kill();
    frame.remove();
  };
  const onLoad = () => {
    loadCount += 1;
    if (loadCount > 1) { fail("PLUGIN_NAVIGATION_BLOCKED"); return; }
    send({ protocol: BROKER_PROTOCOL, type: "host.ready", instance_id: instanceId, nonce, app_handle: appHandle });
  };
  const onFrameError = () => fail("PLUGIN_FRAME_ERROR");
  const onWindowMessage = (event: MessageEvent) => {
    if (closed || event.source !== frame.contentWindow || event.origin !== "null" || !isRecord(event.data)) return;
    const message = event.data;
    if (messageBytes(message) > maxBytes || message.protocol !== BROKER_PROTOCOL) { fail("PLUGIN_RPC_INVALID"); return; }
    if (!ready) {
      if (!exact(message, ["protocol", "type", "instance_id", "nonce", "plugin_id", "version", "digest"]) || message.type !== "plugin.ready" || message.instance_id !== instanceId || message.nonce !== nonce || message.plugin_id !== options.identity.pluginId || message.version !== options.identity.version || message.digest !== options.identity.digest) { fail("PLUGIN_IDENTITY_MISMATCH"); return; }
      ready = true;
      if (timer !== undefined) clearTimeout(timer);
      try { options.onReady?.(); } catch { /* Ready observers cannot affect isolation. */ }
      const capabilities = [...options.broker.allowedCapabilities].flatMap((id) => {
        const version = options.capabilityVersions?.get(id);
        return version && [...options.broker.methods.values()].includes(id as never) ? [{ id, version }] : [];
      });
      send({ protocol: BROKER_PROTOCOL, type: "grant.ready", instance_id: instanceId, nonce, capabilities });
      return;
    }
    if (!exact(message, ["protocol", "kind", "requestId", "instanceId", "method", "capability", "context", "input"], ["idempotencyKey"])) { fail("PLUGIN_RPC_INVALID"); return; }
    void handleRequest(message);
  };
  const handleRequest = async (message: Record<string, unknown>) => {
    const cap = message.capability;
    const input = message.input;
    const valid = message.kind === "request" && message.instanceId === instanceId && message.method === "capability.call"
      && typeof message.requestId === "string" && /^[A-Za-z0-9._:-]{8,128}$/.test(message.requestId)
      && isRecord(cap) && exact(cap, ["id", "version"]) && typeof cap.id === "string" && METHOD_ID.test(cap.id)
      && typeof cap.version === "string" && options.capabilityVersions?.get(cap.id) === cap.version
      && isRecord(message.context) && exact(message.context, [], ["cabinetId", "projectId", "sessionId"])
      && Object.values(message.context).every((value) => typeof value === "string" && value.length <= 256)
      && (message.idempotencyKey === undefined || (typeof message.idempotencyKey === "string" && /^[A-Za-z0-9._:-]{8,128}$/.test(message.idempotencyKey)))
      && isRecord(input) && exact(input, ["method"], ["params"]) && typeof input.method === "string" && METHOD_ID.test(input.method)
      && (input.params === undefined || safeJson(input.params));
    if (!valid) { fail("PLUGIN_RPC_INVALID"); return; }
    const requestId = message.requestId as string;
    if (seenRequests.has(requestId)) { respond(requestId, false, undefined, "DUPLICATE_REQUEST"); return; }
    if (active.size >= MAX_ACTIVE) { respond(requestId, false, undefined, "REQUEST_LIMIT"); return; }
    seenRequests.add(requestId);
    if (cap.id !== options.broker.methods.get(input.method as string)) { respond(requestId, false, undefined, "CAPABILITY_DENIED"); return; }
    const controller = new AbortController(); active.set(requestId, controller);
    try {
      const result = await broker.invoke(input.method as string, input.params ?? {}, controller.signal);
      if (!closed && !controller.signal.aborted) {
        if (!safeJson(result) || messageBytes(result) > maxBytes) respond(requestId, false, undefined, "INVALID_RESPONSE");
        else respond(requestId, true, result);
      }
    } catch (error) { if (!closed) respond(requestId, false, undefined, safeError(error)); }
    finally { active.delete(requestId); }
  };
  hostWindow.addEventListener("message", onWindowMessage);
  frame.addEventListener("load", onLoad);
  frame.addEventListener("error", onFrameError, { once: true });
  options.container.replaceChildren(frame);
  timer = setTimeout(() => fail("PLUGIN_HANDSHAKE_TIMEOUT"), timeoutMs);
  return { frame, instanceId, destroy, kill: () => fail("PLUGIN_KILLED"), get active() { return !closed && ready; } };
}

function safeJson(value: unknown, depth = 0, budget = { nodes: 0 }): boolean {
  budget.nodes += 1;
  if (budget.nodes > 2_000 || depth > 12) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "string") return value.length <= 16_384;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 2_000 && value.every((item) => safeJson(item, depth + 1, budget));
  return isRecord(value) && Object.entries(value).length <= 2_000 && Object.entries(value).every(([key, item]) => !["__proto__", "prototype", "constructor"].includes(key) && safeJson(item, depth + 1, budget));
}
