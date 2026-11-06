import { RESOURCE_CONTRACT_BLOCKERS } from "./blockers";
import type {
  AgentProfile,
  BindResourceInput,
  CreateResourceInput,
  CreateVersionInput,
  MaterializationPlan,
  PlanMaterializationInput,
  ResourceBinding,
  ResourceDiff,
  ResourceDiffInput,
  ResourceRecord,
  ResourceVersion,
  ResourceWorkspaceQuery,
  ResourceWorkspaceSnapshot,
  SyncMaterializationInput,
  UpdateProfileInput,
} from "./types";

export interface ResourceServiceCapabilities {
  readWorkspace: boolean;
  readDiff: boolean;
  updateProfile: boolean;
  createResource: boolean;
  createVersion: boolean;
  bindResource: boolean;
  planMaterialization: boolean;
  syncMaterialization: boolean;
}

export const BLOCKED_RESOURCE_SERVICE_CAPABILITIES: ResourceServiceCapabilities = {
  readWorkspace: false,
  readDiff: false,
  updateProfile: false,
  createResource: false,
  createVersion: false,
  bindResource: false,
  planMaterialization: false,
  syncMaterialization: false,
};

export interface ResourceService {
  readonly capabilities: ResourceServiceCapabilities;
  loadWorkspace(query: ResourceWorkspaceQuery, options?: { signal?: AbortSignal }): Promise<ResourceWorkspaceSnapshot>;
  loadDiff(input: ResourceDiffInput, options?: { signal?: AbortSignal }): Promise<ResourceDiff>;
  updateProfile(input: UpdateProfileInput, options?: { signal?: AbortSignal }): Promise<AgentProfile>;
  createResource(input: CreateResourceInput, options?: { signal?: AbortSignal }): Promise<ResourceRecord>;
  createVersion(input: CreateVersionInput, options?: { signal?: AbortSignal }): Promise<ResourceVersion>;
  bindResource(input: BindResourceInput, options?: { signal?: AbortSignal }): Promise<ResourceBinding>;
  planMaterialization(input: PlanMaterializationInput, options?: { signal?: AbortSignal }): Promise<MaterializationPlan>;
  syncMaterialization(input: SyncMaterializationInput, options?: { signal?: AbortSignal }): Promise<MaterializationPlan>;
}

export class ResourceContractBlockedError extends Error {
  readonly blockers = RESOURCE_CONTRACT_BLOCKERS;

  constructor(readonly operation: string) {
    super(`${operation} is blocked until the resource contract is available`);
    this.name = "ResourceContractBlockedError";
  }
}

function blocked<T>(operation: string): Promise<T> {
  return Promise.reject(new ResourceContractBlockedError(operation));
}

/**
 * Safe default used until an API adapter is explicitly wired. It never makes
 * a request and never reports a fabricated resource or materialization result.
 */
export function createBlockedResourceService(): ResourceService {
  return {
    capabilities: { ...BLOCKED_RESOURCE_SERVICE_CAPABILITIES },
    loadWorkspace: () => blocked("Loading resources"),
    loadDiff: () => blocked("Comparing resource versions"),
    updateProfile: () => blocked("Updating agent profile"),
    createResource: () => blocked("Creating resource"),
    createVersion: () => blocked("Creating resource version"),
    bindResource: () => blocked("Binding resource to project"),
    planMaterialization: () => blocked("Planning materialization"),
    syncMaterialization: () => blocked("Syncing materialization"),
  };
}
