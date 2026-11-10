/**
 * Единый двунаправленный plugin-rpc/1 канал shell ↔ iframe.
 *
 * Доверие не выводится из payload: сначала проверяются exact WindowProxy и
 * MessageEvent.origin. Для sandboxed opaque origin ("null") дополнительно
 * обязательны nonce, instance_id и artifact_digest из реестра. "*" используется
 * только как технический targetOrigin при отправке в opaque frame.
 */
import {
  PLUGIN_RPC_PROTOCOL,
  type CapabilityRef,
  type GrantReadyMessage,
  type HostReadyMessage,
  type JsonValue,
  type PluginErrorMessage,
  type PluginMessageBase,
  type PluginReadyMessage,
  type PluginRequestContext,
  type PluginRpcError,
  type PluginRpcErrorCode,
  type PluginRpcMessage,
  type PluginRpcMethod,
  type PluginRpcRequest,
  type PluginRpcResponse,
} from "../../../../../packages/schemas/plugin-manifest/v1/types";
import { PluginFrameError, pluginErrorFromCode } from "./errors";
import { BLOCKED_FRAME_SRCDOC } from "./frameBlock";

export { PLUGIN_RPC_PROTOCOL };
export type {
  GrantReadyMessage,
  HostReadyMessage,
  PluginErrorMessage,
  PluginReadyMessage,
  PluginRequestContext,
  PluginRpcError,
  PluginRpcErrorCode,
  PluginRpcMethod,
  PluginRpcRequest,
  PluginRpcResponse,
} from "../../../../../packages/schemas/plugin-manifest/v1/types";

export type PluginRpcRequestMessage = PluginRpcRequest;
export type PluginRpcResponseMessage = PluginRpcResponse;
export type PluginChannelMessage = PluginRpcMessage;
export type PluginRequestHandler = (
  request: PluginRpcRequest,
) => JsonValue | Promise<JsonValue>;

export type PluginChannelOptions = {
  /** Ожидаемая identity из реестра; все три значения обязательны для handshake. */
  readonly pluginId?: string;
  readonly version?: string;
  readonly artifactDigest?: string;
  /** Effective capabilities: capability.call требует точный "id@version". */
  readonly capabilities?: readonly string[];
  readonly scope?: PluginRequestContext;
  /** Backend expiry, если известно; effective expiry = min(backend, local lease). */
  readonly grantExpiresAt?: string;
  readonly grantTtlMs?: number;
  readonly now?: () => number;
  readonly timeoutMs?: number;
  readonly requestTimeoutMs?: number;
  readonly hostWindow?: Window | null;
  readonly makeRequestId?: () => string;
  readonly allowedMethods?: readonly PluginRpcMethod[];
  readonly onRequest?: PluginRequestHandler;
  readonly onReady?: (message: PluginReadyMessage) => void;
  readonly onError?: (error: PluginFrameError) => void;
  readonly onRejected?: (reason: "origin" | "source" | "shape") => void;
};

export interface PluginChannel {
  readonly expectedOrigin: string;
  readonly ready: boolean;
  readonly disposed: boolean;
  handshake(): Promise<PluginReadyMessage>;
  request<TResult = unknown>(
    method: PluginRpcMethod,
    input?: JsonValue | { path: string } | { height: number },
    options?: { readonly capability?: CapabilityRef; readonly idempotencyKey?: string },
  ): Promise<TResult>;
  dispose(): void;
}

export type PluginChannelFactory = (
  iframe: HTMLIFrameElement,
  expectedOrigin: string,
  options: PluginChannelOptions,
) => PluginChannel;

export const OPAQUE_ORIGIN = "null";
export const PLUGIN_RPC_MAX_MESSAGE_BYTES = 64 * 1024;

