/**
 * Typed client contract for the dynamic-module registry: definitions, immutable
 * versions, cabinet/project instances and capability grants.
 *
 * Boundary rules (see `docs/08-migration/dynamic-ui-implementation-contract.md` and
 * `docs/08-migration/web-cutover-implementation.md`):
 * - the shell stays the only owner of auth, tenant and cabinet/project context;
 * - every response is validated here, before it can enter React state;
 * - a malformed, partial or unexpected payload becomes a typed error, never a
 *   silently defaulted screen;
 * - no generic request/fetch escape hatch is exposed to the screens.
 *
 * `api/client.ts` belongs to another migration task, so this module extends the
 * `ApiClient` shape locally instead of editing it. The shell's API client does not
 * implement the `dynamicModules` transport yet; until the facade exposes it
 * (`MIG-WEB-FACADE-02` / the dynamic CRUD backend task), every call fails closed with
 * `DYNAMIC_MODULES_API_UNAVAILABLE` and no request leaves the browser.
 */

import type { ApiClient } from "./types";

export type Id = string;

/* -------------------------------------------------------------------------- */
/* Domain vocabulary                                                          */
/* -------------------------------------------------------------------------- */

export const DYNAMIC_MODULE_KINDS = ["declarative-app", "isolated-code-plugin"] as const;
export type DynamicModuleKind = (typeof DYNAMIC_MODULE_KINDS)[number];

/** Registry lifecycle of a definition (`dynamic-ui-implementation-contract.md` §7.1). */
export const DYNAMIC_MODULE_STATUSES = ["draft", "validated", "approved", "published", "disabled", "archived"] as const;
export type DynamicModuleStatus = (typeof DYNAMIC_MODULE_STATUSES)[number];

export const DYNAMIC_MODULE_VERSION_STATUSES = ["draft", "validated", "approved", "published", "rejected", "rolled_back"] as const;
export type DynamicModuleVersionStatus = (typeof DYNAMIC_MODULE_VERSION_STATUSES)[number];

export const DYNAMIC_MODULE_INSTANCE_STATUSES = ["installing", "active", "draining", "disabled", "failed"] as const;
export type DynamicModuleInstanceStatus = (typeof DYNAMIC_MODULE_INSTANCE_STATUSES)[number];

export const DYNAMIC_MODULE_SCOPES = ["cabinet", "project"] as const;
export type DynamicModuleScope = (typeof DYNAMIC_MODULE_SCOPES)[number];

export type DynamicModuleGrantDecision = "granted" | "denied";

export const DYNAMIC_MODULE_APP_ID_PATTERN = /^[a-z][a-z0-9-]{2,63}$/;
export const DYNAMIC_MODULE_SEMVER_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
export const DYNAMIC_MODULE_DIGEST_PATTERN = /^sha256:[0-9a-f]{64}$/;
export const DYNAMIC_MODULE_CAPABILITY_PATTERN = /^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_-]*)+$/;
export const DYNAMIC_MODULE_CAPABILITY_VERSION_PATTERN = /^[1-9][0-9]*$/;

/** Raw manifest envelope. It is data, never executable code. */
export type DynamicModuleManifest = Readonly<Record<string, unknown>>;

export interface DynamicModuleRoute {
  readonly path: string;
  readonly screenId: string;
}

export interface DynamicModuleDefinitionSummary {
  readonly id: Id;
  readonly appId: string;
  readonly displayName: string;
  readonly description?: string;
  readonly kind: DynamicModuleKind;
  readonly status: DynamicModuleStatus;
  readonly latestVersion?: string;
  readonly versionCount: number;
  readonly requestedCapabilityCount: number;
  readonly createdAt?: string;
  readonly updatedAt?: string;
  readonly archivedAt?: string;
}

export interface DynamicModuleDefinition extends DynamicModuleDefinitionSummary {
  readonly routes: readonly DynamicModuleRoute[];
  readonly createdBy?: string;
  readonly ownerCabinetId?: string;
  readonly ownerCompanyId?: string;
}

