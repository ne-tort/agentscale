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
import type { Translate } from "../../i18n/catalogue";
import type { Plural } from "../../i18n/useT";
import type { StatusVariant } from "../../shared/ui";

export const UNKNOWN_VALUE = "—";

/**
 * Status labels are words on screen, never the API's own enum value: an operator
 * should read "Опубликовано", not "published". The translator is passed in rather
 * than imported so the label follows the language the page is rendered in.
 */
export function definitionStatusLabel(status: DynamicModuleStatus, t: Translate): string {
  switch (status) {
    case "draft": return t("dynamicModules.statusDraft");
    case "validated": return t("dynamicModules.statusValidated");
    case "approved": return t("dynamicModules.statusApproved");
    case "published": return t("dynamicModules.statusPublished");
    case "disabled": return t("dynamicModules.statusDisabled");
    case "archived": return t("dynamicModules.statusArchived");
    case "revoked": return t("dynamicModules.statusRevoked");
  }
}

export function definitionStatusTone(status: DynamicModuleStatus): StatusVariant {
  switch (status) {
    case "published": return "success";
    case "approved":
    case "validated": return "info";
    case "disabled":
    case "archived": return "warning";
    case "revoked": return "danger";
    case "draft": return "neutral";
  }
}

export function versionStatusLabel(status: DynamicModuleVersionStatus, t: Translate): string {
  switch (status) {
    case "draft": return t("dynamicModules.statusDraft");
    case "validated": return t("dynamicModules.statusValidated");
    case "approved": return t("dynamicModules.statusApproved");
    case "published": return t("dynamicModules.statusPublished");
    case "rejected": return t("dynamicModules.versionStatusRejected");
    case "rolled_back": return t("dynamicModules.versionStatusRolledBack");
  }
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

export function instanceStatusLabel(status: DynamicModuleInstanceStatus, t: Translate): string {
  switch (status) {
    case "installing": return t("dynamicModules.instanceStatusInstalling");
    case "active": return t("dynamicModules.instanceStatusActive");
    case "draining": return t("dynamicModules.instanceStatusDraining");
    case "disabled": return t("dynamicModules.statusDisabled");
    case "failed": return t("dynamicModules.instanceStatusFailed");
  }
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

/** Dates follow the interface language, not the OS locale the browser happened to pick. */
export function formatDateTime(value: string | undefined, locale?: string): string {
  if (!value) return UNKNOWN_VALUE;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return UNKNOWN_VALUE;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(parsed));
}

/** `routes` есть только у полного definition; у summary его нет, а не-массив — мусор. */
function routeCountOf(routes: unknown): number | undefined {
  return Array.isArray(routes) ? routes.length : undefined;
}

export function definitionSubtitle(
  definition: DynamicModuleDefinitionSummary,
  t: Translate,
  plural: Plural,
): string {
  // Список определений (summary) не содержит routes — они приходят только в полном
  // definition. Поэтому длина маршрутов читается, только если поле реально пришло.
  const routeCount = "routes" in definition ? routeCountOf(definition.routes) : undefined;
  // Counts the server did not report are left out instead of being shown as zero.
  const parts = [
    definition.kind === "declarative-app" ? t("dynamicModules.kindDeclarativeApp") : t("dynamicModules.kindIsolatedPlugin"),
  ];
  if (definition.versionCount !== undefined) parts.push(plural("dynamicModules.versionCount", definition.versionCount));
  if (definition.requestedCapabilityCount !== undefined) {
    parts.push(plural("dynamicModules.capabilityCount", definition.requestedCapabilityCount));
  }
  if (routeCount !== undefined) parts.push(plural("dynamicModules.routeCount", routeCount));
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
 *
 * ``manifest`` is accepted as optional because the API reports a version whose
 * manifest could not be read as absent, not as an empty object. The body already
 * tolerated that (``asRecord(...) ?? {}``); the signature now says so, so callers do
 * not have to pretend the value is present.
 */
export function summarizeManifest(manifest: DynamicModuleManifest | undefined): ManifestSummary {
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

/**
 * Pretty-printed manifest for the viewer.
 *
 * ``undefined`` renders as an empty block: the API reports a version whose manifest
 * could not be read as absent, and the viewer must show that absence rather than the
 * string "undefined" or a fabricated object.
 */
export function manifestText(manifest: DynamicModuleManifest | undefined): string {
  return manifest === undefined ? "" : JSON.stringify(manifest, null, 2);
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

export function grantStateLabel(state: GrantState, t: Translate): string {
  switch (state) {
    case "active": return t("dynamicModules.grantStateGranted");
    case "expired": return t("dynamicModules.grantStateExpired");
    case "denied": return t("dynamicModules.grantStateDenied");
    case "revoked": return t("dynamicModules.grantStateRevoked");
  }
}

export function isRevocable(grant: DynamicModuleGrant, now = Date.now()): boolean {
  return grantState(grant, now) === "active";
}

export function sortGrants(grants: readonly DynamicModuleGrant[]): readonly DynamicModuleGrant[] {
  return [...grants].sort((left, right) =>
    left.capabilityId.localeCompare(right.capabilityId)
    // A grant row does not echo the accepted capability version, so an absent value
    // sorts first rather than being replaced with a fabricated default.
    || (left.capabilityVersion ?? "").localeCompare(right.capabilityVersion ?? "")
    || (left.projectId ?? "").localeCompare(right.projectId ?? "")
    || left.id.localeCompare(right.id));
}

export function instanceScopeLabel(instance: DynamicModuleInstance, t: Translate): string {
  if (instance.projectId) return t("dynamicModules.scopeProject", { id: instance.projectId });
  // ``normalizeScope`` guarantees exactly one of the two for every accepted row;
  // the fallback keeps a malformed DTO from rendering "Cabinet undefined".
  return instance.cabinetId
    ? t("dynamicModules.scopeCabinet", { id: instance.cabinetId })
    : t("dynamicModules.scopeUnknown");
}

/**
 * The definition row has no `kind` column, so the API cannot send one. The screen
 * says so instead of labelling every app with a default it did not read.
 */
export function definitionKindLabel(kind: DynamicModuleDefinition["kind"], t: Translate): string {
  if (kind === "declarative-app") return t("dynamicModules.kindDeclarativeApp");
  if (kind === "isolated-code-plugin") return t("dynamicModules.kindIsolatedCodePlugin");
  return t("dynamicModules.kindNotReported");
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
