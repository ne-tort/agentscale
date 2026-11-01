import type {
  CapabilityName,
  CapabilityGrant,
  CapabilityRequest,
  DeclarativeAppManifest,
  GrantSet,
  JsonValue,
} from "../../../../packages/schemas/declarative-app/v1";

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
  reloadManifest(): Promise<DeclarativeAppManifest>;
}
