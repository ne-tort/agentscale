import type { JsonValue } from "../../../../packages/schemas/declarative-app/v1";

export const BROKER_PROTOCOL = "plugin-rpc/1" as const;
export type BrokerCapability = "ui.navigation" | "storage.kv" | "storage.files" | "events.subscribe" | "platform.commands" | "platform.queries";
export interface BrokerCall {
  readonly capability: BrokerCapability;
  readonly method: string;
  readonly input: JsonValue;
  readonly signal: AbortSignal;
}
export type BrokerAdapter = (call: BrokerCall) => Promise<unknown>;
export interface BrokerPolicy {
  readonly allowedCapabilities: ReadonlySet<string>;
  readonly methods: ReadonlyMap<string, BrokerCapability>;
  readonly adapter: BrokerAdapter;
  readonly timeoutMs?: number;
  readonly isKilled?: () => boolean;
}

const CAPABILITIES = new Set<BrokerCapability>(["ui.navigation", "storage.kv", "storage.files", "events.subscribe", "platform.commands", "platform.queries"]);
const DENIED_METHODS = /(?:fetch|network|sql|redis|kafka|mongo|opensearch|kubernetes|shell|cookie|token|secret)/i;
const RESERVED = new Set(["__proto__", "prototype", "constructor"]);

function isJson(value: unknown, seen = new Set<object>()): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) return value.every((item) => isJson(item, seen));
  const proto = Object.getPrototypeOf(value);
  return (proto === Object.prototype || proto === null) && Object.entries(value).every(([key, item]) => !RESERVED.has(key) && isJson(item, seen));
}

export function createCapabilityBroker(policy: BrokerPolicy) {
  let killed = false;
  const active = new Set<AbortController>();
  const timeoutMs = Math.max(1, Math.min(policy.timeoutMs ?? 10_000, 60_000));
  return {
    async invoke(method: string, input: unknown, signal?: AbortSignal): Promise<JsonValue> {
      if (killed || policy.isKilled?.()) throw new Error("BROKER_KILLED");
      const capability = policy.methods.get(method);
      if (!capability || !CAPABILITIES.has(capability) || DENIED_METHODS.test(method) || !policy.allowedCapabilities.has(capability)) throw new Error("CAPABILITY_DENIED");
      if (!isJson(input)) throw new Error("INVALID_INPUT");
      const controller = new AbortController();
      active.add(controller);
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(abort, timeoutMs);
      try {
        if (signal?.aborted) controller.abort();
        const result = await policy.adapter({ capability, method, input, signal: controller.signal });
        if (controller.signal.aborted) throw new Error("REQUEST_CANCELLED_OR_TIMED_OUT");
        if (!isJson(result)) throw new Error("INVALID_RESPONSE");
        return result;
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        active.delete(controller);
      }
    },
    kill(): void {
      killed = true;
      for (const controller of active) controller.abort();
      active.clear();
    },
    get isKilled(): boolean { return killed || Boolean(policy.isKilled?.()); },
  };
}
