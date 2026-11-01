import type { CapabilityName, JsonValue } from "../../../../packages/schemas/declarative-app/v1";
import { requireCapability, constraintsAllow } from "./boundary";
import type { CapabilityBoundary, CapabilityGateway, CapabilityGatewayTransport, GatewayInvokeOptions, GatewayNotifyOptions } from "./types";

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

function guardMethod(boundary: CapabilityBoundary | undefined, method: string, capability?: CapabilityName): void {
  if (genericMethods.has(method)) {
    if (capability) throw new Error(`Generic lifecycle/control method must not declare capability: ${method}`);
    return;
  }
  if (!capability) throw new Error(`Capability is required for RPC method: ${method}`);
  requireCapability(boundary, capability);
  if (!constraintsAllow(boundary, capability, "methods", method)) {
    throw new Error(`RPC method is not allowed by capability constraints: ${method}`);
  }
}

export function createCapabilityGateway(transport: CapabilityGatewayTransport, getBoundary: () => CapabilityBoundary | undefined): CapabilityGateway {
  return {
    invoke: async <TResult extends JsonValue = JsonValue, TParams extends JsonValue = JsonValue>(method: string, params?: TParams, options?: GatewayInvokeOptions) => {
      guardMethod(getBoundary(), method, options?.capability);
      return transport.request<TResult, TParams>({ method, params, deadlineAt: options?.deadlineAt, signal: options?.signal });
    },
    notify: <TParams extends JsonValue = JsonValue>(method: string, params?: TParams, options?: GatewayNotifyOptions) => {
      guardMethod(getBoundary(), method, options?.capability);
      transport.notify({ method, params });
    },
  };
}

export function createHostMessageTransport(target: Window = window, origin = "*"): CapabilityGatewayTransport {
  const pending = new Map<string, { resolve: (value: JsonValue) => void; reject: (reason: Error) => void }>();
  const onMessage = (event: MessageEvent) => {
    if (origin !== "*" && event.origin !== origin) return;
    const message = event.data as { protocol?: string; type?: string; id?: string; result?: JsonValue; error?: { message?: string } };
    if (message?.protocol !== "1.0" || (message.type !== "response" && message.type !== "error")) return;
    const request = message.id ? pending.get(message.id) : undefined;
    if (!request) return;
    pending.delete(message.id!);
    if (message.type === "error") request.reject(new Error(message.error?.message ?? "Capability gateway request failed"));
    else request.resolve(message.result);
  };
  target.addEventListener("message", onMessage);
  return {
    request: <TResult extends JsonValue = JsonValue>({ method, params, deadlineAt, signal }) => new Promise<JsonValue>((resolve, reject) => {
      const id = `cap-${++nextRequestId}`;
      if (signal?.aborted) {
        reject(new DOMException("The request was aborted", "AbortError"));
        return;
      }
      pending.set(id, { resolve, reject });
      const abort = () => { pending.delete(id); reject(new DOMException("The request was aborted", "AbortError")); };
      signal?.addEventListener("abort", abort, { once: true });
      target.postMessage({ protocol: "1.0", type: "request", id, method, params, deadlineAt }, origin);
    }).then((result) => result as TResult),
    notify: ({ method, params }) => target.postMessage({ protocol: "1.0", type: "notification", method, params }, origin),
  };
}

export function capabilityMethod(capability: CapabilityName, method: string): { method: string; capability: CapabilityName } {
  return { method, capability };
}
