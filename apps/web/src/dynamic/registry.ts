import type { DeclarativeAppManifest, UiNode } from "../../../../packages/schemas/declarative-app/v1";
import { deepFreeze, validateDeclarativeManifest, validateManifestRoute, validateRouteRequirements, type ValidatedRoute } from "./schema";
import type { ArtifactError, RequestContext } from "./protocol";
import { evaluateRoute } from "./policy";

export interface SignedRegistryEntry {
  readonly appId: string;
  readonly version: string;
  readonly artifactDigest: string;
  readonly schemaVersion: "1.0";
  readonly status: "published" | "disabled" | "rolled_back";
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
  readonly issuedAt: string;
  readonly ttlSeconds: number;
  readonly entries: readonly SignedRegistryEntry[];
}
export interface RegistryResponse {
  readonly snapshot?: SignedRegistrySnapshot;
  readonly etag: string;
  readonly notModified?: boolean;
  readonly maxAgeSeconds?: number;
}
export type RegistryFetcher = (input: { readonly etag?: string; readonly context: RequestContext; readonly signal?: AbortSignal }) => Promise<RegistryResponse>;
export interface RegistryRequest {
  readonly appId: string;
  readonly version: string;
  readonly routeId: string;
  readonly context: RequestContext;
  readonly effectiveCapabilities: ReadonlySet<string>;
  readonly signal?: AbortSignal;
}
export type RegistryResolution =
  | { readonly state: "resolved"; readonly manifest: DeclarativeAppManifest; readonly route: ValidatedRoute; readonly digest: string; readonly etag: string; readonly cacheKey: string }
  | ArtifactError;
interface CacheRecord { readonly snapshot: SignedRegistrySnapshot; readonly etag: string; readonly expiresAt: number; readonly firstSeenAt: number; readonly scopeKey: string; readonly cacheKey: string }