const DEFAULT_TIMEOUT_MS = 10_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const DEFAULT_GRANT_TTL_MS = 5 * 60_000;
const MAX_GRANT_TTL_MS = 15 * 60_000;
const MAX_REQUEST_IDS = 2_048;
const PLUGIN_ID = /^[a-z][a-z0-9_]{0,127}$/;
const SEMVER = /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)(?:[.](?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:[+]([0-9a-zA-Z-]+(?:[.][0-9a-zA-Z-]+)*))?$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const INSTANCE_TOKEN = /^[A-Za-z0-9_-]{16,128}$/;
const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const CAPABILITY_ID = /^[a-z][a-z0-9._-]{0,127}$/;
const CAPABILITY_VERSION = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/;
const GRANT_CAPABILITY = /^[a-z][a-z0-9._-]{0,127}(?:@[0-9A-Za-z][0-9A-Za-z._+-]{0,63})?$/;
const NAVIGATION_PATH = /^[/](?![/])[A-Za-z0-9._/-]{0,511}$/;
const METHODS: readonly PluginRpcMethod[] = ["capability.call", "host.navigate", "host.resize"];
const ERROR_CODES: readonly PluginRpcErrorCode[] = [
  "ORIGIN_MISMATCH", "PROTOCOL_UNSUPPORTED", "HANDSHAKE_MISMATCH", "HANDSHAKE_TIMEOUT",
  "NONCE_MISMATCH", "UNKNOWN_METHOD", "CAPABILITY_DENIED", "DUPLICATE_REQUEST_ID",
  "PAYLOAD_TOO_LARGE", "SCOPE_DENIED", "GRANT_EXPIRED", "INVALID_ENVELOPE",
  "REQUEST_FAILED", "REQUEST_TIMEOUT",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function exactShape(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return Object.getOwnPropertySymbols(value).length === 0
    && Object.getOwnPropertyNames(value).every((key) => {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      return allowed.has(key)
        && !["__proto__", "prototype", "constructor"].includes(key)
        && Boolean(descriptor && "value" in descriptor && descriptor.enumerable);
    })
    && required.every((key) => Object.hasOwn(value, key));
}

function safeJson(value: unknown, depth = 0, budget = { nodes: 0 }): boolean {
  budget.nodes += 1;
  if (budget.nodes > 2_000 || depth > 12) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "string") return value.length <= 65_536;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) {
    return value.length <= 2_000 && value.every((item) => safeJson(item, depth + 1, budget));
  }
  return isRecord(value) && Object.keys(value).length <= 2_000
    && Object.entries(value).every(([key, item]) =>
      !["__proto__", "prototype", "constructor"].includes(key)
      && safeJson(item, depth + 1, budget));
}

function messageBytes(value: unknown): number {
  try {
    const serialized = JSON.stringify(value);
    if (serialized === undefined) return Number.MAX_SAFE_INTEGER;
    return new TextEncoder().encode(serialized).byteLength;
  } catch {
    return Number.MAX_SAFE_INTEGER;
  }
}

function boundedTimeout(value: number | undefined, fallback: number): number {
  return Math.max(100, Math.min(value ?? fallback, 120_000));
}

function randomToken(): string {
  if (!globalThis.crypto?.getRandomValues) {
    throw new PluginFrameError("loadError", "plugin_secure_random_unavailable");
  }
  const bytes = new Uint8Array(18);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function defaultRequestId(): string {
  return `rpc-${randomToken()}`;
}

function isContext(value: unknown): value is PluginRequestContext {
  if (!isRecord(value) || !exactShape(value, [], ["company_id", "cabinet_id", "project_id", "session_id"])) {
    return false;
  }
  return Object.values(value).every((item) =>
    typeof item === "string" && item.length > 0 && item.length <= 128);
}

function contextsEqual(left: PluginRequestContext, right: PluginRequestContext): boolean {
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && left[key as keyof PluginRequestContext] === right[key as keyof PluginRequestContext]);
}

function isCapabilityRef(value: unknown): value is CapabilityRef {
  return isRecord(value)
    && exactShape(value, ["id", "version"])
    && typeof value.id === "string" && CAPABILITY_ID.test(value.id)
    && typeof value.version === "string" && CAPABILITY_VERSION.test(value.version);
}