export interface DynamicModuleVersion {
  readonly id: Id;
  readonly definitionId: Id;
  readonly version: string;
  readonly status: DynamicModuleVersionStatus;
  readonly digest: string;
  readonly manifest: DynamicModuleManifest;
  readonly notes?: string;
  readonly createdBy?: string;
  readonly createdAt?: string;
  readonly publishedAt?: string;
}

export interface DynamicModuleInstance {
  readonly id: Id;
  readonly definitionId: Id;
  readonly versionId: Id;
  readonly version: string;
  readonly cabinetId: Id;
  readonly projectId?: Id;
  readonly scope: DynamicModuleScope;
  readonly status: DynamicModuleInstanceStatus;
  readonly installedBy?: string;
  readonly installedAt?: string;
}

export interface DynamicModuleGrant {
  readonly id: Id;
  readonly definitionId: Id;
  readonly instanceId?: Id;
  readonly capabilityId: string;
  readonly capabilityVersion: string;
  readonly decision: DynamicModuleGrantDecision;
  readonly scope: DynamicModuleScope;
  readonly cabinetId: Id;
  readonly projectId?: Id;
  readonly grantedBy: string;
  readonly grantedAt?: string;
  readonly expiresAt?: string;
  readonly revokedAt?: string;
  readonly reason?: string;
}

/* -------------------------------------------------------------------------- */
/* Queries and commands                                                       */
/* -------------------------------------------------------------------------- */

export interface DynamicModulesRequestOptions {
  readonly signal?: AbortSignal;
}

export interface DynamicModuleDefinitionQuery {
  readonly search?: string;
  readonly status?: DynamicModuleStatus;
  readonly cabinetId?: Id;
  readonly projectId?: Id;
  readonly cursor?: string;
  readonly limit?: number;
}

export interface DynamicModuleDefinitionPage {
  readonly items: readonly DynamicModuleDefinitionSummary[];
  readonly nextCursor?: string;
  readonly total?: number;
}

export interface DynamicModuleInstanceQuery {
  readonly definitionId: Id;
  readonly cabinetId?: Id;
  readonly projectId?: Id;
}

export interface DynamicModuleGrantQuery {
  readonly definitionId: Id;
  readonly instanceId?: Id;
  readonly cabinetId?: Id;
  readonly projectId?: Id;
}

export interface PublishDynamicModuleVersionInput {
  /** Full immutable manifest envelope submitted for publication. */
  readonly manifest: DynamicModuleManifest;
  /** Optimistic concurrency: the latest version the operator saw before publishing. */
  readonly expectedVersion?: string;
  readonly notes?: string;
  readonly idempotencyKey: string;
}

export interface InstallDynamicModuleInstanceInput {
  readonly definitionId: Id;
  readonly versionId: Id;
  readonly cabinetId: Id;
  readonly projectId?: Id;
  readonly scope: DynamicModuleScope;
  readonly idempotencyKey: string;
}

export interface GrantDynamicModuleCapabilityInput {
  readonly definitionId: Id;
  readonly instanceId?: Id;
  readonly capabilityId: string;
  readonly capabilityVersion: string;
  readonly scope: DynamicModuleScope;
  readonly cabinetId: Id;
  readonly projectId?: Id;
  readonly expiresAt?: string;
  readonly reason?: string;
  readonly idempotencyKey: string;
}

export interface RevokeDynamicModuleGrantInput {
  readonly definitionId: Id;
  readonly grantId: Id;
  readonly reason?: string;
  readonly idempotencyKey: string;
}

export interface ArchiveDynamicModuleDefinitionInput {
  readonly reason?: string;
  readonly expectedVersion?: string;
  readonly idempotencyKey: string;
}

export interface DynamicModuleDirectoryEntry {
  readonly id: Id;
  readonly name: string;
}

/* -------------------------------------------------------------------------- */
/* Errors                                                                     */
/* -------------------------------------------------------------------------- */

