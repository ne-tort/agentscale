import type {
  CapabilityName,
  CapabilityGrant,
  CapabilityRequest,
  DeclarativeAppManifest,
  GrantSet,
  JsonValue,
} from "../../../../packages/schemas/declarative-app/v1";
import type { CapabilityDenialCode } from "./boundary";
import type { ScopedContext } from "./scope";

export type { CapabilityName, DeclarativeAppManifest, GrantSet, JsonValue };

export interface EffectiveCapability {
  name: CapabilityName;
  constraints?: CapabilityGrant["constraints"];
  source: "platform" | "administrator" | "user";
  expiresAt?: string;
  revokedAt?: string;
}

export interface CapabilityBoundary {
  appId: string;
  manifestVersion: string;
  requested: readonly CapabilityRequest[];
  effective: ReadonlyMap<CapabilityName, EffectiveCapability>;
  /** Why a requested capability has no effective grant; diagnostics only, never an allow. */
  denied?: ReadonlyMap<CapabilityName, CapabilityDenialCode>;
}

export interface CapabilityGateway {
  invoke<TResult extends JsonValue = JsonValue, TParams extends JsonValue = JsonValue>(
    method: string,
    params?: TParams,
    options?: GatewayInvokeOptions,
  ): Promise<TResult>;
  notify<TParams extends JsonValue = JsonValue>(method: string, params?: TParams, options?: GatewayNotifyOptions): void;
}

export interface GatewayInvokeOptions {
  capability?: CapabilityName;
  signal?: AbortSignal;
  deadlineAt?: string;
}

export interface GatewayNotifyOptions {
  capability?: CapabilityName;
}

/**
 * Wiring for the gateway guard.
 *
 * `methodBinding` is the trusted method -> capability mapping from a verified manifest/registry
 * snapshot. When present, a caller-supplied `capability` is only an assertion that must match it.
 *
 * `scope` is the server-proven scope for this instance. When present, capabilities whose contract
 * requires authoritative scope fail on an unverified context. Production wiring must pass it;
 * omitting it keeps the legacy (unscoped) behaviour for callers that predate the scope gate.
 */
export interface CapabilityGatewayOptions {
  readonly methodBinding?: (method: string) => CapabilityName | undefined;
  readonly scope?: () => ScopedContext | undefined;
}

export interface CapabilityGatewayTransport {
  request<TResult extends JsonValue = JsonValue, TParams extends JsonValue = JsonValue>(
    message: { method: string; params?: TParams; deadlineAt?: string; signal?: AbortSignal },
  ): Promise<TResult>;
  notify<TParams extends JsonValue = JsonValue>(message: { method: string; params?: TParams }): void;
}

export interface CapabilityProviderValue {
  manifest?: DeclarativeAppManifest;
  grants?: GrantSet;
  boundary?: CapabilityBoundary;
  gateway: CapabilityGateway;
  /** Scope used by the gateway guard; `unverified` until the server bridge attests it. */
  scope?: ScopedContext;
  reloadManifest(): Promise<DeclarativeAppManifest>;
}
