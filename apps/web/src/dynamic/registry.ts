import type { DeclarativeAppManifest, UiNode } from "../../../../packages/schemas/declarative-app/v1";
import { cloneJsonData, deepFreeze, validateDeclarativeManifest, validateManifestRoute, validateRoute, validateRouteRequirements, type ValidatedRoute } from "./schema";
import { createRequestContext, type ArtifactError, type RequestContext } from "./protocol";
import { evaluateRoute } from "./policy";

export interface SnapshotSignature {
  readonly algorithm: "Ed25519";
  readonly keyId: string;
  readonly value: string;
}
export interface SignedRegistryEntry {
  readonly appId: string;
  readonly version: string;
  readonly artifactDigest: string;
  readonly schemaVersion: "1.0";
  readonly status: "published" | "disabled" | "revoked" | "rolled_back" | "quarantined";
  readonly tenantId: string;
  readonly cabinetId?: string;
  readonly projectId?: string;
  readonly manifest: unknown;
  readonly routes: readonly (ValidatedRoute & { readonly signatureStatus: "verified" | "missing" | "invalid" })[];
  readonly grants: readonly string[];
}
export interface SignedRegistrySnapshot {
  readonly schemaVersion: "1.0";
  readonly apiContractVersion: string;
  readonly digest: string;
  readonly signatureStatus: "verified" | "missing" | "invalid";
  readonly signature: SnapshotSignature;
  readonly issuedAt: string;
  readonly ttlSeconds: number;
  readonly entries: readonly SignedRegistryEntry[];
}
export interface RegistryResponse {
  readonly snapshot?: unknown;
  readonly etag: string;
  readonly correlationId: string;
  readonly notModified?: boolean;
  readonly maxAgeSeconds?: number;
}
export interface RegistryFetchInput {
  readonly etag?: string;
  readonly correlationId: string;
  readonly context: RequestContext;
  readonly signal?: AbortSignal;
}
export type RegistryFetcher = (input: RegistryFetchInput) => Promise<RegistryResponse>;
export interface RegistryVerificationInput {
  readonly correlationId: string;
  readonly context: RequestContext;
  readonly signal?: AbortSignal;
}
/** Must verify the canonical snapshot payload digest and its signature against a trusted key. */
export type SignedSnapshotVerifier = (snapshot: SignedRegistrySnapshot, input: RegistryVerificationInput) => Promise<boolean>;
export interface RegistryRequest {
  readonly appId: string;
  readonly version: string;
  readonly routeId: string;
  readonly context: RequestContext;
  readonly effectiveCapabilities: ReadonlySet<string>;
  readonly signal?: AbortSignal;
}
export type RegistryResolution =
  | { readonly state: "resolved"; readonly manifest: DeclarativeAppManifest; readonly route: ValidatedRoute; readonly digest: string; readonly etag: string; readonly cacheKey: string; readonly correlationId: string }
  | ArtifactError;
interface CacheRecord { readonly snapshot: SignedRegistrySnapshot; readonly etag: string; readonly expiresAt: number; readonly snapshotExpiresAt: number; readonly scopeKey: string; readonly cacheKey: string }

const CAPABILITIES = new Set(["ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch", "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read"]);
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const ETAG = /^(?:W\/)?"[\x21\x23-\x7e]{0,250}"$/;
const CACHE_TTL_CAP_SECONDS = 3600;
const RESERVED_KEYS = new Set(["__proto__", "prototype", "constructor"]);