export class DynamicModulesApiUnavailableError extends Error {
  readonly code = "DYNAMIC_MODULES_API_UNAVAILABLE" as const;
  constructor(readonly missing: readonly string[]) {
    super(`Dynamic module API is unavailable: missing ${missing.join(", ") || "transport"}`);
    this.name = "DynamicModulesApiUnavailableError";
  }
}

export class DynamicModulesProtocolError extends Error {
  readonly code = "DYNAMIC_MODULES_INVALID_RESPONSE" as const;
  constructor(readonly field: string) {
    super(`Dynamic module response rejected at the boundary: ${field}`);
    this.name = "DynamicModulesProtocolError";
  }
}

/* -------------------------------------------------------------------------- */
/* Boundary normalization                                                     */
/* -------------------------------------------------------------------------- */

type Wire = Record<string, unknown>;

function record(value: unknown): value is Wire {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstDefined(...values: readonly unknown[]): unknown {
  return values.find((value) => value !== undefined);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim().length === 0) throw new DynamicModulesProtocolError(field);
  return value;
}

function optionalString(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new DynamicModulesProtocolError(field);
  return value;
}

function optionalTimestamp(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) throw new DynamicModulesProtocolError(field);
  return value;
}

function requireEnum<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== "string") throw new DynamicModulesProtocolError(field);
  const match = allowed.find((candidate) => candidate === value);
  if (!match) throw new DynamicModulesProtocolError(field);
  return match;
}

function requireCount(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw new DynamicModulesProtocolError(field);
  return value;
}

function optionalCount(value: unknown, field: string): number {
  return value === undefined || value === null ? 0 : requireCount(value, field);
}

function requirePattern(value: unknown, pattern: RegExp, field: string): string {
  const text = requireString(value, field);
  if (!pattern.test(text)) throw new DynamicModulesProtocolError(field);
  return text;
}

function readItems(payload: unknown, field: string): readonly unknown[] {
  if (Array.isArray(payload)) return payload;
  if (record(payload) && Array.isArray(payload.items)) return payload.items;
  throw new DynamicModulesProtocolError(field);
}

function readEnvelope(payload: unknown, field: string): Wire {
  if (!record(payload)) throw new DynamicModulesProtocolError(field);
  return payload;
}

export function normalizeDefinitionSummary(raw: unknown, field = "definition"): DynamicModuleDefinitionSummary {
  const value = readEnvelope(raw, field);
  return {
    id: requireString(value.id, `${field}.id`),
    appId: requirePattern(firstDefined(value.app_id, value.appId), DYNAMIC_MODULE_APP_ID_PATTERN, `${field}.app_id`),
    displayName: requireString(firstDefined(value.display_name, value.displayName), `${field}.display_name`),
    description: optionalString(value.description, `${field}.description`),
    kind: requireEnum(value.kind, DYNAMIC_MODULE_KINDS, `${field}.kind`),
    status: requireEnum(value.status, DYNAMIC_MODULE_STATUSES, `${field}.status`),
    latestVersion: optionalString(firstDefined(value.latest_version, value.latestVersion), `${field}.latest_version`),
    versionCount: optionalCount(firstDefined(value.version_count, value.versionCount), `${field}.version_count`),
    requestedCapabilityCount: optionalCount(
      firstDefined(value.requested_capability_count, value.requestedCapabilityCount),
      `${field}.requested_capability_count`,
    ),
    createdAt: optionalTimestamp(firstDefined(value.created_at, value.createdAt), `${field}.created_at`),
    updatedAt: optionalTimestamp(firstDefined(value.updated_at, value.updatedAt), `${field}.updated_at`),
    archivedAt: optionalTimestamp(firstDefined(value.archived_at, value.archivedAt), `${field}.archived_at`),
  };
}

