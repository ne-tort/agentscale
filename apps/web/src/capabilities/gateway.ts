import type { CapabilityName, JsonValue } from "../../../../packages/schemas/declarative-app/v1";
import { CapabilityDeniedError, constraintsAllow, requireCapability } from "./boundary";
import { capabilityContract, isKnownCapability } from "./contracts";
import { OriginPolicyError, resolvePeerOriginPolicy, type OriginPolicyMode } from "./origin-policy";
import { requireAuthoritativeScope, type ScopedContext } from "./scope";
import type { CapabilityBoundary, CapabilityGateway, CapabilityGatewayOptions, CapabilityGatewayTransport, GatewayInvokeOptions, GatewayNotifyOptions } from "./types";

let nextRequestId = 0;

/** The only methods that may use generic transport without an app capability. */
export const GENERIC_LIFECYCLE_CONTROL_METHODS = [
  "lifecycle.start",
  "lifecycle.stop",
  "lifecycle.restart",
  "control.ping",
  "control.shutdown",
] as const;
const genericMethods = new Set<string>(GENERIC_LIFECYCLE_CONTROL_METHODS);
const METHOD_ID = /^[a-z][a-z0-9._-]{0,127}$/;
const MAX_INBOUND_BYTES = 256 * 1024;

interface MethodGuardInput {
  readonly boundary: CapabilityBoundary | undefined;
  readonly method: string;
  readonly capability?: CapabilityName;
  /** Trusted method -> capability binding from a verified manifest/registry snapshot. */
  readonly binding?: (method: string) => CapabilityName | undefined;
  readonly scope?: () => ScopedContext | undefined;
  readonly enforceScope: boolean;
}

/**
 * Client-side call guard. It is intentionally redundant with the server: a capability claim from
 * the caller is never authorization, so an unknown name, a claim that contradicts the verified
 * binding, a missing effective grant, a method outside the constraint list, or an unverified
 * scope for an authoritative capability all fail before anything is sent.
 */
function guardMethod(input: MethodGuardInput): void {
  const { boundary, method, capability } = input;
  if (genericMethods.has(method)) {
    if (capability) throw new Error(`Generic lifecycle/control method must not declare capability: ${method}`);
    return;
  }
  if (!capability) throw new Error(`Capability is required for RPC method: ${method}`);
  if (!isKnownCapability(capability)) throw new CapabilityDeniedError(capability, "Unknown capability", "unknown_capability");
  if (input.binding) {
    const bound = input.binding(method);
    if (bound === undefined) throw new CapabilityDeniedError(capability, "RPC method has no verified capability binding", "binding_mismatch");
    if (bound !== capability) throw new CapabilityDeniedError(capability, `Capability claim does not match the verified method binding (${bound})`, "binding_mismatch");
  }
  requireCapability(boundary, capability);
  const contract = capabilityContract(capability);
  if (contract?.constraints.includes("methods") && !constraintsAllow(boundary, capability, "methods", method)) {
    throw new Error(`RPC method is not allowed by capability constraints: ${method}`);
  }
  if (input.enforceScope && contract && contract.authoritativeScope.length > 0) {
    requireAuthoritativeScope(input.scope?.(), contract.authoritativeScope);
  }
}

export function createCapabilityGateway(
  transport: CapabilityGatewayTransport,
  getBoundary: () => CapabilityBoundary | undefined,
  options: CapabilityGatewayOptions = {},
): CapabilityGateway {
  const enforceScope = typeof options.scope === "function";
  const guard = (method: string, capability?: CapabilityName): void =>
    guardMethod({ boundary: getBoundary(), method, capability, binding: options.methodBinding, scope: options.scope, enforceScope });
  return {
    invoke: async <TResult extends JsonValue = JsonValue, TParams extends JsonValue = JsonValue>(method: string, params?: TParams, invocation?: GatewayInvokeOptions) => {
      if (invocation?.signal?.aborted) throw new DOMException("The request was aborted", "AbortError");
      guard(method, invocation?.capability);
      return transport.request<TResult, TParams>({ method, params, deadlineAt: invocation?.deadlineAt, signal: invocation?.signal });
    },
    notify: <TParams extends JsonValue = JsonValue>(method: string, params?: TParams, invocation?: GatewayNotifyOptions) => {
      guard(method, invocation?.capability);
      transport.notify({ method, params });
    },
  };
}

export interface HostMessageTransportOptions {
  readonly target?: Window;
  /** Exact origin of the peer (plugin/app frame). Required: there is no wildcard default. */
  readonly peerOrigin: string;
  /** Exact `WindowProxy` of the peer; when supplied, messages from other windows are refused. */
  readonly source?: Window | null;
  /** Defaults to `production`. Preview uses its own isolated allow-list. */
  readonly mode?: OriginPolicyMode;
  readonly allowedOrigins?: ReadonlySet<string> | readonly string[];
  readonly previewOrigins?: ReadonlySet<string> | readonly string[];
  readonly shellOrigin?: string;
  /** Preview only: accept the opaque `"null"` origin of a sandboxed frame. */
  readonly allowOpaqueOrigin?: boolean;
  readonly onRejectedMessage?: (reason: "origin" | "source" | "protocol" | "oversized") => void;
}

type PendingRequest = {
  readonly resolve: (value: JsonValue) => void;
  readonly reject: (reason: Error) => void;
};

