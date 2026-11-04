import { capabilityMethod } from "../capabilities/gateway";
import type { CapabilityGateway } from "../capabilities/types";
import type { JsonValue } from "../../../../packages/schemas/declarative-app/v1";
import type {
  DynamicAppCapabilityContext,
  DynamicAppDetail,
  DynamicAppRegistryContract,
  DynamicAppRegistryPage,
  DynamicAppRegistryQuery,
  GrantCapabilityInput,
  PublishDynamicAppInput,
  RevokeCapabilityInput,
} from "./contracts";

export const DYNAMIC_APP_METHODS = {
  list: "dynamic-app.registry.list",
  detail: "dynamic-app.registry.detail",
  publish: "dynamic-app.registry.publish",
  grant: "dynamic-app.registry.grant",
  revoke: "dynamic-app.registry.revoke",
} as const;

export interface DynamicAppCapabilityPolicy {
  queryCapability?: "platform.queries";
  commandCapability?: "platform.commands";
}

const QUERY_METHODS = new Set<string>([DYNAMIC_APP_METHODS.list, DYNAMIC_APP_METHODS.detail]);
const COMMAND_METHODS = new Set<string>([DYNAMIC_APP_METHODS.publish, DYNAMIC_APP_METHODS.grant, DYNAMIC_APP_METHODS.revoke]);

function requireNonEmpty(value: string, field: string): void {
  if (typeof value !== "string" || value.length === 0) throw new Error(`Invalid ${field}`);
}

/** Why the payloads below need this check: registry inputs are `interface`s, which carry no index
 * signature and therefore are not structurally assignable to the protocol's `JsonValue`, even
 * though every field is JSON data. Instead of asserting the conversion away, the payload is
 * verified as plain JSON where it crosses into the capability gateway, so a class instance,
 * function member, `undefined` field or cycle fails closed here rather than producing an RPC
 * message the host cannot serialise. */
function isJsonPayload(value: unknown, seen = new Set<object>(), depth = 0): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || depth > 16 || seen.has(value)) return false;
  seen.add(value);
  let valid: boolean;
  if (Array.isArray(value)) {
    valid = value.every((item) => isJsonPayload(item, seen, depth + 1));
  } else {
    const prototype = Object.getPrototypeOf(value);
    valid = (prototype === Object.prototype || prototype === null)
      && Object.values(value as Record<string, unknown>).every((item) => item !== undefined && isJsonPayload(item, seen, depth + 1));
  }
  seen.delete(value);
  return valid;
}

function jsonPayload(value: object, field: string): JsonValue {
  if (!isJsonPayload(value)) throw new Error(`Invalid ${field}`);
  return value;
}

function assertBoundApp(context: DynamicAppCapabilityContext, appId: string, manifestVersion?: string): void {
  requireNonEmpty(appId, "appId");
  if (!/^[a-z][a-z0-9-]{2,63}$/.test(appId)) throw new Error("Invalid appId");
  if (appId !== context.appId) throw new Error("Dynamic app scope does not match capability context");
  requireNonEmpty(context.manifestVersion, "manifestVersion");
  if (manifestVersion !== undefined && manifestVersion !== context.manifestVersion) {
    throw new Error("Dynamic app version does not match capability context");
  }
}

function validateQuery(query: DynamicAppRegistryQuery): DynamicAppRegistryQuery {
  const allowed = ["search", "publication", "lifecycle", "availability", "cursor", "limit"] as const;
  if (Object.keys(query).some((key) => !allowed.includes(key as (typeof allowed)[number]))) throw new Error("Invalid registry query");
  if (query.search !== undefined && typeof query.search !== "string") throw new Error("Invalid registry search");
  if (query.cursor !== undefined && typeof query.cursor !== "string") throw new Error("Invalid registry cursor");
  if (query.limit !== undefined && (!Number.isInteger(query.limit) || query.limit < 1 || query.limit > 100)) {
    throw new Error("Invalid registry limit");
  }
  if (query.publication !== undefined && !["draft", "published", "withdrawn"].includes(query.publication)) throw new Error("Invalid registry publication");
  if (query.lifecycle !== undefined && !["registered", "starting", "running", "stopping", "stopped", "failed", "suspended"].includes(query.lifecycle)) throw new Error("Invalid registry lifecycle");
  if (query.availability !== undefined && !["available", "unavailable", "degraded"].includes(query.availability)) throw new Error("Invalid registry availability");
  return {
    ...(query.search === undefined ? {} : { search: query.search }),
    ...(query.publication === undefined ? {} : { publication: query.publication }),
    ...(query.lifecycle === undefined ? {} : { lifecycle: query.lifecycle }),
    ...(query.availability === undefined ? {} : { availability: query.availability }),
    ...(query.cursor === undefined ? {} : { cursor: query.cursor }),
    ...(query.limit === undefined ? {} : { limit: query.limit }),
  };
}

export function createDynamicAppRegistryContract(
  context: DynamicAppCapabilityContext,
  policy: DynamicAppCapabilityPolicy = {},
): DynamicAppRegistryContract {
  const queryCapability = policy.queryCapability ?? "platform.queries";
  const commandCapability = policy.commandCapability ?? "platform.commands";
  const invoke = <TResult>(method: string, params: object, options?: { signal?: AbortSignal }) => {
    const capability = QUERY_METHODS.has(method) ? queryCapability : COMMAND_METHODS.has(method) ? commandCapability : undefined;
    if (!capability) throw new Error(`Unsupported dynamic app registry method: ${method}`);
    // The registry result types (page/detail) are interfaces too, so they are not structurally
    // `JsonValue` either; the gateway answers with the validated JSON payload and the declared
    // `TResult` is asserted here, at the single boundary that decodes it.
    return context.gateway.invoke<JsonValue>(method, jsonPayload(params, "registry params"), { ...capabilityMethod(capability, method), signal: options?.signal }) as Promise<TResult>;
  };

  return {
    list: (query: DynamicAppRegistryQuery = {}, options) => invoke<DynamicAppRegistryPage>(DYNAMIC_APP_METHODS.list, validateQuery(query), options),
    detail: (appId, options) => { assertBoundApp(context, appId); return invoke<DynamicAppDetail>(DYNAMIC_APP_METHODS.detail, { appId }, options); },
    publish: (input: PublishDynamicAppInput, options) => {
      if (input.manifest.protocol !== "1.0") throw new Error("Unsupported declarative app protocol");
      assertBoundApp(context, input.manifest.appId, input.manifest.version);
      return invoke<DynamicAppDetail>(DYNAMIC_APP_METHODS.publish, input, options);
    },
    grant: (input: GrantCapabilityInput, options) => {
      assertBoundApp(context, input.appId, input.manifestVersion);
      return invoke<DynamicAppDetail>(DYNAMIC_APP_METHODS.grant, input, options);
    },
    revoke: (input: RevokeCapabilityInput, options) => {
      assertBoundApp(context, input.appId, input.manifestVersion);
      return invoke<DynamicAppDetail>(DYNAMIC_APP_METHODS.revoke, input, options);
    },
  };
}