function isRpcError(value: unknown): value is PluginRpcError {
  return isRecord(value)
    && exactShape(value, ["code", "message", "trace_id"])
    && typeof value.code === "string" && ERROR_CODES.includes(value.code as PluginRpcErrorCode)
    && typeof value.message === "string" && value.message.length > 0 && value.message.length <= 500
    && typeof value.trace_id === "string" && REQUEST_ID.test(value.trace_id);
}

function validateNavigation(value: unknown): boolean {
  return isRecord(value) && exactShape(value, ["path"])
    && typeof value.path === "string" && NAVIGATION_PATH.test(value.path)
    && !value.path.split("/").includes("..");
}

function validateResize(value: unknown): boolean {
  return isRecord(value) && exactShape(value, ["height"])
    && typeof value.height === "number" && Number.isFinite(value.height)
    && value.height >= 0 && value.height <= 10_000;
}

type RequestValidation =
  | { readonly ok: true; readonly request: PluginRpcRequest }
  | { readonly ok: false; readonly code: PluginRpcErrorCode };

function validateRequest(data: Record<string, unknown>): RequestValidation {
  const required = [
    "protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce",
    "request_id", "method", "context", "input",
  ];
  if (!exactShape(data, required, ["capability", "idempotency_key"]) || data.type !== "plugin.rpc.request") {
    return { ok: false, code: "INVALID_ENVELOPE" };
  }
  if (typeof data.request_id !== "string" || !REQUEST_ID.test(data.request_id) || !isContext(data.context)) {
    return { ok: false, code: "INVALID_ENVELOPE" };
  }
  if (typeof data.method !== "string" || !METHODS.includes(data.method as PluginRpcMethod)) {
    return { ok: false, code: "UNKNOWN_METHOD" };
  }
  if (data.idempotency_key !== undefined
    && (typeof data.idempotency_key !== "string" || !REQUEST_ID.test(data.idempotency_key))) {
    return { ok: false, code: "INVALID_ENVELOPE" };
  }
  if (data.method === "capability.call") {
    if (!isCapabilityRef(data.capability) || typeof data.idempotency_key !== "string" || !safeJson(data.input)) {
      return { ok: false, code: "INVALID_ENVELOPE" };
    }
  } else {
    if (data.capability !== undefined) return { ok: false, code: "INVALID_ENVELOPE" };
    if (data.method === "host.navigate" && !validateNavigation(data.input)) {
      return { ok: false, code: "INVALID_ENVELOPE" };
    }
    if (data.method === "host.resize" && !validateResize(data.input)) {
      return { ok: false, code: "INVALID_ENVELOPE" };
    }
  }
  return { ok: true, request: data as unknown as PluginRpcRequest };
}

function brokerDenialCode(cause: unknown): PluginRpcErrorCode | undefined {
  if (typeof cause !== "object" || cause === null) return undefined;
  const rpcCode = (cause as { readonly rpcCode?: unknown }).rpcCode;
  if (typeof rpcCode !== "string") return undefined;
  return ERROR_CODES.includes(rpcCode as PluginRpcErrorCode)
    ? rpcCode as PluginRpcErrorCode
    : undefined;
}

function errorMessage(code: PluginRpcErrorCode): string {
  switch (code) {
    case "UNKNOWN_METHOD": return "RPC method is not allowlisted";
    case "CAPABILITY_DENIED": return "Capability is not granted";
    case "DUPLICATE_REQUEST_ID": return "request_id was already processed";
    case "PAYLOAD_TOO_LARGE": return "RPC payload exceeds the bounded channel limit";
    case "SCOPE_DENIED": return "RPC context does not match the effective grant";
    case "GRANT_EXPIRED": return "The effective grant has expired";
    case "REQUEST_FAILED": return "The typed request handler failed";
    case "REQUEST_TIMEOUT": return "The typed request handler timed out";
    default: return "Invalid plugin-rpc/1 envelope";
  }
}

