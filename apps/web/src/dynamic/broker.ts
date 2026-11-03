import type { JsonValue } from "../../../../packages/schemas/declarative-app/v1";
import { isVerifiedScope, scopeMismatch, type ScopeInput, type ScopedContext } from "../capabilities/scope";

export const BROKER_PROTOCOL = "plugin-rpc/1" as const;
export type BrokerCapability = "ui.navigation" | "storage.kv" | "storage.files" | "events.subscribe" | "platform.commands" | "platform.queries";
export interface BrokerCall {
  readonly capability: BrokerCapability;
  readonly method: string;
  readonly input: JsonValue;
  readonly signal: AbortSignal;
  /**
   * Server-proven scope for this instance. `undefined` means no bridge attestation exists yet;
   * adapters must not substitute the plugin-supplied claim for it.
   */
  readonly context?: ScopedContext;
}
export type BrokerAdapter = (call: BrokerCall) => Promise<unknown>;
export interface BrokerPolicy {
  readonly allowedCapabilities: ReadonlySet<string>;
  readonly methods: ReadonlyMap<string, BrokerCapability>;
  readonly adapter: BrokerAdapter;
  readonly timeoutMs?: number;
  readonly isKilled?: () => boolean;
  /** Scope re-resolved by the server bridge for this plugin instance. */
  readonly scope?: ScopedContext;
}

const CAPABILITIES = new Set<BrokerCapability>(["ui.navigation", "storage.kv", "storage.files", "events.subscribe", "platform.commands", "platform.queries"]);
/** Capabilities that may only run against a server-verified scope. */
export const AUTHORITATIVE_BROKER_CAPABILITIES: ReadonlySet<BrokerCapability> = new Set<BrokerCapability>(["platform.commands", "storage.files"]);
/** Capabilities a preview instance may expose; mutations and secret-bearing scopes are excluded. */
export const PREVIEW_BROKER_CAPABILITIES: ReadonlySet<BrokerCapability> = new Set<BrokerCapability>(["ui.navigation", "storage.kv", "events.subscribe", "platform.queries"]);
const DENIED_METHODS = /(?:fetch|network|sql|redis|kafka|mongo|opensearch|kubernetes|shell|cookie|token|secret)/i;
const RESERVED = new Set(["__proto__", "prototype", "constructor"]);
const MAX_MESSAGE_BYTES = 64 * 1024;
const MAX_IN_FLIGHT = 16;

function isJson(value: unknown, seen = new Set<object>(), depth = 0, budget = { nodes: 0 }): value is JsonValue {
  budget.nodes += 1;
  if (budget.nodes > 2_000 || depth > 12) return false;
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) return value.length <= 2_000 && value.every((item) => isJson(item, seen, depth + 1, budget));
  const proto = Object.getPrototypeOf(value);
  const valid = (proto === Object.prototype || proto === null) && Object.entries(value).length <= 2_000
    && Object.entries(value).every(([key, item]) => !RESERVED.has(key) && key.length <= 256 && typeof item !== "undefined" && isJson(item, seen, depth + 1, budget));
  seen.delete(value);
  return valid;
}
function byteLength(value: unknown): number {
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength; } catch { return MAX_MESSAGE_BYTES + 1; }
}

export function createCapabilityBroker(policy: BrokerPolicy) {
  let killed = false;
  const active = new Set<AbortController>();
  const timeoutMs = Math.max(1, Math.min(policy.timeoutMs ?? 10_000, 60_000));
  return {
    /**
     * `claim` is the scope the plugin *asserts* in its RPC envelope. It is compared against the
     * proven scope and can only narrow or deny a call; it never becomes the call context.
     */
    async invoke(method: string, input: unknown, signal?: AbortSignal, claim?: ScopeInput): Promise<JsonValue> {
      if (killed || policy.isKilled?.()) throw new Error("BROKER_KILLED");
      const capability = policy.methods.get(method);
      if (!capability || !CAPABILITIES.has(capability) || DENIED_METHODS.test(method) || !policy.allowedCapabilities.has(capability)) throw new Error("CAPABILITY_DENIED");
      if (AUTHORITATIVE_BROKER_CAPABILITIES.has(capability) && !isVerifiedScope(policy.scope)) throw new Error("SCOPE_UNVERIFIED");
      if (claim !== undefined && scopeMismatch(claim, policy.scope).length) throw new Error("CONTEXT_MISMATCH");
      if (!isJson(input) || byteLength(input) > MAX_MESSAGE_BYTES) throw new Error("INVALID_INPUT");
      if (active.size >= MAX_IN_FLIGHT) throw new Error("REQUEST_LIMIT");
      const controller = new AbortController();
      active.add(controller);
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(abort, timeoutMs);
      try {
        if (signal?.aborted) controller.abort();
        const result = await policy.adapter({ capability, method, input, signal: controller.signal, ...(isVerifiedScope(policy.scope) ? { context: policy.scope } : {}) });
        if (controller.signal.aborted) throw new Error("REQUEST_CANCELLED_OR_TIMED_OUT");
        if (!isJson(result) || byteLength(result) > MAX_MESSAGE_BYTES) throw new Error("INVALID_RESPONSE");
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
