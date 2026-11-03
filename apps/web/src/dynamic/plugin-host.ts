import {
  OriginPolicyError,
  resolvePeerOriginPolicy,
  type OriginPolicyMode,
  type PeerOriginPolicy,
} from "../capabilities/origin-policy";
import { scopeMismatch, type ScopeInput, type ScopedContext } from "../capabilities/scope";
import { createCapabilityBroker, BROKER_PROTOCOL, PREVIEW_BROKER_CAPABILITIES, type BrokerCapability, type BrokerPolicy } from "./broker";

export interface PluginIdentity { readonly pluginId: string; readonly version: string; readonly digest: string; }
export interface PluginHostOptions {
  readonly document: Document;
  readonly container: HTMLElement;
  readonly src: string;
  /** Production deployment origins. Exact `https://host[:port]` values only; wildcards are rejected. */
  readonly allowedOrigins: ReadonlySet<string>;
  readonly identity: PluginIdentity;
  readonly broker: BrokerPolicy;
  readonly capabilityVersions?: ReadonlyMap<string, string>;
  readonly timeoutMs?: number;
  readonly maxMessageBytes?: number;
  readonly onFailure?: (code: string) => void;
  readonly onReady?: () => void;
  readonly onRejectedMessage?: (reason: PluginMessageRejectReason) => void;
  /**
   * Isolation policy. `production` (default) accepts only the exact registered origin. `preview`
   * uses its own allow-list that must not intersect production origins or the shell origin/site,
   * and is the only mode that may accept the opaque origin of a sandboxed frame.
   */
  readonly mode?: OriginPolicyMode;
  readonly previewOrigins?: ReadonlySet<string>;
  readonly shellOrigin?: string;
  readonly allowOpaqueOrigin?: boolean;
  /** Scope re-resolved by the server bridge; plugin-supplied scope fields are assertions only. */
  readonly scope?: ScopedContext;
}
export type PluginMessageRejectReason = "origin" | "source" | "protocol";
export interface IsolatedPluginHandle {
  readonly frame: HTMLIFrameElement;
  readonly instanceId: string;
  readonly active: boolean;
  destroy(): void;
  kill(): void;
}
const MAX_BYTES = 64 * 1024;
const MAX_ACTIVE = 16;
const MAX_SEEN_REQUESTS = 2_048;
const METHOD_ID = /^[a-z][a-z0-9._-]{0,127}$/;
const PLUGIN_ERROR_CODES = new WeakMap<object, string>();
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
function refused(code: string): TypeError {
  const error = new TypeError(code);
  PLUGIN_ERROR_CODES.set(error, code);
  return error;
}
function mapPolicyError(error: unknown): TypeError {
  if (!(error instanceof OriginPolicyError)) return refused("plugin_isolation_policy_invalid");
  switch (error.code) {
    case "peer_origin_not_allowlisted":
      return refused("plugin_origin_not_allowlisted");
    case "preview_allowlist_required":
    case "preview_allowlist_not_isolated":
    case "preview_shares_shell_origin":
    case "preview_shares_shell_site":
    case "peer_origin_is_production":
      return refused("plugin_preview_policy_invalid");
    case "opaque_origin_denied_in_production":
    case "origin_policy_mode_invalid":
      return refused("plugin_isolation_policy_invalid");
    case "wildcard_origin_denied":
    case "origin_list_invalid":
      return refused("plugin_origin_allowlist_invalid");
    default:
      return refused("plugin_origin_allowlist_invalid");
  }
}

/**
 * Mounts a code plugin in an isolated iframe. The host never accepts a plugin-provided URL, never
 * enables `allow-same-origin`, and accepts `postMessage` only from the exact registered peer.
 */
export function mountIsolatedPlugin(options: PluginHostOptions): IsolatedPluginHandle {
  let parsed: URL;
  try { parsed = new URL(options.src); } catch { throw refused("plugin_origin_not_allowlisted"); }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) throw refused("plugin_origin_not_allowlisted");
  if (!options.identity.pluginId || !options.identity.version || !/^sha256:[a-f0-9]{64}$/.test(options.identity.digest)) throw refused("plugin_identity_invalid");
  if (options.mode !== undefined && options.mode !== "production" && options.mode !== "preview") throw refused("plugin_isolation_policy_invalid");

  let policy: PeerOriginPolicy;
  try {
    policy = resolvePeerOriginPolicy({
      peerOrigin: parsed.origin,
      mode: options.mode,
      allowedOrigins: options.allowedOrigins,
      previewOrigins: options.previewOrigins,
      shellOrigin: options.shellOrigin,
      allowOpaqueOrigin: options.allowOpaqueOrigin ?? options.mode === "preview",
    });
  } catch (error) {
    throw mapPolicyError(error);
  }
  if (policy.mode === "preview") {
    // A preview instance may not reach mutation or secret-bearing capabilities at all, even if the
    // host policy would technically allow them for a production instance.
    for (const capability of options.broker.allowedCapabilities) {
      if (!PREVIEW_BROKER_CAPABILITIES.has(capability as BrokerCapability)) throw refused("plugin_preview_capability_denied");
    }
    // The preview frame is sandboxed without `allow-same-origin`, so its origin is opaque; preview
    // isolation therefore rests on the exact `WindowProxy`, the instance nonce and the identity
    // handshake instead of an origin string.
    if (!policy.allowsOpaqueOrigin) throw refused("plugin_preview_policy_invalid");
  }

  const instanceId = randomId();
  const broker = createCapabilityBroker(options.broker);
  const hostWindow = options.document.defaultView;
  if (!hostWindow) throw refused("plugin_host_window_unavailable");
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
  const send = (message: Record<string, unknown>) => {
    try { frame.contentWindow?.postMessage(message, policy.targetOrigin); } catch { /* A frame that cannot receive a message cannot complete the handshake. */ }
  };
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
    if (closed || event.source !== frame.contentWindow) return;
    if (!policy.accepts(event.origin)) { try { options.onRejectedMessage?.("origin"); } catch { /* ignore */ } return; }
    if (!isRecord(event.data)) return;
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
    if (seenRequests.size > MAX_SEEN_REQUESTS) seenRequests.delete(seenRequests.values().next().value as string);
    if (cap.id !== options.broker.methods.get(input.method as string)) { respond(requestId, false, undefined, "CAPABILITY_DENIED"); return; }
    // The plugin's `context` is an assertion. It is compared with the server-proven scope and can
    // only narrow the call: a value the bridge did not resolve is refused, never adopted.
    const claim = message.context as ScopeInput;
    if (scopeMismatch(claim, options.scope).length) { respond(requestId, false, undefined, "CONTEXT_MISMATCH"); return; }
    const controller = new AbortController(); active.set(requestId, controller);
    try {
      const result = await broker.invoke(input.method as string, input.params ?? {}, controller.signal, claim);
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

/** Stable refusal code for a mount that was rejected by the isolation policy. */
export function pluginRefusalCode(error: unknown): string | undefined {
  return error && typeof error === "object" ? PLUGIN_ERROR_CODES.get(error) : undefined;
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