export function createPluginChannel(
  iframe: HTMLIFrameElement,
  expectedOrigin: string,
  options: PluginChannelOptions = {},
): PluginChannel {
  const hostWindow = options.hostWindow === undefined ? iframe.ownerDocument.defaultView : options.hostWindow;
  const sourceWindow = iframe.contentWindow;
  if (!hostWindow || !sourceWindow) {
    throw new PluginFrameError("loadError", "plugin_host_window_unavailable");
  }
  if (typeof expectedOrigin !== "string" || !expectedOrigin || expectedOrigin === "*") {
    throw new PluginFrameError("originMismatch", "plugin_expected_origin_invalid");
  }
  if (expectedOrigin !== OPAQUE_ORIGIN) {
    let parsed: URL;
    try { parsed = new URL(expectedOrigin); } catch {
      throw new PluginFrameError("originMismatch", "plugin_expected_origin_invalid");
    }
    if (parsed.protocol !== "https:" || parsed.origin !== expectedOrigin) {
      throw new PluginFrameError("originMismatch", "plugin_expected_origin_invalid");
    }
  }

  const expectedPluginId = options.pluginId ?? iframe.dataset.pluginId ?? "";
  const expectedVersion = options.version ?? iframe.dataset.pluginVersion ?? "";
  const expectedDigest = options.artifactDigest ?? "";
  if (!PLUGIN_ID.test(expectedPluginId) || !SEMVER.test(expectedVersion) || !DIGEST.test(expectedDigest)) {
    throw new PluginFrameError("identityMismatch", "plugin_identity_unavailable");
  }
  const capabilities = [...(options.capabilities ?? [])];
  if (capabilities.length > 64 || new Set(capabilities).size !== capabilities.length
    || capabilities.some((item) => !GRANT_CAPABILITY.test(item))) {
    throw new PluginFrameError("notGranted", "plugin_capabilities_invalid");
  }
  const scope = options.scope ?? {};
  if (!isContext(scope)) throw new PluginFrameError("notGranted", "plugin_scope_invalid");

  const now = options.now ?? (() => Date.now());
  const requestedGrantTtl = options.grantTtlMs ?? DEFAULT_GRANT_TTL_MS;
  if (!Number.isFinite(requestedGrantTtl) || !Number.isFinite(now())) {
    throw new PluginFrameError("notGranted", "plugin_grant_expiry_invalid");
  }
  const grantTtlMs = Math.max(1_000, Math.min(requestedGrantTtl, MAX_GRANT_TTL_MS));
  const localExpiry = now() + grantTtlMs;
  let backendExpiry = Number.POSITIVE_INFINITY;
  if (options.grantExpiresAt !== undefined) {
    backendExpiry = Date.parse(options.grantExpiresAt);
    if (!Number.isFinite(backendExpiry)) throw new PluginFrameError("notGranted", "plugin_grant_expiry_invalid");
  }
  const grantExpiry = Math.min(localExpiry, backendExpiry);
  if (grantExpiry <= now()) throw new PluginFrameError("notGranted", "plugin_grant_expired");
  const grantExpiresAt = new Date(grantExpiry).toISOString();
  const handshakeTimeoutMs = boundedTimeout(options.timeoutMs, DEFAULT_TIMEOUT_MS);
  const requestTimeoutMs = boundedTimeout(options.requestTimeoutMs, DEFAULT_REQUEST_TIMEOUT_MS);
  const makeRequestId = options.makeRequestId ?? defaultRequestId;
  const allowedMethods = new Set<PluginRpcMethod>(options.allowedMethods ?? METHODS);
  const instanceId = randomToken();
  const nonce = randomToken();

  let disposed = false;
  let ready = false;
  let loadSeen = false;
  let hostReadySent = false;
  let handshakeRequested = false;
  let readyMessage: PluginReadyMessage | undefined;
  let handshakeResolve: ((message: PluginReadyMessage) => void) | undefined;
  let handshakeReject: ((error: PluginFrameError) => void) | undefined;
  let handshakePromise: Promise<PluginReadyMessage> | undefined;
  let handshakeTimer: ReturnType<typeof setTimeout> | undefined;
  let grantTimer: ReturnType<typeof setTimeout> | undefined;

  type Pending = {
    resolve: (value: unknown) => void;
    reject: (error: PluginFrameError) => void;
    timer: ReturnType<typeof setTimeout>;
  };
  const pending = new Map<string, Pending>();
  const seenResponses = new Set<string>();
  const seenIncomingRequests = new Set<string>();

  const base = (): PluginMessageBase => ({
    protocol: PLUGIN_RPC_PROTOCOL,
    plugin_id: expectedPluginId,
    version: expectedVersion,
    artifact_digest: expectedDigest,
    instance_id: instanceId,
    nonce,
  });

  const reject = (reason: "origin" | "source" | "shape"): void => {
    try { options.onRejected?.(reason); } catch { /* Diagnostics cannot affect isolation. */ }
  };

  const neutralizeFrame = (): void => {
    // srcdoc wins over src: clear both so a disposed channel cannot keep executing
    // a loaded plugin or be revived by changing only src on a later navigation.
    iframe.removeAttribute("src");
    iframe.setAttribute("srcdoc", BLOCKED_FRAME_SRCDOC);
    iframe.hidden = true;
    iframe.setAttribute("aria-hidden", "true");
    iframe.dataset.pluginBlocked = "true";
  };

  const close = (error: PluginFrameError): void => {
    if (disposed) return;
    disposed = true;
    ready = false;
    hostWindow.removeEventListener("message", onMessage);
    iframe.removeEventListener("load", onLoad);
    if (handshakeTimer !== undefined) { clearTimeout(handshakeTimer); handshakeTimer = undefined; }
    if (grantTimer !== undefined) { clearTimeout(grantTimer); grantTimer = undefined; }
    const rejectHandshake = handshakeReject;
    handshakeResolve = undefined;
    handshakeReject = undefined;
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(error);
    }
    pending.clear();
    seenResponses.clear();
    seenIncomingRequests.clear();
    neutralizeFrame();
    rejectHandshake?.(error);
  };

  const fail = (error: PluginFrameError): void => {
    if (disposed) return;
    close(error);
    try { options.onError?.(error); } catch { /* Diagnostics cannot affect isolation. */ }
  };

  const send = (message: PluginRpcMessage): boolean => {
    if (disposed) return false;
    if (!safeJson(message) || messageBytes(message) > PLUGIN_RPC_MAX_MESSAGE_BYTES) return false;
    if (iframe.contentWindow !== sourceWindow) {
      fail(new PluginFrameError("identityMismatch", "plugin_window_changed"));
      return false;
    }
    const targetOrigin = expectedOrigin === OPAQUE_ORIGIN ? "*" : expectedOrigin;
    try {
      sourceWindow.postMessage(message, targetOrigin);
      return true;
    } catch {
      return false;
    }
  };

  const sendFailure = (requestId: string, code: PluginRpcErrorCode): void => {
    if (disposed || !REQUEST_ID.test(requestId)) return;
    const response: PluginRpcResponse = {
      ...base(),
      type: "plugin.rpc.response",
      request_id: requestId,
      ok: false,
      error: { code, message: errorMessage(code), trace_id: requestId },
    };
    if (!send(response)) {
      fail(new PluginFrameError("loadError", "plugin_rpc_response_failed"));
    }
  };

  const hasExpectedIdentity = (data: Record<string, unknown>): boolean =>
    data.protocol === PLUGIN_RPC_PROTOCOL
    && data.plugin_id === expectedPluginId
    && data.version === expectedVersion
    && data.artifact_digest === expectedDigest
    && data.instance_id === instanceId
    && data.nonce === nonce;

  const grantExpired = (): boolean => now() >= grantExpiry;
  const failIfGrantExpired = (): boolean => {
    if (!grantExpired()) return false;
    fail(new PluginFrameError("notGranted", "plugin_grant_expired"));
    return true;
  };

  const onHostRequestResult = (request: PluginRpcRequest, result: unknown): void => {
    if (disposed) return;
    if (!safeJson(result)) {
      sendFailure(request.request_id, "INVALID_ENVELOPE");
      return;
    }
    const response: PluginRpcResponse = {
      ...base(),
      type: "plugin.rpc.response",
      request_id: request.request_id,
      ok: true,
      result: result as JsonValue,
    };
    if (messageBytes(response) > PLUGIN_RPC_MAX_MESSAGE_BYTES) {
      sendFailure(request.request_id, "PAYLOAD_TOO_LARGE");
      return;
    }
    if (!send(response)) fail(new PluginFrameError("loadError", "plugin_rpc_response_failed"));
  };

  const routeHostRequest = (request: PluginRpcRequest): void => {
    if (!allowedMethods.has(request.method)) {
      sendFailure(request.request_id, "UNKNOWN_METHOD");
      return;
    }
    if (!contextsEqual(request.context, scope)) {
      sendFailure(request.request_id, "SCOPE_DENIED");
      return;
    }
    if (request.method === "capability.call"
      && !capabilities.includes(`${request.capability.id}@${request.capability.version}`)) {
      sendFailure(request.request_id, "CAPABILITY_DENIED");
      return;
    }
    if (!options.onRequest) {
      sendFailure(request.request_id, "CAPABILITY_DENIED");
      return;
    }

    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, rejectTimeout) => {
      timer = setTimeout(() => rejectTimeout(new PluginFrameError("timeout", "plugin_host_request_timeout")), requestTimeoutMs);
    });
    let handler: Promise<JsonValue>;
    try {
      handler = Promise.resolve(options.onRequest(request));
    } catch (cause: unknown) {
      if (timer !== undefined) clearTimeout(timer);
      sendFailure(request.request_id, brokerDenialCode(cause) ?? "REQUEST_FAILED");
      return;
    }
    void Promise.race([handler, timeout]).then((result) => {
      if (timer !== undefined) clearTimeout(timer);
      onHostRequestResult(request, result);
    }).catch((cause: unknown) => {
      if (timer !== undefined) clearTimeout(timer);
      if (disposed) return;
      const code: PluginRpcErrorCode = cause instanceof PluginFrameError && cause.code === "timeout"
        ? "REQUEST_TIMEOUT" : brokerDenialCode(cause) ?? "REQUEST_FAILED";
      sendFailure(request.request_id, code);
    });
  };

  const onIncomingRequest = (data: Record<string, unknown>): void => {
    if (failIfGrantExpired()) return;
    const validation = validateRequest(data);
    const requestId = typeof data.request_id === "string" && REQUEST_ID.test(data.request_id)
      ? data.request_id : "";
    if (!validation.ok) {
      if (requestId) sendFailure(requestId, validation.code);
      else fail(new PluginFrameError("loadError", "plugin_rpc_invalid_request"));
      return;
    }
    const request = validation.request;
    if (seenIncomingRequests.has(request.request_id)) {
      sendFailure(request.request_id, "DUPLICATE_REQUEST_ID");
      return;
    }
    if (seenIncomingRequests.size >= MAX_REQUEST_IDS) {
      // Do not evict replay protection: a bounded channel fails closed instead.
      fail(new PluginFrameError("loadError", "plugin_request_dedup_exhausted"));
      return;
    }
    seenIncomingRequests.add(request.request_id);
    routeHostRequest(request);
  };

  const onIncomingResponse = (data: Record<string, unknown>): void => {
    const required = [
      "protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce", "request_id", "ok",
    ];
    if (!exactShape(data, required, ["result", "error"])
      || data.type !== "plugin.rpc.response"
      || typeof data.request_id !== "string" || !REQUEST_ID.test(data.request_id)
      || typeof data.ok !== "boolean"
      || (data.ok && (!Object.hasOwn(data, "result") || data.error !== undefined || !safeJson(data.result)))
      || (!data.ok && (data.result !== undefined || !isRpcError(data.error)))) {
      fail(new PluginFrameError("loadError", "plugin_rpc_invalid_response"));
      return;
    }
    const requestId = data.request_id;
    if (seenResponses.has(requestId)) return;
    const entry = pending.get(requestId);
    if (!entry) return;
    pending.delete(requestId);
    seenResponses.add(requestId);
    clearTimeout(entry.timer);
    if (data.ok) {
      entry.resolve(data.result);
    } else {
      entry.reject(new PluginFrameError("loadError", (data.error as PluginRpcError).code));
    }
  };

  const onPluginError = (data: Record<string, unknown>): void => {
    const required = [
      "protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce", "error",
    ];
    if (!exactShape(data, required, ["request_id"]) || data.type !== "plugin.error" || !isRpcError(data.error)
      || (data.request_id !== undefined && (typeof data.request_id !== "string" || !REQUEST_ID.test(data.request_id)))) {
      fail(new PluginFrameError("loadError", "plugin_error_invalid"));
      return;
    }
    fail(pluginErrorFromCode(data.error.code));
  };

  const onPluginReady = (data: Record<string, unknown>): void => {
    const required = ["protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce"];
    if (!handshakeRequested || !hostReadySent || !exactShape(data, required) || data.type !== "plugin.ready") {
      fail(new PluginFrameError("identityMismatch", "plugin_ready_shape_invalid"));
      return;
    }
    if (!hasExpectedIdentity(data)) {
      const detail = data.nonce !== nonce ? "plugin_nonce_mismatch"
        : data.artifact_digest !== expectedDigest ? "plugin_digest_mismatch"
          : data.instance_id !== instanceId ? "plugin_instance_mismatch" : "plugin_ready_identity_mismatch";
      fail(new PluginFrameError("identityMismatch", detail));
      return;
    }
    if (handshakeTimer !== undefined) { clearTimeout(handshakeTimer); handshakeTimer = undefined; }
    const completedReady: PluginReadyMessage = {
      ...base(),
      type: "plugin.ready",
    };
    readyMessage = completedReady;
    const grant: GrantReadyMessage = {
      ...base(),
      type: "grant.ready",
      capabilities,
      scope,
      expires_at: grantExpiresAt,
    };
    if (!send(grant)) {
      fail(new PluginFrameError("loadError", "plugin_grant_send_failed"));
      return;
    }
    ready = true;
    grantTimer = setTimeout(() => {
      fail(new PluginFrameError("notGranted", "plugin_grant_expired"));
    }, Math.max(1, grantExpiry - now()));
    const resolve = handshakeResolve;
    handshakeResolve = undefined;
    handshakeReject = undefined;
    try { options.onReady?.(completedReady); } catch { /* Observers cannot affect isolation. */ }
    resolve?.(completedReady);
  };

  function onLoad(): void {
    if (disposed) return;
    if (loadSeen) {
      // WindowProxy survives navigation, so only a fresh load proves that the
      // old nonce/digest channel must be destroyed rather than reused.
      fail(new PluginFrameError("identityMismatch", "plugin_frame_reloaded"));
      return;
    }
    loadSeen = true;
    if (handshakeRequested) sendHostReady();
  }

  function sendHostReady(): void {
    if (disposed || hostReadySent || !loadSeen) return;
    hostReadySent = true;
    const message: HostReadyMessage = { ...base(), type: "host.ready" };
    if (!send(message)) fail(new PluginFrameError("loadError", "plugin_host_ready_send_failed"));
  }

  function onMessage(event: MessageEvent): void {
    if (disposed) return;
    if (event.source !== sourceWindow || iframe.contentWindow !== sourceWindow) {
      reject("source");
      return;
    }
    if (event.origin !== expectedOrigin) {
      // Foreign windows cannot kill the channel with a cross-origin DoS message.
      reject("origin");
      return;
    }
    if (!isRecord(event.data)) {
      reject("shape");
      return;
    }
    const data = event.data;
    const size = messageBytes(data);
    if (size > PLUGIN_RPC_MAX_MESSAGE_BYTES) {
      if (ready && hasExpectedIdentity(data) && data.type === "plugin.rpc.request"
        && typeof data.request_id === "string" && REQUEST_ID.test(data.request_id)) {
        sendFailure(data.request_id, "PAYLOAD_TOO_LARGE");
      } else {
        fail(new PluginFrameError("loadError", "plugin_message_too_large"));
      }
      return;
    }
    if (!safeJson(data)) {
      reject("shape");
      fail(new PluginFrameError("loadError", "plugin_json_shape_invalid"));
      return;
    }
    if (data.protocol !== PLUGIN_RPC_PROTOCOL) {
      fail(new PluginFrameError("unsupportedProtocol", "plugin_rpc_protocol_mismatch"));
      return;
    }
    if (!ready) {
      onPluginReady(data);
      return;
    }
    if (!hasExpectedIdentity(data)) {
      fail(new PluginFrameError("identityMismatch", "plugin_message_identity_mismatch"));
      return;
    }
    if (data.type === "plugin.rpc.request") {
      onIncomingRequest(data);
      return;
    }
    if (data.type === "plugin.rpc.response") {
      onIncomingResponse(data);
      return;
    }
    if (data.type === "plugin.error") {
      onPluginError(data);
      return;
    }
    // Includes repeated plugin.ready and host-only message types.
    fail(new PluginFrameError("identityMismatch", "plugin_message_type_invalid"));
  }

  hostWindow.addEventListener("message", onMessage);
  iframe.addEventListener("load", onLoad);

  function dispose(): void {
    close(new PluginFrameError("disposed", "plugin_channel_disposed"));
  }

  return {
    expectedOrigin,
    get ready() { return ready && !disposed; },
    get disposed() { return disposed; },
    handshake(): Promise<PluginReadyMessage> {
      if (disposed) return Promise.reject(new PluginFrameError("disposed", "plugin_channel_disposed"));
      if (ready && readyMessage) return Promise.resolve(readyMessage);
      if (handshakePromise) return handshakePromise;
      handshakePromise = new Promise<PluginReadyMessage>((resolve, rejectHandshake) => {
        handshakeResolve = resolve;
        handshakeReject = rejectHandshake;
        handshakeTimer = setTimeout(() => {
          handshakeTimer = undefined;
          if (!disposed) fail(new PluginFrameError("timeout", "plugin_handshake_timeout"));
        }, handshakeTimeoutMs);
      });
      void handshakePromise.catch(() => undefined);
      handshakeRequested = true;
      if (loadSeen) sendHostReady();
      return handshakePromise;
    },
    request<TResult = unknown>(
      method: PluginRpcMethod,
      input: JsonValue | { path: string } | { height: number } = null,
      requestOptions: { readonly capability?: CapabilityRef; readonly idempotencyKey?: string } = {},
    ): Promise<TResult> {
      if (disposed) return Promise.reject(new PluginFrameError("disposed", "plugin_channel_disposed"));
      if (!ready) return Promise.reject(new PluginFrameError("notReady", "plugin_channel_not_ready"));
      if (failIfGrantExpired()) return Promise.reject(new PluginFrameError("notGranted", "plugin_grant_expired"));
      const requestId = makeRequestId();
      if (!REQUEST_ID.test(requestId) || pending.has(requestId) || seenResponses.has(requestId)) {
        return Promise.reject(new PluginFrameError("loadError", "plugin_rpc_request_id_reused"));
      }
      if (seenResponses.size >= MAX_REQUEST_IDS) {
        return Promise.reject(new PluginFrameError("loadError", "plugin_response_dedup_exhausted"));
      }
      const draft: Record<string, unknown> = {
        ...base(),
        type: "plugin.rpc.request",
        request_id: requestId,
        method,
        context: scope,
        input,
        ...(requestOptions.capability ? { capability: requestOptions.capability } : {}),
        ...(method === "capability.call"
          ? { idempotency_key: requestOptions.idempotencyKey ?? requestId }
          : requestOptions.idempotencyKey ? { idempotency_key: requestOptions.idempotencyKey } : {}),
      };
      const validation = validateRequest(draft);
      if (!validation.ok) return Promise.reject(new PluginFrameError("loadError", validation.code));
      return new Promise<TResult>((resolve, rejectRequest) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          rejectRequest(new PluginFrameError("timeout", "plugin_rpc_request_timeout"));
        }, requestTimeoutMs);
        pending.set(requestId, { resolve: resolve as (value: unknown) => void, reject: rejectRequest, timer });
        if (!send(validation.request)) {
          clearTimeout(timer);
          pending.delete(requestId);
          rejectRequest(new PluginFrameError("loadError", "plugin_rpc_request_send_failed"));
        }
      });
    },
    dispose,
  };
}
