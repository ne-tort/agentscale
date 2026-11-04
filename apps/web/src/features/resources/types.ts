export type ResourceId = string;
export type ResourceKind = "prompt" | "file" | "mcp";
export type ResourceScopeKind = "platform" | "company" | "cabinet" | "project" | "session";
/**
 * `ResourceLifecycle` as the canonical API emits it (`lifecycle_contracts.ResourceLifecycle`).
 *
 * `active` is the serving state here, not `published`: the API keeps resource
 * lifecycle separate from *version* status, where `published` is the serving value.
 * Using the version vocabulary made every live resource render as neither active nor
 * published, and invented a `blocked` state the resource model does not have.
 */
export type ResourceLifecycle = "draft" | "active" | "archived" | "quarantined";
export type ResourceVersionStatus = "draft" | "published" | "retired";
export type ResourceBindingMode = "local" | "global";
/**
 * The plan API answers `ready` or `blocked` (`StoredPlan.status` in
 * `application/resources/api/materialization.py`). `applied`/`failed`/`stale` are
 * *apply* outcomes and belong to the apply response, not to a plan.
 */
export type MaterializationStatus = "ready" | "blocked";
export type MaterializationEntryStatus = "planned";
export type MaterializationOutcome = "applied" | "replayed" | "conflict" | "blocked";

export interface ResourceScope {
  kind: ResourceScopeKind;
  id: string;
  label?: string;
}

export interface AgentProfile {
  id: string;
  name: string;
  description: string;
  status: "draft" | "active" | "blocked";
  revision: string;
  updatedAt: string;
  resourceCount: number;
  projectCount: number;
}

export interface PromptPayload {
  kind: "prompt";
  body: string;
  variables: string[];
}

export interface FilePayload {
  kind: "file";
  path: string;
  contents: string;
}

export interface McpPayload {
  kind: "mcp";
  server: string;
  tools: string[];
}

export type ResourcePayload = PromptPayload | FilePayload | McpPayload;

export interface ResourceProvenance {
  sourceType: string;
  sourceId: string;
  sourceGeneration: number;
  sourceRevision?: string;
  sourcePath?: string;
  digest: string;
  capturedAt?: string;
}

export interface ResourceRecord {
  id: ResourceId;
  kind: ResourceKind;
  slug: string;
  title: string;
  description: string;
  scope: ResourceScope;
  lifecycle: ResourceLifecycle;
  activeVersionNumber?: number;
  versionNumbers: number[];
  bindingCount: number;
  updatedAt: string;
  provenance: ResourceProvenance;
}

export interface ResourceVersion {
  id: string;
  resourceId: ResourceId;
  number: number;
  status: ResourceVersionStatus;
  label: string;
  changeNote: string;
  payload: ResourcePayload;
  digest: string;
  createdAt: string;
  createdBy: string;
  provenance: ResourceProvenance;
}

export interface ResourceBinding {
  id: string;
  resourceId: ResourceId;
  projectId: string;
  projectName: string;
  target: ResourceScope;
  mode: ResourceBindingMode;
  versionNumber: number;
  workspacePath?: string;
  childMayEdit: boolean;
  updatedAt: string;
}

export interface ResourceProject {
  id: string;
  name: string;
  status: "draft" | "active" | "paused" | "blocked";
}

export interface MaterializationProvenance {
  sourceType: string;
  sourceId: string;
  sourceRevision?: string;
  sourceGeneration: number;
  digest: string;
}

export interface MaterializationEntry {
  id: string;
  resourceId: ResourceId;
  kind: ResourceKind;
  target: string;
  required: boolean;
  status: MaterializationEntryStatus;
  digest: string;
  provenance: MaterializationProvenance;
  diagnostic?: string;
}

export interface MaterializationPlan {
  id: string;
  profileId: string;
  projectId: string;
  projectName: string;
  revisionId: string;
  status: MaterializationStatus;
  desiredGeneration: number;
  appliedGeneration?: number;
  digest: string;
  idempotencyKey: string;
  createdAt: string;
  updatedAt: string;
  entries: MaterializationEntry[];
  blockers: string[];
  provenance: MaterializationProvenance;
}

export interface DiffLine {
  type: "context" | "addition" | "deletion";
  text: string;
}

export interface ResourceDiff {
  resourceId: ResourceId;
  fromVersion: number;
  toVersion: number;
  summary: string;
  lines: DiffLine[];
  fromDigest: string;
  toDigest: string;
}

export interface ResourceContractState {
  ready: boolean;
  blockers: ResourceContractBlocker[];
}

export interface ResourceContractBlocker {
  code: string;
  severity: "blocking" | "warning";
  title: string;
  detail: string;
  owner: "api" | "scope" | "persistence" | "verification";
}

export interface ResourceWorkspaceSnapshot {
  contract: ResourceContractState;
  profile: AgentProfile;
  projects: ResourceProject[];
  resources: ResourceRecord[];
  versions: ResourceVersion[];
  bindings: ResourceBinding[];
  materialization?: MaterializationPlan;
}

export interface ResourceWorkspaceQuery {
  profileId: string;
  projectId?: string;
}

export interface UpdateProfileInput {
  profileId: string;
  name: string;
  description: string;
  expectedRevision: string;
}

export interface CreateResourceInput {
  profileId: string;
  kind: ResourceKind;
  slug: string;
  title: string;
  description: string;
  scope: ResourceScope;
}

export interface CreateVersionInput {
  resourceId: ResourceId;
  label: string;
  changeNote: string;
  payload: ResourcePayload;
  expectedActiveVersion?: number;
}

export interface BindResourceInput {
  resourceId: ResourceId;
  projectId: string;
  projectName: string;
  target: ResourceScope;
  mode: ResourceBindingMode;
  versionNumber: number;
  workspacePath?: string;
  childMayEdit: boolean;
}

export interface PlanMaterializationInput {
  profileId: string;
  projectId: string;
  expectedGeneration?: number;
}

export interface SyncMaterializationInput {
  planId: string;
  expectedGeneration: number;
}

export interface ResourceDiffInput {
  resourceId: ResourceId;
  fromVersion: number;
  toVersion: number;
}