function scopeKey(context: RequestContext): string {
  return JSON.stringify([context.tenantId, context.cabinetId ?? null, context.projectId ?? null, context.sessionId ?? null]);
}
function failure(code: ArtifactError["code"], context: RequestContext): ArtifactError {
  if (code === "unknown_app") return Object.freeze({ state: "unknown_app", code, traceId: context.traceId });
  if (code === "unknown_route") return Object.freeze({ state: "unknown_route", code, traceId: context.traceId });
  if (code === "invalid_app" || code === "validation_error") return Object.freeze({ state: "invalid", code, traceId: context.traceId });
  if (code === "capability_denied" || code === "missing_context" || code === "route_scope_mismatch") return Object.freeze({ state: "denied", code, traceId: context.traceId });
  if (code === "quarantined" || code === "correlation_mismatch" || code === "replay_detected") return Object.freeze({ state: "quarantined", code, traceId: context.traceId });
  return Object.freeze({ state: "stale", code: "stale_revision", traceId: context.traceId });
}
function ownNonEmpty(value: unknown): value is string { return typeof value === "string" && value.trim().length > 0; }
function record(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function exactObject(value: unknown, keys: readonly string[], required: readonly string[] = keys): value is Record<string, unknown> {
  return record(value) && Object.getOwnPropertySymbols(value).length === 0 && Object.getOwnPropertyNames(value).every((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return Boolean(descriptor && "value" in descriptor && descriptor.enumerable && keys.includes(key) && !RESERVED_KEYS.has(key));
  }) && required.every((key) => Object.hasOwn(value, key));
}
function isCapabilityName(value: string): boolean {
  return CAPABILITIES.has(value);
}
function validSignature(value: unknown): value is SnapshotSignature {
  return exactObject(value, ["algorithm", "keyId", "value"]) && value.algorithm === "Ed25519" && ownNonEmpty(value.keyId) && value.keyId.length <= 256 && typeof value.value === "string" && /^[A-Za-z0-9_-]{80,256}$/.test(value.value);
}
function validRouteEntry(value: unknown): value is SignedRegistryEntry["routes"][number] {
  if (!exactObject(value, ["id", "path", "title", "root", "requirements", "signatureStatus"]) ||
      !(value.signatureStatus === "verified" || value.signatureStatus === "missing" || value.signatureStatus === "invalid")) return false;
  const routeFields = { id: value.id, path: value.path, title: value.title, root: value.root, requirements: value.requirements };
  return validateRoute(routeFields, new Set([typeof value.root === "string" ? value.root : ""]));
}
function safeJsonData(value: unknown, path = new WeakSet<object>(), depth = 0, budget = { nodes: 0 }): boolean {
  budget.nodes += 1;
  if (budget.nodes > 100_000 || depth > 64) return false;
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || path.has(value)) return false;
  if (Array.isArray(value)) {
    if (Object.getOwnPropertySymbols(value).length > 0 || Object.keys(value).length !== value.length || Object.getOwnPropertyNames(value).length !== value.length + 1) return false;
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) return false;
    }
    path.add(value);
    const valid = value.every((item) => safeJsonData(item, path, depth + 1, budget));
    path.delete(value);
    return valid;
  }
  if (!record(value) || Object.getOwnPropertySymbols(value).length > 0 || Object.getOwnPropertyNames(value).some((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return !descriptor || !("value" in descriptor) || !descriptor.enumerable || RESERVED_KEYS.has(key);
  })) return false;
  path.add(value);
  const valid = Object.values(value).every((item) => safeJsonData(item, path, depth + 1, budget));
  path.delete(value);
  return valid;
}
function validEntry(value: unknown): value is SignedRegistryEntry {
  if (!exactObject(value, ["appId", "version", "artifactDigest", "schemaVersion", "status", "tenantId", "cabinetId", "projectId", "manifest", "routes", "grants"], ["appId", "version", "artifactDigest", "schemaVersion", "status", "tenantId", "manifest", "routes", "grants"])) return false;
  return ownNonEmpty(value.appId) && /^[a-z][a-z0-9-]{2,63}$/.test(value.appId) && ownNonEmpty(value.version) &&
    typeof value.artifactDigest === "string" && DIGEST.test(value.artifactDigest) && value.schemaVersion === "1.0" &&
    ["published", "disabled", "revoked", "rolled_back", "quarantined"].includes(String(value.status)) && ownNonEmpty(value.tenantId) &&
    (value.cabinetId === undefined || ownNonEmpty(value.cabinetId)) && (value.projectId === undefined || ownNonEmpty(value.projectId)) &&
    Array.isArray(value.routes) && value.routes.length <= 256 && value.routes.every(validRouteEntry) &&
    new Set(value.routes.map((route) => route.id)).size === value.routes.length &&
    new Set(value.routes.map((route) => route.path)).size === value.routes.length &&
    Array.isArray(value.grants) && value.grants.every((grant) => typeof grant === "string" && isCapabilityName(grant)) && new Set(value.grants).size === value.grants.length;
}
function validSnapshot(value: unknown): value is SignedRegistrySnapshot {
  if (!safeJsonData(value) || !exactObject(value, ["schemaVersion", "apiContractVersion", "digest", "signatureStatus", "signature", "issuedAt", "ttlSeconds", "entries"])) return false;
  const issuedAt = typeof value.issuedAt === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value.issuedAt) ? Date.parse(value.issuedAt) : NaN;
  return value.schemaVersion === "1.0" && ownNonEmpty(value.apiContractVersion) && value.apiContractVersion.length <= 128 &&
    typeof value.digest === "string" && DIGEST.test(value.digest) &&
    (value.signatureStatus === "verified" || value.signatureStatus === "missing" || value.signatureStatus === "invalid") &&
    validSignature(value.signature) && Number.isFinite(issuedAt) && Number.isInteger(value.ttlSeconds) && value.ttlSeconds >= 1 && value.ttlSeconds <= 86_400 &&
    Array.isArray(value.entries) && value.entries.length <= 10_000 && value.entries.every(validEntry) &&
    new Set(value.entries.map((entry) => `${entry.appId}\u0000${entry.version}`)).size === value.entries.length;
}
function validResponse(value: unknown): value is RegistryResponse {
  if (!exactObject(value, ["snapshot", "etag", "correlationId", "notModified", "maxAgeSeconds"], ["etag", "correlationId"])) return false;
  if (typeof value.etag !== "string" || !ETAG.test(value.etag) || !ownNonEmpty(value.correlationId) || value.correlationId.length > 256) return false;
  if (value.notModified !== undefined && typeof value.notModified !== "boolean") return false;
  if (value.maxAgeSeconds !== undefined && (!Number.isInteger(value.maxAgeSeconds) || (value.maxAgeSeconds as number) < 0 || (value.maxAgeSeconds as number) > 86_400)) return false;
  return value.notModified === true ? value.snapshot === undefined : value.snapshot !== undefined;
}
function validRegistryRequest(value: unknown): value is RegistryRequest {
  if (!exactObject(value, ["appId", "version", "routeId", "context", "effectiveCapabilities", "signal"], ["appId", "version", "routeId", "context", "effectiveCapabilities"])) return false;
  if (!ownNonEmpty(value.appId) || !ownNonEmpty(value.version) || !ownNonEmpty(value.routeId) || !(value.effectiveCapabilities instanceof Set)) return false;
  if ([...value.effectiveCapabilities].some((capability) => typeof capability !== "string" || !isCapabilityName(capability))) return false;
  const signal = value.signal;
  return signal === undefined || (typeof signal === "object" && signal !== null && typeof (signal as AbortSignal).aborted === "boolean" && typeof (signal as AbortSignal).addEventListener === "function");
}
function safeTraceId(value: unknown): string {
  try {
    if (!record(value)) return "unavailable";
    const contextDescriptor = Object.getOwnPropertyDescriptor(value, "context");
    if (!contextDescriptor || !("value" in contextDescriptor) || !record(contextDescriptor.value)) return "unavailable";
    const traceDescriptor = Object.getOwnPropertyDescriptor(contextDescriptor.value, "traceId");
    return traceDescriptor && "value" in traceDescriptor && typeof traceDescriptor.value === "string" && traceDescriptor.value.trim() ? traceDescriptor.value : "unavailable";
  } catch { return "unavailable"; }
}
function collectNodeIds(node: UiNode, ids: Set<string>): boolean {
  if (node.type === "stack" || node.type === "row" || node.type === "column") {
    if (node.id) ids.add(node.id);
    return node.children.every((child) => collectNodeIds(child, ids));
  }
  ids.add(node.id);
  return true;
}