function isWindowLike(value: unknown): value is Window {
  return Boolean(value && typeof value === "object" && typeof (value as Window).postMessage === "function" && typeof (value as Window).addEventListener === "function");
}

/**
 * Host-side transport. Only the exact registered origin (and, when supplied, the exact frame
 * `WindowProxy`) is accepted, and every outbound message is addressed to that exact origin.
 */
export function createHostMessageTransport(options: HostMessageTransportOptions): CapabilityGatewayTransport;
export function createHostMessageTransport(target: Window, peerOrigin: string): CapabilityGatewayTransport;
export function createHostMessageTransport(targetOrOptions?: Window | HostMessageTransportOptions, peerOrigin?: string): CapabilityGatewayTransport {
  const options: HostMessageTransportOptions = isWindowLike(targetOrOptions) || targetOrOptions === undefined
    ? { target: targetOrOptions as Window | undefined, peerOrigin: peerOrigin ?? "" }
    : targetOrOptions;
  if (typeof options.peerOrigin !== "string" || !options.peerOrigin) throw new OriginPolicyError("origin_required");
  const policy = resolvePeerOriginPolicy({
    peerOrigin: options.peerOrigin,
    mode: options.mode,
    allowedOrigins: options.allowedOrigins ?? [options.peerOrigin],
    previewOrigins: options.previewOrigins,
    shellOrigin: options.shellOrigin,
    allowOpaqueOrigin: options.allowOpaqueOrigin,
  });
  const target = options.target ?? (typeof window === "undefined" ? undefined : window);
  if (!target) throw new OriginPolicyError("origin_required", "no host window");

  const pending = new Map<string, PendingRequest>();
  const onMessage = (event: MessageEvent) => {
    if (!policy.accepts(event.origin)) { options.onRejectedMessage?.("origin"); return; }
    if (options.source && event.source !== options.source) { options.onRejectedMessage?.("source"); return; }
    const message = event.data as { protocol?: string; type?: string; id?: string; result?: JsonValue; error?: { message?: string } };
    if (!message || message.protocol !== "1.0" || (message.type !== "response" && message.type !== "error")) { options.onRejectedMessage?.("protocol"); return; }
    if (message.error !== undefined && (!message.error || typeof message.error !== "object" || typeof message.error.message !== "string" || message.error.message.length > 512)) { options.onRejectedMessage?.("protocol"); return; }
    let bytes: number;
    try { bytes = new TextEncoder().encode(JSON.stringify(message)).byteLength; } catch { options.onRejectedMessage?.("oversized"); return; }
    if (bytes > MAX_INBOUND_BYTES) { options.onRejectedMessage?.("oversized"); return; }
    const request = typeof message.id === "string" ? pending.get(message.id) : undefined;
    if (!request) return;
    pending.delete(message.id!);
    if (message.type === "error") request.reject(new Error(message.error?.message ?? "Capability gateway request failed"));
    else request.resolve(message.result as JsonValue);
  };
  target.addEventListener("message", onMessage);
  return {
    request: <TResult extends JsonValue = JsonValue>({ method, params, deadlineAt, signal }: { method: string; params?: JsonValue; deadlineAt?: string; signal?: AbortSignal }) => new Promise<JsonValue>((resolve, reject) => {
      const id = `cap-${++nextRequestId}`;
      if (signal?.aborted) {
        reject(new DOMException("The request was aborted", "AbortError"));
        return;
      }
      const abort = () => {
        pending.delete(id);
        signal?.removeEventListener("abort", abort);
        reject(new DOMException("The request was aborted", "AbortError"));
      };
      pending.set(id, {
        resolve: (value) => { signal?.removeEventListener("abort", abort); resolve(value); },
        reject: (reason) => { signal?.removeEventListener("abort", abort); reject(reason); },
      });
      signal?.addEventListener("abort", abort, { once: true });
      try {
        target.postMessage({ protocol: "1.0", type: "request", id, method, params, deadlineAt }, policy.targetOrigin);
      } catch (error) {
        pending.delete(id);
        signal?.removeEventListener("abort", abort);
        reject(error instanceof Error ? error : new Error("capability_transport_failed"));
      }
    }).then((result) => result as TResult),
    notify: ({ method, params }) => target.postMessage({ protocol: "1.0", type: "notification", method, params }, policy.targetOrigin),
  };
}

/** Binds a method to the capability that must be declared for it. Validated: no unknown names. */
export function capabilityMethod(capability: CapabilityName, method: string): { method: string; capability: CapabilityName } {
  if (!isKnownCapability(capability)) throw new CapabilityDeniedError(capability, "Unknown capability", "unknown_capability");
  if (typeof method !== "string" || !METHOD_ID.test(method)) throw new TypeError(`Invalid RPC method: ${String(method)}`);
  return { method, capability };
}

/** Builds a verified-binding resolver from an already validated manifest method list. */
export function methodBindingsFrom(entries: Iterable<{ readonly method: string; readonly capability: CapabilityName }>): Map<string, CapabilityName> {
  const bindings = new Map<string, CapabilityName>();
  for (const entry of entries) {
    if (!isKnownCapability(entry.capability) || typeof entry.method !== "string" || !METHOD_ID.test(entry.method)) continue;
    if (bindings.has(entry.method)) throw new Error(`Duplicate capability binding for method: ${entry.method}`);
    bindings.set(entry.method, entry.capability);
  }
  return bindings;
}
