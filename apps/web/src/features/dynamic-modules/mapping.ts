/**
 * Pure projections from API records to screen view models. Everything here is
 * synchronous and side-effect free so it can be unit-tested without React.
 */

import type {
  DynamicModuleDefinition,
  DynamicModuleDefinitionSummary,
  DynamicModuleGrant,
  DynamicModuleInstance,
  DynamicModuleManifest,
  DynamicModuleStatus,
  DynamicModuleVersion,
  DynamicModuleVersionStatus,
  DynamicModuleInstanceStatus,
} from "../../api/dynamic";
import type { StatusVariant } from "../../shared/ui";

export const UNKNOWN_VALUE = "—";

const STATUS_LABELS: Readonly<Record<DynamicModuleStatus, string>> = {
  draft: "Draft",
  validated: "Validated",
  approved: "Approved",
  published: "Published",
  disabled: "Disabled",
  archived: "Archived",
};

const VERSION_STATUS_LABELS: Readonly<Record<DynamicModuleVersionStatus, string>> = {
  draft: "Draft",
  validated: "Validated",
  approved: "Approved",
  published: "Published",
  rejected: "Rejected",
  rolled_back: "Rolled back",
};

const INSTANCE_STATUS_LABELS: Readonly<Record<DynamicModuleInstanceStatus, string>> = {
  installing: "Installing",
  active: "Active",
  draining: "Draining",
  disabled: "Disabled",
  failed: "Failed",
};

export function definitionStatusLabel(status: DynamicModuleStatus): string {
  return STATUS_LABELS[status];
}

export function definitionStatusTone(status: DynamicModuleStatus): StatusVariant {
  switch (status) {
    case "published": return "success";
    case "approved":
    case "validated": return "info";
    case "disabled":
    case "archived": return "warning";
    case "draft": return "neutral";
  }
}

export function versionStatusLabel(status: DynamicModuleVersionStatus): string {
  return VERSION_STATUS_LABELS[status];
}

export function versionStatusTone(status: DynamicModuleVersionStatus): StatusVariant {
  switch (status) {
    case "published": return "success";
    case "approved":
    case "validated": return "info";
    case "rejected": return "danger";
    case "rolled_back": return "warning";
    case "draft": return "neutral";
  }
}

export function instanceStatusLabel(status: DynamicModuleInstanceStatus): string {
  return INSTANCE_STATUS_LABELS[status];
}

export function instanceStatusTone(status: DynamicModuleInstanceStatus): StatusVariant {
  switch (status) {
    case "active": return "success";
    case "installing": return "info";
    case "draining": return "warning";
    case "failed": return "danger";
    case "disabled": return "neutral";
  }
}

export function formatDateTime(value: string | undefined): string {
  if (!value) return UNKNOWN_VALUE;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return UNKNOWN_VALUE;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(parsed));
}

/** `routes` есть только у полного definition; у summary его нет, а не-массив — мусор. */
function routeCountOf(routes: unknown): number | undefined {
  return Array.isArray(routes) ? routes.length : undefined;
}

export function definitionSubtitle(definition: DynamicModuleDefinitionSummary): string {
  // Список определений (summary) не содержит routes — они приходят только в полном
  // definition. Поэтому длина маршрутов читается, только если поле реально пришло.
  const routeCount = "routes" in definition ? routeCountOf(definition.routes) : undefined;
  const parts = [
    definition.kind === "declarative-app" ? "Declarative app" : "Isolated plugin",
    `${definition.versionCount} ${definition.versionCount === 1 ? "version" : "versions"}`,
    `${definition.requestedCapabilityCount} requested ${definition.requestedCapabilityCount === 1 ? "capability" : "capabilities"}`,
  ];
  if (routeCount !== undefined) parts.push(`${routeCount} ${routeCount === 1 ? "route" : "routes"}`);
  return parts.join(" · ");
}

export function isArchived(definition: DynamicModuleDefinition | DynamicModuleDefinitionSummary): boolean {
  return definition.status === "archived" || definition.archivedAt !== undefined;
}

export function isPublished(definition: DynamicModuleDefinition | DynamicModuleDefinitionSummary): boolean {
  return definition.status === "published";
}

/* -------------------------------------------------------------------------- */
/* Version ordering                                                           */
/* -------------------------------------------------------------------------- */

function parseSemver(version: string): readonly [number, number, number, string] | undefined {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?/.exec(version);
  if (!match) return undefined;
  return [Number(match[1]), Number(match[2]), Number(match[3]), match[4] ?? ""];
}

/** Descending semantic-version order; unparsable versions sort last by text. */
export function compareVersionsDesc(left: string, right: string): number {
  const a = parseSemver(left);
  const b = parseSemver(right);
  if (a && b) {
    if (a[0] !== b[0]) return b[0] - a[0];
    if (a[1] !== b[1]) return b[1] - a[1];
    if (a[2] !== b[2]) return b[2] - a[2];
    if (a[3] === b[3]) return 0;
    if (a[3] === "") return -1;
    if (b[3] === "") return 1;
    return a[3] < b[3] ? 1 : -1;
  }
  if (a) return -1;
  if (b) return 1;
  return left < right ? 1 : left > right ? -1 : 0;
}

export function sortVersionsDesc(versions: readonly DynamicModuleVersion[]): readonly DynamicModuleVersion[] {
  return [...versions].sort((left, right) => compareVersionsDesc(left.version, right.version) || (left.id < right.id ? 1 : -1));
}