export class SignedRegistryResolver {
  private cache?: CacheRecord;
  private generation = 0;
  private correlationSequence = 0;
  private readonly seenCorrelations = new Set<string>();
  constructor(private readonly fetchSnapshot: RegistryFetcher, private readonly verifySnapshot: SignedSnapshotVerifier, private readonly now: () => number = Date.now) {}

  invalidate(): void { this.generation += 1; this.cache = undefined; this.seenCorrelations.clear(); }
  onDisabled(): void { this.invalidate(); }
  onRollback(): void { this.invalidate(); }
  onGrantRevoked(): void { this.invalidate(); }
  onContextChanged(): void { this.invalidate(); }
  onLogout(): void { this.invalidate(); }

  private nextCorrelation(context: RequestContext): string {
    this.correlationSequence += 1;
    const nonce = new Uint8Array(16);
    globalThis.crypto.getRandomValues(nonce);
    const token = Array.from(nonce, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `registry.${this.correlationSequence}.${token}`;
  }

  private async verify(snapshot: SignedRegistrySnapshot, correlationId: string, context: RequestContext, signal?: AbortSignal): Promise<boolean> {
    try { return await this.verifySnapshot(snapshot, { correlationId, context, signal }); } catch { return false; }
  }

  private async resolveChecked(request: RegistryRequest): Promise<RegistryResolution> {
    let context: RequestContext;
    try { context = createRequestContext(request.context); } catch {
      return Object.freeze({ state: "denied", code: "missing_context", traceId: safeTraceId(request) });
    }
    const generation = this.generation;
    const scope = scopeKey(context);
    const correlationId = this.nextCorrelation(context);
    const appId = request.appId;
    const version = request.version;
    const routeId = request.routeId;
    const effectiveCapabilities = new Set(request.effectiveCapabilities);
    if (!context.requestId) return failure("missing_context", context);
    if ([...effectiveCapabilities].some((capability) => !isCapabilityName(capability))) return failure("validation_error", context);
    if (request.signal?.aborted) return failure("stale_revision", context);
    let cache = this.cache && this.cache.scopeKey === scope && this.cache.expiresAt > this.now() ? this.cache : undefined;
    if (!cache) {
      const priorCache = this.cache?.scopeKey === scope ? this.cache : undefined;
      if (priorCache && priorCache.snapshotExpiresAt <= this.now()) this.cache = undefined;
      const conditionalCache = priorCache && priorCache.snapshotExpiresAt > this.now() ? priorCache : undefined;
      let rawResponse: unknown;
      try { rawResponse = await this.fetchSnapshot({ etag: conditionalCache?.etag, correlationId, context, signal: request.signal }); }
      catch { return failure("stale_revision", context); }
      if (generation !== this.generation || request.signal?.aborted) return failure("stale_revision", context);
      let response: RegistryResponse;
      try {
        if (!validResponse(rawResponse)) return failure("quarantined", context);
        response = rawResponse;
      } catch { return failure("quarantined", context); }
      if (this.seenCorrelations.has(response.correlationId)) return failure("replay_detected", context);
      if (response.correlationId !== correlationId) return failure("correlation_mismatch", context);
      this.seenCorrelations.add(response.correlationId);
      if (this.seenCorrelations.size > 512) this.seenCorrelations.delete(this.seenCorrelations.values().next().value as string);
      if (response.notModified) {
        if (!conditionalCache || this.cache !== conditionalCache || response.etag !== conditionalCache.etag) return failure("stale_revision", context);
        if (conditionalCache.snapshotExpiresAt <= this.now()) return failure("stale_revision", context);
        const ageLimit = Math.min(conditionalCache.snapshot.ttlSeconds, response.maxAgeSeconds ?? conditionalCache.snapshot.ttlSeconds, CACHE_TTL_CAP_SECONDS);
        const renewedUntil = Math.min(conditionalCache.snapshotExpiresAt, this.now() + ageLimit * 1000);
        if (renewedUntil <= this.now()) return failure("stale_revision", context);
        cache = { ...conditionalCache, expiresAt: renewedUntil };
        if (generation !== this.generation || request.signal?.aborted || this.cache !== conditionalCache) return failure("stale_revision", context);
        this.cache = cache;
      } else {
        let snapshot: SignedRegistrySnapshot;
        try {
          if (!validSnapshot(response.snapshot)) return failure("quarantined", context);
          snapshot = deepFreeze(cloneJsonData(response.snapshot));
        } catch { return failure("quarantined", context); }
        const issuedAt = Date.parse(snapshot.issuedAt);
        const snapshotExpiresAt = issuedAt + snapshot.ttlSeconds * 1000;
        if (snapshot.signatureStatus !== "verified") return failure("quarantined", context);
        if (issuedAt > this.now()) return failure("quarantined", context);
        if (snapshotExpiresAt <= this.now()) return failure("stale_revision", context);
        if (!(await this.verify(snapshot, correlationId, context, request.signal))) return failure("quarantined", context);
        if (generation !== this.generation || request.signal?.aborted) return failure("stale_revision", context);
        if (this.cache && this.cache.scopeKey === scope) {
          const cachedIssuedAt = Date.parse(this.cache.snapshot.issuedAt);
          if (Number.isFinite(cachedIssuedAt) && (cachedIssuedAt > issuedAt || (cachedIssuedAt === issuedAt && this.cache.snapshot.digest !== snapshot.digest))) return failure("stale_revision", context);
        }
        const ttl = Math.min(snapshot.ttlSeconds, response.maxAgeSeconds ?? snapshot.ttlSeconds, CACHE_TTL_CAP_SECONDS);
        const expiresAt = Math.min(snapshotExpiresAt, this.now() + ttl * 1000);
        if (expiresAt <= this.now()) return failure("stale_revision", context);
        const current = this.cache?.scopeKey === scope ? this.cache : undefined;
        if (current) {
          const currentIssuedAt = Date.parse(current.snapshot.issuedAt);
          if (!Number.isFinite(currentIssuedAt) || currentIssuedAt > issuedAt || (currentIssuedAt === issuedAt && current.snapshot.digest !== snapshot.digest)) return failure("stale_revision", context);
        }
        cache = { snapshot, etag: response.etag, expiresAt, snapshotExpiresAt, scopeKey: scope, cacheKey: `${scope}|${snapshot.digest}|${snapshot.schemaVersion}` };
        this.cache = cache;
      }
    }
    if (!cache || cache.expiresAt <= this.now()) return failure("stale_revision", context);
    const snapshot = cache.snapshot;
    if (snapshot.signatureStatus !== "verified" || !(await this.verify(snapshot, correlationId, context, request.signal))) return failure("quarantined", context);
    const currentCache = this.cache?.scopeKey === scope ? this.cache : undefined;
    if (generation !== this.generation || request.signal?.aborted || !currentCache || currentCache.cacheKey !== cache.cacheKey || currentCache.etag !== cache.etag) return failure("stale_revision", context);
    const entry = snapshot.entries.find((candidate) => candidate.appId === appId && candidate.version === version);
    if (!entry) return failure("unknown_app", context);
    if (entry.status === "quarantined") return failure("quarantined", context);
    if (entry.status === "disabled" || entry.status === "revoked" || entry.status === "rolled_back") return failure("stale_revision", context);
    if (entry.status !== "published") return failure("invalid_app", context);
    if (entry.tenantId !== context.tenantId || (entry.cabinetId !== undefined && entry.cabinetId !== context.cabinetId) || (entry.projectId !== undefined && entry.projectId !== context.projectId)) return failure("capability_denied", context);
    if (entry.schemaVersion !== snapshot.schemaVersion || !DIGEST.test(entry.artifactDigest)) return failure("quarantined", context);
    const manifestResult = validateDeclarativeManifest(entry.manifest);
    if (!manifestResult.ok) return failure("validation_error", context);
    const manifest = manifestResult.manifest;
    if (manifest.appId !== entry.appId || manifest.version !== entry.version) return failure("quarantined", context);
    const requestedCapabilities = new Set(manifest.requestedCapabilities.map((capability) => capability.name));
    if (entry.grants.some((capability) => !requestedCapabilities.has(capability))) return failure("quarantined", context);
    if (entry.grants.some((capability) => !effectiveCapabilities.has(capability))) return failure("capability_denied", context);
    const uiEntry = manifestResult.manifest.entrypoints.ui;
    if (!uiEntry) return failure("invalid_app", context);
    const uiIds = new Set<string>();
    if (!collectNodeIds(uiEntry.root, uiIds)) return failure("quarantined", context);
    const candidate = entry.routes.find((route) => route.id === routeId);
    if (!candidate) return failure("unknown_route", context);
    if (candidate.signatureStatus !== "verified") return failure("quarantined", context);
    const routeValue: unknown = { id: candidate.id, path: candidate.path, title: candidate.title, root: candidate.root, requirements: candidate.requirements };
    if (!validateRoute(routeValue, uiIds) || !validateRouteRequirements(candidate.requirements)) return failure("quarantined", context);
    const route = deepFreeze({ id: candidate.id, path: candidate.path, title: candidate.title, root: candidate.root,
      requirements: { context: [...candidate.requirements.context], ...(candidate.requirements.capabilities === undefined ? {} : { capabilities: [...candidate.requirements.capabilities] }) } }) as ValidatedRoute;
    const manifestRoutes = uiEntry.routes ?? [];
    const manifestRoute = manifestRoutes.find((candidate) => candidate.id === route.id);
    if (!manifestRoute || !validateManifestRoute(manifestRoute, uiIds) || manifestRoute.path !== route.path || manifestRoute.title !== route.title || manifestRoute.root !== route.root) return failure("quarantined", context);
    const pluginArtifact = manifestResult.manifest.entrypoints.plugin?.artifact;
    if (pluginArtifact && entry.artifactDigest !== `sha256:${pluginArtifact.sha256.toLowerCase()}`) return failure("quarantined", context);
    const routeCapabilities = candidate.requirements.capabilities ?? [];
    if (routeCapabilities.some((capability) => !requestedCapabilities.has(capability) || !entry.grants.includes(capability) || !effectiveCapabilities.has(capability))) return failure("capability_denied", context);
    const decision = evaluateRoute(context, route, { appTenantId: entry.tenantId, appCabinetId: entry.cabinetId, appProjectId: entry.projectId, requirements: route.requirements, effectiveCapabilities });
    if (!decision.allowed) return failure(decision.code === "missing_context" ? "missing_context" : decision.code, context);
    return deepFreeze({ state: "resolved" as const, manifest, route: decision.route, digest: entry.artifactDigest, etag: cache.etag, cacheKey: cache.cacheKey, correlationId });
  }

  async resolve(request: RegistryRequest): Promise<RegistryResolution> {
    try {
      if (!validRegistryRequest(request)) return Object.freeze({ state: "invalid", code: "validation_error", traceId: safeTraceId(request) });
      return await this.resolveChecked(request);
    } catch {
      return Object.freeze({ state: "quarantined", code: "quarantined", traceId: safeTraceId(request) });
    }
  }
}
