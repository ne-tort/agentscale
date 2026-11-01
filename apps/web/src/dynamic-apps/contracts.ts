import type {
  AppId,
  AppLifecycleEvent,
  AppLifecycleState,
  CapabilityGrant,
  CapabilityRequest,
  DeclarativeAppManifest,
  GrantSet,
} from "../../../packages/schemas/declarative-app/v1";
import type { CapabilityGateway } from "../capabilities/types";

export type { AppId, AppLifecycleEvent, AppLifecycleState, CapabilityGrant, CapabilityRequest, DeclarativeAppManifest, GrantSet };

export type DynamicAppAvailability = "available" | "unavailable" | "degraded";
export type DynamicAppPublication = "draft" | "published" | "withdrawn";

export interface DynamicAppSummary {
  appId: AppId;
  manifestVersion: string;
  displayName: string;
  description?: string;
  publication: DynamicAppPublication;
  lifecycle: AppLifecycleState;
  availability: DynamicAppAvailability;
  requestedCapabilityCount: number;
  updatedAt: string;
}

export interface DynamicAppDetail extends DynamicAppSummary {
  manifest: DeclarativeAppManifest;
  grants?: GrantSet;
  lifecycleEvents: readonly AppLifecycleEvent[];
}

export interface DynamicAppRegistryQuery {
  search?: string;
  publication?: DynamicAppPublication;
  lifecycle?: AppLifecycleState;
  availability?: DynamicAppAvailability;
  cursor?: string;
  limit?: number;
}

export interface DynamicAppRegistryPage {
  items: readonly DynamicAppSummary[];
  nextCursor?: string;
  total?: number;
}

export interface PublishDynamicAppInput {
  manifest: DeclarativeAppManifest;
  expectedVersion?: string;
}

export interface GrantCapabilityInput {
  appId: AppId;
  manifestVersion: string;
  capability: CapabilityGrant;
}

export interface RevokeCapabilityInput {
  appId: AppId;
  manifestVersion: string;
  capability: CapabilityGrant["name"];
  reason?: string;
}

export interface DynamicAppRegistryContract {
  list(query?: DynamicAppRegistryQuery, options?: { signal?: AbortSignal }): Promise<DynamicAppRegistryPage>;
  detail(appId: AppId, options?: { signal?: AbortSignal }): Promise<DynamicAppDetail>;
  publish(input: PublishDynamicAppInput, options?: { signal?: AbortSignal }): Promise<DynamicAppDetail>;
  grant(input: GrantCapabilityInput, options?: { signal?: AbortSignal }): Promise<DynamicAppDetail>;
  revoke(input: RevokeCapabilityInput, options?: { signal?: AbortSignal }): Promise<DynamicAppDetail>;
}

export interface DynamicAppCapabilityContext {
  gateway: CapabilityGateway;
  appId: AppId;
  manifestVersion: string;
}

export type DynamicAppAction =
  | { type: "publish"; input: PublishDynamicAppInput }
  | { type: "grant"; input: GrantCapabilityInput }
  | { type: "revoke"; input: RevokeCapabilityInput };