function scopeKey(context: RequestContext): string {
  return [context.tenantId, context.cabinetId ?? "-", context.projectId ?? "-", context.sessionId ?? "-"].join("|");
}
function error(code: ArtifactError["code"], context: RequestContext): ArtifactError {
  return Object.freeze({ state: "error", code, traceId: context.traceId });
}
function validDigest(value: string): boolean { return /^sha256:[a-f\d]{64}$/i.test(value); }
function isCapabilityName(value: string): boolean {
  return ["ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch", "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read"].includes(value);
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
  constructor(private readonly fetchSnapshot: RegistryFetcher, private readonly verifyDigest: (snapshot: SignedRegistrySnapshot) => Promise<boolean>, private readonly now: () => number = Date.now) {}

  invalidate(): void { this.generation += 1; this.cache = undefined; }
  onDisabled(): void { this.invalidate(); }
  onRollback(): void { this.invalidate(); }
  onGrantRevoked(): void { this.invalidate(); }
  onContextChanged(): void { this.invalidate(); }
  onLogout(): void { this.invalidate(); }

  async resolve(request: RegistryRequest): Promise<RegistryResolution> {
    const generation = this.generation;
    const scope = scopeKey(request.context);
    if (request.context.sessionId === undefined) return error("missing_context", request.context);
    if (!request.appId || !request.version || !request.routeId) return error("validation_error", request.context);
    let cache = this.cache && this.cache.scopeKey === scope && this.cache.expiresAt > this.now() ? this.cache : undefined;
    if (!cache) {
      const conditionalCache = this.cache?.scopeKey === scope ? this.cache : undefined;
      const response = await this.fetchSnapshot({ etag: conditionalCache?.etag, context: request.context, signal: request.signal });
      if (generation !== this.generation) return error("stale_revision", request.context);
      if (request.signal?.aborted) return error("stale_revision", request.context);
      if (typeof response.etag !== "string" || !response.etag.trim() || response.etag.length > 256) return error("invalid_app", request.context);
      if (response.notModified) {
        if (!conditionalCache || response.etag !== conditionalCache.etag || conditionalCache.firstSeenAt + conditionalCache.snapshot.ttlSeconds * 1000 <= this.now()) return error("stale_revision", request.context);
        const ageLimit = Math.min(conditionalCache.snapshot.ttlSeconds, response.maxAgeSeconds ?? conditionalCache.snapshot.ttlSeconds, 3600);
        const renewed = { ...conditionalCache, expiresAt: Math.min(conditionalCache.firstSeenAt + conditionalCache.snapshot.ttlSeconds * 1000, this.now() + Math.max(0, ageLimit) * 1000) };
        this.cache = renewed;
        cache = renewed;
      } else {
        const snapshot = response.snapshot;
        if (!snapshot || snapshot.schemaVersion !== "1.0" || snapshot.signatureStatus !== "verified" || !validDigest(snapshot.digest) || typeof snapshot.apiContractVersion !== "string" || !snapshot.apiContractVersion.trim() || !Number.isFinite(Date.parse(snapshot.issuedAt)) || Date.parse(snapshot.issuedAt) > this.now() || !Number.isInteger(snapshot.ttlSeconds) || snapshot.ttlSeconds < 1 || !Array.isArray(snapshot.entries)) return error("invalid_app", request.context);
        const identities = new Set<string>();
        for (const candidate of snapshot.entries) {
          if (!candidate || typeof candidate.appId !== "string" || typeof candidate.version !== "string") return error("invalid_app", request.context);
          const identity = `${candidate.appId}\u0000${candidate.version}`;
          if (identities.has(identity)) return error("invalid_app", request.context);
          identities.add(identity);
        }
        if (!(await this.verifyDigest(snapshot))) return error("invalid_app", request.context);
        if (generation !== this.generation || request.signal?.aborted) return error("stale_revision", request.context);
        const ttl = Math.max(1, Math.min(snapshot.ttlSeconds, response.maxAgeSeconds ?? snapshot.ttlSeconds, 3600));
        cache = { snapshot: deepFreeze(snapshot), etag: response.etag, expiresAt: this.now() + ttl * 1000, firstSeenAt: this.now(), scopeKey: scope, cacheKey: `${scope}|${snapshot.digest}|${snapshot.schemaVersion}` };
        this.cache = cache;
      }
    }
    if (!cache || cache.expiresAt <= this.now()) return error("stale_revision", request.context);
    const snapshot = cache.snapshot;
    if (snapshot.signatureStatus !== "verified" || !validDigest(snapshot.digest) || !(await this.verifyDigest(snapshot))) return error("invalid_app", request.context);
    if (generation !== this.generation || request.signal?.aborted) return error("stale_revision", request.context);
    const entry = snapshot.entries.find((candidate) => candidate.appId === request.appId && candidate.version === request.version);
    if (!entry) return error("unknown_app", request.context);
    if (entry.status === "disabled" || entry.status === "rolled_back") return error("stale_revision", request.context);
    if (entry.status !== "published") return error("invalid_app", request.context);
    if (entry.tenantId !== request.context.tenantId || (entry.cabinetId !== undefined && entry.cabinetId !== request.context.cabinetId) || (entry.projectId !== undefined && entry.projectId !== request.context.projectId)) return error("capability_denied", request.context);
    if (entry.schemaVersion !== snapshot.schemaVersion || !validDigest(entry.artifactDigest) || !Array.isArray(entry.routes) || !Array.isArray(entry.grants)) return error("invalid_app", request.context);
    const manifestResult = validateDeclarativeManifest(entry.manifest);
    if (!manifestResult.ok || manifestResult.manifest.appId !== entry.appId || manifestResult.manifest.version !== entry.version) return error("validation_error", request.context);
    if (entry.grants.some((grant) => typeof grant !== "string" || !isCapabilityName(grant)) || !entry.grants.every((grant) => request.effectiveCapabilities.has(grant))) return error("capability_denied", request.context);
    const routeValue = entry.routes.find((candidate) => candidate?.id === request.routeId);
    if (!routeValue) return error("unknown_route", request.context);
    const uiEntry = manifestResult.manifest.entrypoints.ui;
    const uiIds = new Set<string>();
    if (!uiEntry || !collectNodeIds(uiEntry.root, uiIds) || routeValue.signatureStatus !== "verified") return error("invalid_app", request.context);
    const routeFields: unknown = { id: routeValue.id, path: routeValue.path, title: routeValue.title, root: routeValue.root };
    if (!validateManifestRoute(routeFields, uiIds) || !validateRouteRequirements(routeValue.requirements)) return error("invalid_app", request.context);
    const route = deepFreeze({ ...routeFields, requirements: deepFreeze({ context: [...routeValue.requirements.context], ...(routeValue.requirements.capabilities === undefined ? {} : { capabilities: [...routeValue.requirements.capabilities] }) }) }) as ValidatedRoute;
    const routeCapabilities = route.requirements.capabilities ?? [];
    const manifestRoutes = uiEntry.routes ?? [];
    const manifestRoute = manifestRoutes.find((candidate) => candidate.id === route.id);
    if (!manifestRoute || manifestRoute.path !== route.path || manifestRoute.title !== route.title || manifestRoute.root !== route.root) return error("invalid_app", request.context);
    const pluginArtifact = manifestResult.manifest.entrypoints.plugin?.artifact;
    if (pluginArtifact && entry.artifactDigest.toLowerCase() !== `sha256:${pluginArtifact.sha256.toLowerCase()}`) return error("invalid_app", request.context);
    if (routeCapabilities.some((capability) => !entry.grants.includes(capability))) return error("capability_denied", request.context);
    const decision = evaluateRoute(request.context, route, { appTenantId: entry.tenantId, appCabinetId: entry.cabinetId, appProjectId: entry.projectId, requirements: route.requirements, effectiveCapabilities: request.effectiveCapabilities });
    if (!decision.allowed) return error(decision.code === "missing_context" ? "missing_context" : decision.code, request.context);
    return deepFreeze({ state: "resolved" as const, manifest: manifestResult.manifest, route: decision.route, digest: entry.artifactDigest, etag: cache.etag, cacheKey: cache.cacheKey });
  }
}