export function latestPublishedVersion(versions: readonly DynamicModuleVersion[]): DynamicModuleVersion | undefined {
  return sortVersionsDesc(versions).find((version) => version.status === "published");
}

/* -------------------------------------------------------------------------- */
/* Manifest summary                                                           */
/* -------------------------------------------------------------------------- */

export interface ManifestCapability {
  readonly id: string;
  readonly version: string;
}

export interface ManifestSummary {
  readonly kind: string;
  readonly schemaVersion: string;
  readonly appId: string;
  readonly version: string;
  readonly artifactDigest?: string;
  readonly routes: readonly string[];
  readonly capabilities: readonly ManifestCapability[];
  readonly limits: readonly { readonly key: string; readonly value: number }[];
  readonly dataPolicy: readonly { readonly key: string; readonly value: string }[];
  readonly screenCount: number;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function asText(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/**
 * Read-only descriptor of a stored manifest for the "manifest viewer". Invalid or
 * partial manifests degrade to placeholders instead of throwing: the screen renders
 * what the registry stored and never executes it.
 */
export function summarizeManifest(manifest: DynamicModuleManifest): ManifestSummary {
  const root = asRecord(manifest) ?? {};
  const routes = Array.isArray(root.routes) ? root.routes : [];
  const capabilities = Array.isArray(root.requested_capabilities) ? root.requested_capabilities : [];
  const limits = asRecord(root.limits) ?? {};
  const policy = asRecord(root.data_policy) ?? {};
  const screens = Array.isArray(root.screens) ? root.screens : [];
  return {
    kind: asText(root.kind) ?? UNKNOWN_VALUE,
    schemaVersion: asText(root.schema_version) ?? UNKNOWN_VALUE,
    appId: asText(root.app_id) ?? UNKNOWN_VALUE,
    version: asText(root.version) ?? UNKNOWN_VALUE,
    ...(asText(root.artifact_digest) ? { artifactDigest: asText(root.artifact_digest) as string } : {}),
    routes: routes
      .map((route) => {
        const record = asRecord(route);
        const path = asText(record?.path);
        const screen = asText(record?.screen);
        return path ? (screen ? `${path} → ${screen}` : path) : undefined;
      })
      .filter((entry): entry is string => entry !== undefined),
    capabilities: capabilities.flatMap((capability) => {
      const record = asRecord(capability);
      const id = asText(record?.id);
      if (!id) return [];
      const version = record?.version;
      return [{ id, version: typeof version === "number" ? String(version) : asText(version) ?? UNKNOWN_VALUE }];
    }),
    limits: Object.entries(limits)
      .filter((entry): entry is [string, number] => typeof entry[1] === "number")
      .map(([key, value]) => ({ key, value })),
    dataPolicy: Object.entries(policy)
      .filter((entry): entry is [string, string] => typeof entry[1] === "string")
      .map(([key, value]) => ({ key, value })),
    screenCount: screens.length,
  };
}

export function manifestText(manifest: DynamicModuleManifest): string {
  return JSON.stringify(manifest, null, 2);
}

/* -------------------------------------------------------------------------- */
/* Grants and instances                                                       */
/* -------------------------------------------------------------------------- */

export type GrantState = "active" | "revoked" | "expired" | "denied";

export function grantState(grant: DynamicModuleGrant, now = Date.now()): GrantState {
  if (grant.revokedAt && Date.parse(grant.revokedAt) <= now) return "revoked";
  if (grant.decision === "denied") return "denied";
  if (grant.expiresAt && Date.parse(grant.expiresAt) <= now) return "expired";
  return "active";
}

export function grantStateTone(state: GrantState): StatusVariant {
  switch (state) {
    case "active": return "success";
    case "expired": return "warning";
    case "denied": return "neutral";
    case "revoked": return "danger";
  }
}

export function grantStateLabel(state: GrantState): string {
  switch (state) {
    case "active": return "Granted";
    case "expired": return "Expired";
    case "denied": return "Denied";
    case "revoked": return "Revoked";
  }
}

export function isRevocable(grant: DynamicModuleGrant, now = Date.now()): boolean {
  return grantState(grant, now) === "active";
}

export function sortGrants(grants: readonly DynamicModuleGrant[]): readonly DynamicModuleGrant[] {
  return [...grants].sort((left, right) =>
    left.capabilityId.localeCompare(right.capabilityId)
    || left.capabilityVersion.localeCompare(right.capabilityVersion)
    || (left.projectId ?? "").localeCompare(right.projectId ?? "")
    || left.id.localeCompare(right.id));
}

export function instanceScopeLabel(instance: DynamicModuleInstance): string {
  return instance.projectId ? `Project ${instance.projectId}` : `Cabinet ${instance.cabinetId}`;
}

export function sortInstances(instances: readonly DynamicModuleInstance[]): readonly DynamicModuleInstance[] {
  return [...instances].sort((left, right) => (right.installedAt ?? "").localeCompare(left.installedAt ?? "") || left.id.localeCompare(right.id));
}

/** Next semantic version suggested in the publish form; purely a UI convenience. */
export function suggestNextVersion(current: string | undefined): string {
  if (!current) return "1.0.0";
  const parsed = parseSemver(current);
  if (!parsed) return "1.0.0";
  return `${parsed[0]}.${parsed[1]}.${parsed[2] + 1}`;
}