function normalizeRoute(raw: unknown, field: string): DynamicModuleRoute {
  const value = readEnvelope(raw, field);
  const path = requireString(value.path, `${field}.path`);
  if (!path.startsWith("/") || path.startsWith("//") || /[\\?#\u0000-\u001f\u007f]/.test(path)) {
    throw new DynamicModulesProtocolError(`${field}.path`);
  }
  return {
    path,
    screenId: requireString(firstDefined(value.screen, value.screenId, value.screen_id), `${field}.screen`),
  };
}

export function normalizeDefinition(raw: unknown, field = "definition"): DynamicModuleDefinition {
  const summary = normalizeDefinitionSummary(raw, field);
  const value = readEnvelope(raw, field);
  const rawRoutes = value.routes ?? [];
  if (!Array.isArray(rawRoutes)) throw new DynamicModulesProtocolError(`${field}.routes`);
  return {
    ...summary,
    routes: rawRoutes.map((route, index) => normalizeRoute(route, `${field}.routes[${index}]`)),
    createdBy: optionalString(firstDefined(value.created_by, value.createdBy), `${field}.created_by`),
    ownerCabinetId: optionalString(firstDefined(value.owner_cabinet_id, value.ownerCabinetId), `${field}.owner_cabinet_id`),
    ownerCompanyId: optionalString(firstDefined(value.owner_company_id, value.ownerCompanyId), `${field}.owner_company_id`),
  };
}

export function normalizeDefinitionPage(payload: unknown, field = "definition_page"): DynamicModuleDefinitionPage {
  const items = readItems(payload, field).map((item, index) => normalizeDefinitionSummary(item, `${field}.items[${index}]`));
  const envelope = record(payload) ? payload : {};
  const total = envelope.total === undefined || envelope.total === null ? undefined : requireCount(envelope.total, `${field}.total`);
  return {
    items,
    nextCursor: optionalString(firstDefined(envelope.next_cursor, envelope.nextCursor), `${field}.next_cursor`),
    total,
  };
}

export function normalizeVersion(raw: unknown, field = "version"): DynamicModuleVersion {
  const value = readEnvelope(raw, field);
  const manifest = value.manifest;
  if (!record(manifest)) throw new DynamicModulesProtocolError(`${field}.manifest`);
  return {
    id: requireString(value.id, `${field}.id`),
    definitionId: requireString(firstDefined(value.definition_id, value.definitionId), `${field}.definition_id`),
    version: requirePattern(value.version, DYNAMIC_MODULE_SEMVER_PATTERN, `${field}.version`),
    status: requireEnum(value.status, DYNAMIC_MODULE_VERSION_STATUSES, `${field}.status`),
    digest: requirePattern(firstDefined(value.digest, value.artifact_digest), DYNAMIC_MODULE_DIGEST_PATTERN, `${field}.digest`),
    manifest,
    notes: optionalString(value.notes, `${field}.notes`),
    createdBy: optionalString(firstDefined(value.created_by, value.createdBy), `${field}.created_by`),
    createdAt: optionalTimestamp(firstDefined(value.created_at, value.createdAt), `${field}.created_at`),
    publishedAt: optionalTimestamp(firstDefined(value.published_at, value.publishedAt), `${field}.published_at`),
  };
}

export function normalizeVersions(payload: unknown, field = "versions"): readonly DynamicModuleVersion[] {
  return readItems(payload, field).map((item, index) => normalizeVersion(item, `${field}[${index}]`));
}

export function normalizeInstance(raw: unknown, field = "instance"): DynamicModuleInstance {
  const value = readEnvelope(raw, field);
  return {
    id: requireString(value.id, `${field}.id`),
    definitionId: requireString(firstDefined(value.definition_id, value.definitionId), `${field}.definition_id`),
    versionId: requireString(firstDefined(value.version_id, value.versionId), `${field}.version_id`),
    version: requirePattern(value.version, DYNAMIC_MODULE_SEMVER_PATTERN, `${field}.version`),
    cabinetId: requireString(firstDefined(value.cabinet_id, value.cabinetId), `${field}.cabinet_id`),
    projectId: optionalString(firstDefined(value.project_id, value.projectId), `${field}.project_id`),
    scope: requireEnum(value.scope, DYNAMIC_MODULE_SCOPES, `${field}.scope`),
    status: requireEnum(value.status, DYNAMIC_MODULE_INSTANCE_STATUSES, `${field}.status`),
    installedBy: optionalString(firstDefined(value.installed_by, value.installedBy), `${field}.installed_by`),
    installedAt: optionalTimestamp(firstDefined(value.installed_at, value.installedAt), `${field}.installed_at`),
  };
}

export function normalizeInstances(payload: unknown, field = "instances"): readonly DynamicModuleInstance[] {
  return readItems(payload, field).map((item, index) => normalizeInstance(item, `${field}[${index}]`));
}

export function normalizeGrant(raw: unknown, field = "grant"): DynamicModuleGrant {
  const value = readEnvelope(raw, field);
  const rawCapabilityVersion = firstDefined(value.capability_version, value.capabilityVersion);
  return {
    id: requireString(value.id, `${field}.id`),
    definitionId: requireString(firstDefined(value.definition_id, value.definitionId), `${field}.definition_id`),
    instanceId: optionalString(firstDefined(value.instance_id, value.instanceId), `${field}.instance_id`),
    capabilityId: requirePattern(firstDefined(value.capability_id, value.capabilityId), DYNAMIC_MODULE_CAPABILITY_PATTERN, `${field}.capability_id`),
    capabilityVersion: requirePattern(
      typeof rawCapabilityVersion === "number" ? String(rawCapabilityVersion) : rawCapabilityVersion,
      DYNAMIC_MODULE_CAPABILITY_VERSION_PATTERN,
      `${field}.capability_version`,
    ),
    decision: requireEnum(value.decision, ["granted", "denied"] as const, `${field}.decision`),
    scope: requireEnum(value.scope, DYNAMIC_MODULE_SCOPES, `${field}.scope`),
    cabinetId: requireString(firstDefined(value.cabinet_id, value.cabinetId), `${field}.cabinet_id`),
    projectId: optionalString(firstDefined(value.project_id, value.projectId), `${field}.project_id`),
    grantedBy: requireString(firstDefined(value.granted_by, value.grantedBy), `${field}.granted_by`),
    grantedAt: optionalTimestamp(firstDefined(value.granted_at, value.grantedAt), `${field}.granted_at`),
    expiresAt: optionalTimestamp(firstDefined(value.expires_at, value.expiresAt), `${field}.expires_at`),
    revokedAt: optionalTimestamp(firstDefined(value.revoked_at, value.revokedAt), `${field}.revoked_at`),
    reason: optionalString(value.reason, `${field}.reason`),
  };
}

export function normalizeGrants(payload: unknown, field = "grants"): readonly DynamicModuleGrant[] {
  return readItems(payload, field).map((item, index) => normalizeGrant(item, `${field}[${index}]`));
}

function normalizeDirectory(payload: unknown, field: string): readonly DynamicModuleDirectoryEntry[] {
  return readItems(payload, field).map((item, index) => {
    const value = readEnvelope(item, `${field}[${index}]`);
    return {
      id: requireString(value.id, `${field}[${index}].id`),
      name: requireString(value.name, `${field}[${index}].name`),
    };
  });
}

/* -------------------------------------------------------------------------- */
/* Transport contract                                                         */
/* -------------------------------------------------------------------------- */

/**
 * Methods the API facade must expose for the CRUD screens. Payloads are `unknown`
 * on purpose: the facade owns HTTP, this module owns boundary validation.
 */
export interface DynamicModulesTransport {
  listDefinitions(query: DynamicModuleDefinitionQuery, options?: DynamicModulesRequestOptions): Promise<unknown>;
  getDefinition(definitionId: Id, options?: DynamicModulesRequestOptions): Promise<unknown>;
  listVersions(definitionId: Id, options?: DynamicModulesRequestOptions): Promise<unknown>;
  publishVersion(definitionId: Id, input: PublishDynamicModuleVersionInput, options?: DynamicModulesRequestOptions): Promise<unknown>;
  listInstances(query: DynamicModuleInstanceQuery, options?: DynamicModulesRequestOptions): Promise<unknown>;
  installInstance(input: InstallDynamicModuleInstanceInput, options?: DynamicModulesRequestOptions): Promise<unknown>;
  listGrants(query: DynamicModuleGrantQuery, options?: DynamicModulesRequestOptions): Promise<unknown>;
  grantCapability(input: GrantDynamicModuleCapabilityInput, options?: DynamicModulesRequestOptions): Promise<unknown>;
  revokeCapability(input: RevokeDynamicModuleGrantInput, options?: DynamicModulesRequestOptions): Promise<unknown>;
  archiveDefinition(definitionId: Id, input: ArchiveDynamicModuleDefinitionInput, options?: DynamicModulesRequestOptions): Promise<unknown>;
}

/** Local extension of the shared `ApiClient`; `api/client.ts` is not modified. */
export interface DynamicModulesApiClient extends ApiClient {
  readonly dynamicModules?: DynamicModulesTransport;
}

export const DYNAMIC_MODULES_TRANSPORT_METHODS = [
  "listDefinitions",
  "getDefinition",
  "listVersions",
  "publishVersion",
  "listInstances",
  "installInstance",
  "listGrants",
  "grantCapability",
  "revokeCapability",
  "archiveDefinition",
] as const;

type TransportMethod = (typeof DYNAMIC_MODULES_TRANSPORT_METHODS)[number];

export function dynamicModulesTransportOf(api: ApiClient | undefined): DynamicModulesTransport | undefined {
  if (!api) return undefined;
  const candidate = (api as DynamicModulesApiClient).dynamicModules;
  if (!candidate || typeof candidate !== "object") return undefined;
  const complete = DYNAMIC_MODULES_TRANSPORT_METHODS.every(
    (method) => typeof (candidate as unknown as Record<TransportMethod, unknown>)[method] === "function",
  );
  return complete ? candidate : undefined;
}

/** UI-safe description of the transport gap; method names only, never a payload or token. */
export function describeDynamicModulesApi(api: ApiClient | undefined): { readonly available: boolean; readonly missing: readonly string[] } {
  const transport = dynamicModulesTransportOf(api);
  if (transport) return { available: true, missing: [] };
  const candidate = api ? (api as DynamicModulesApiClient).dynamicModules as unknown as Record<TransportMethod, unknown> | undefined : undefined;
  const missing = DYNAMIC_MODULES_TRANSPORT_METHODS.filter((method) => typeof candidate?.[method] !== "function");
  return { available: false, missing };
}

function requireTransport(api: ApiClient | undefined): DynamicModulesTransport {
  const transport = dynamicModulesTransportOf(api);
  if (!transport) throw new DynamicModulesApiUnavailableError(describeDynamicModulesApi(api).missing);
  return transport;
}

/* -------------------------------------------------------------------------- */
/* Query sanitizing                                                           */
/* -------------------------------------------------------------------------- */

function boundedText(value: string | undefined, max: number): string | undefined {
  if (value === undefined) return undefined;
  const trimmed = value.trim();
  return trimmed.length === 0 ? undefined : trimmed.slice(0, max);
}

function boundedLimit(value: number | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return Math.min(100, Math.max(1, Math.trunc(value)));
}

export function sanitizeDefinitionQuery(query: DynamicModuleDefinitionQuery = {}): DynamicModuleDefinitionQuery {
  return {
    search: boundedText(query.search, 200),
    status: query.status,
    cabinetId: boundedText(query.cabinetId, 128),
    projectId: boundedText(query.projectId, 128),
    cursor: boundedText(query.cursor, 512),
    limit: boundedLimit(query.limit),
  };
}

/* -------------------------------------------------------------------------- */
/* Gateway                                                                    */
/* -------------------------------------------------------------------------- */

export interface DynamicModulesGateway {
  listDefinitions(query?: DynamicModuleDefinitionQuery, options?: DynamicModulesRequestOptions): Promise<DynamicModuleDefinitionPage>;
  getDefinition(definitionId: Id, options?: DynamicModulesRequestOptions): Promise<DynamicModuleDefinition>;
  listVersions(definitionId: Id, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleVersion[]>;
  publishVersion(definitionId: Id, input: PublishDynamicModuleVersionInput, options?: DynamicModulesRequestOptions): Promise<DynamicModuleVersion>;
  listInstances(query: DynamicModuleInstanceQuery, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleInstance[]>;
  installInstance(input: InstallDynamicModuleInstanceInput, options?: DynamicModulesRequestOptions): Promise<DynamicModuleInstance>;
  listGrants(query: DynamicModuleGrantQuery, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleGrant[]>;
  grantCapability(input: GrantDynamicModuleCapabilityInput, options?: DynamicModulesRequestOptions): Promise<DynamicModuleGrant>;
  revokeCapability(input: RevokeDynamicModuleGrantInput, options?: DynamicModulesRequestOptions): Promise<DynamicModuleGrant>;
  archiveDefinition(definitionId: Id, input: ArchiveDynamicModuleDefinitionInput, options?: DynamicModulesRequestOptions): Promise<DynamicModuleDefinition>;
  /** Cabinet/project directory used by the install and grant forms (existing ApiClient methods). */
  listCabinets(options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleDirectoryEntry[]>;
  listProjects(cabinetId: Id, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleDirectoryEntry[]>;
}

function requireDefinitionId(definitionId: Id): Id {
  if (typeof definitionId !== "string" || definitionId.trim().length === 0) {
    throw new DynamicModulesProtocolError("definition_id");
  }
  return definitionId;
}

/**
 * Builds the typed gateway over an `ApiClient`. Nothing here reaches the network
 * directly: if the client has no `dynamicModules` transport the gateway fails closed
 * with `DynamicModulesApiUnavailableError` before any request is attempted.
 */
export function createDynamicModulesGateway(api: ApiClient | undefined): DynamicModulesGateway {
  return {
    listDefinitions: async (query = {}, options) =>
      normalizeDefinitionPage(await requireTransport(api).listDefinitions(sanitizeDefinitionQuery(query), options)),
    getDefinition: async (definitionId, options) =>
      normalizeDefinition(await requireTransport(api).getDefinition(requireDefinitionId(definitionId), options)),
    listVersions: async (definitionId, options) =>
      normalizeVersions(await requireTransport(api).listVersions(requireDefinitionId(definitionId), options)),
    publishVersion: async (definitionId, input, options) =>
      normalizeVersion(await requireTransport(api).publishVersion(requireDefinitionId(definitionId), input, options)),
    listInstances: async (query, options) =>
      normalizeInstances(await requireTransport(api).listInstances(query, options)),
    installInstance: async (input, options) =>
      normalizeInstance(await requireTransport(api).installInstance(input, options)),
    listGrants: async (query, options) =>
      normalizeGrants(await requireTransport(api).listGrants(query, options)),
    grantCapability: async (input, options) =>
      normalizeGrant(await requireTransport(api).grantCapability(input, options)),
    revokeCapability: async (input, options) =>
      normalizeGrant(await requireTransport(api).revokeCapability(input, options)),
    archiveDefinition: async (definitionId, input, options) =>
      normalizeDefinition(await requireTransport(api).archiveDefinition(requireDefinitionId(definitionId), input, options)),
    listCabinets: async (options) => {
      void options;
      if (!api || typeof api.listCabinets !== "function") throw new DynamicModulesApiUnavailableError(["listCabinets"]);
      return normalizeDirectory(await api.listCabinets(), "cabinets");
    },
    listProjects: async (cabinetId, options) => {
      void options;
      if (!api || typeof api.listProjects !== "function") throw new DynamicModulesApiUnavailableError(["listProjects"]);
      if (typeof cabinetId !== "string" || cabinetId.trim().length === 0) throw new DynamicModulesProtocolError("cabinet_id");
      return normalizeDirectory(await api.listProjects(cabinetId), "projects");
    },
  };
}
