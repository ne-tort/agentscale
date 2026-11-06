import { describe, expect, it, vi } from "vitest";
import { createRequestContext } from "./protocol";
import { evaluateRoute } from "./policy";
import { SignedRegistryResolver, type RegistryFetcher, type RegistryResponse, type SignedRegistrySnapshot } from "./registry";
import { validateDeclarativeManifest, type ValidatedRoute } from "./schema";

const manifest = { protocol: "1.0", appId: "demo-app", version: "1.2.0", displayName: "Demo", entrypoints: { ui: { kind: "declarative-ui", root: { type: "stack", id: "root", children: [{ type: "text", id: "label", props: { text: "Demo" } }] }, routes: [{ id: "main", path: "/demo", title: "Demo", root: "root" }] } }, requestedCapabilities: [{ name: "platform.commands", reason: "needed by app" }, { name: "platform.queries", reason: "requested only" }] };
const route: ValidatedRoute = { id: "main", path: "/demo", title: "Demo", root: "root", requirements: { context: ["tenantId", "cabinetId", "projectId", "sessionId"], capabilities: ["platform.commands"] } };
const issuedAt = "2026-10-20T00:00:00.000Z";
const signature = { algorithm: "Ed25519" as const, keyId: "registry-key-2026", value: "A".repeat(86) };
const snapshot: SignedRegistrySnapshot = { schemaVersion: "1.0", apiContractVersion: "2026-10", digest: `sha256:${"a".repeat(64)}`, signatureStatus: "verified", signature, issuedAt, ttlSeconds: 60, entries: [{ appId: "demo-app", version: "1.2.0", artifactDigest: `sha256:${"b".repeat(64)}`, schemaVersion: "1.0", status: "published", tenantId: "tenant-1", cabinetId: "cabinet-1", projectId: "project-1", manifest, routes: [{ ...route, signatureStatus: "verified" }], grants: ["platform.commands"] }] };
const context = () => createRequestContext({ tenantId: "tenant-1", cabinetId: "cabinet-1", projectId: "project-1", sessionId: "session-1", traceId: "trace-1", requestId: "request-1" });
const request = () => ({ appId: "demo-app", version: "1.2.0", routeId: "main", context: context(), effectiveCapabilities: new Set<string>(["platform.commands"]) });
const verifier = async () => true;
const resolverNow = () => Date.parse(issuedAt) + 1000;
const response = (correlationId: string, overrides: Partial<RegistryResponse> = {}): RegistryResponse => ({ snapshot, etag: '"v1"', correlationId, ...overrides });
const fetcher = (build: (correlationId: string) => RegistryResponse = (correlationId) => response(correlationId)): RegistryFetcher => async ({ correlationId }) => build(correlationId);

 describe("MIG-WEB-01 acceptance fixtures", () => {
  it("rejects unsupported versions, unsafe discriminants, extra fields, duplicate IDs, and unsafe routes", () => {
    expect(validateDeclarativeManifest({ ...manifest, protocol: "2.0" })).toMatchObject({ ok: false, code: "unsupported_version" });
    const bad = structuredClone(manifest); (bad.entrypoints.ui.root.children[0] as { type: string }).type = "html";
    expect(validateDeclarativeManifest(bad)).toMatchObject({ ok: false, code: "invalid_schema" });
    const duplicate = structuredClone(manifest); duplicate.entrypoints.ui.root.children.push({ type: "text", id: "label", props: { text: "Demo" } });
    expect(validateDeclarativeManifest(duplicate)).toMatchObject({ ok: false, code: "invalid_schema" });
    expect(validateDeclarativeManifest({ ...manifest, privileged: true })).toMatchObject({ ok: false, code: "invalid_schema" });
    const malformedRoute = { ...manifest, entrypoints: { ...manifest.entrypoints, ui: { ...manifest.entrypoints.ui, routes: [{ ...manifest.entrypoints.ui.routes[0], path: "/%2e%2e/admin" }] } } };
    expect(validateDeclarativeManifest(malformedRoute)).toMatchObject({ ok: false, code: "invalid_schema" });
  });

  it("copies and freezes request context and rejects missing required dimensions", () => {
    const input = { tenantId: "tenant-1", traceId: "trace-1", projectId: "project-1" };
    const immutable = createRequestContext(input);
    input.projectId = "other-project";
    expect(Object.isFrozen(immutable)).toBe(true);
    expect(immutable.projectId).toBe("project-1");
    expect(evaluateRoute(immutable, route, { appTenantId: "tenant-1", requirements: route.requirements, effectiveCapabilities: new Set(["platform.commands"]) })).toMatchObject({ allowed: false, code: "missing_context" });
    expect(() => createRequestContext({ ...context(), requestId: "bad id" })).toThrow("invalid_requestId");
  });

  it("requires a request ID before registry resolution and never uses a fallback route", async () => {
    const contextWithoutRequest = createRequestContext({ tenantId: "tenant-1", traceId: "trace-1", sessionId: "session-1" });
    const result = await new SignedRegistryResolver(fetcher(), verifier, resolverNow).resolve({ ...request(), context: contextWithoutRequest });
    expect(result).toMatchObject({ state: "denied", code: "missing_context" });
    expect(await new SignedRegistryResolver(fetcher(), verifier, resolverNow).resolve({ ...request(), routeId: "shell-admin" })).toMatchObject({ state: "unknown_route", code: "unknown_route" });
  });

  it("denies route scope mismatch and manifest-request-as-grant", async () => {
    const mismatch = evaluateRoute(context(), route, { appTenantId: "tenant-1", appCabinetId: "other-cabinet", requirements: route.requirements, effectiveCapabilities: new Set(["platform.commands"]) });
    expect(mismatch).toMatchObject({ allowed: false, code: "route_scope_mismatch" });
    const noCapabilitiesDenied = await new SignedRegistryResolver(fetcher(), verifier, resolverNow).resolve({ ...request(), effectiveCapabilities: new Set() });
    expect(noCapabilitiesDenied).toMatchObject({ state: "denied", code: "capability_denied" });
    const requestedOnly = await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], grants: ["platform.queries"] }] } })), verifier, resolverNow).resolve({ ...request(), effectiveCapabilities: new Set(["platform.commands", "platform.queries"]) });
    expect(requestedOnly).toMatchObject({ state: "denied", code: "capability_denied" });
    const capabilityGranted = await new SignedRegistryResolver(fetcher(), verifier, resolverNow).resolve({ ...request(), effectiveCapabilities: new Set(["platform.commands"]) });
    expect(capabilityGranted).toMatchObject({ state: "resolved" });
    const runtimeGrantDenied = await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], grants: ["platform.commands", "platform.queries"] }] } })), verifier, resolverNow).resolve({ ...request(), effectiveCapabilities: new Set(["platform.commands"]) });
    expect(runtimeGrantDenied).toMatchObject({ state: "denied", code: "capability_denied" });
  });

  it("quarantines malformed and overbroad registry grants", async () => {
    const malformedGrant = await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], grants: ["admin.superuser"] }] } })), verifier, resolverNow).resolve(request());
    expect(malformedGrant).toMatchObject({ state: "quarantined", code: "quarantined" });
    const overbroadGrant = await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], grants: ["platform.commands", "storage.kv"] }] } })), verifier, resolverNow).resolve({ ...request(), effectiveCapabilities: new Set(["platform.commands", "storage.kv"]) });
    expect(overbroadGrant).toMatchObject({ state: "quarantined", code: "quarantined" });
  });

  it("requires a valid signed snapshot and digest verification before resolution", async () => {
    const badSignature = { ...snapshot, signatureStatus: "missing" as const };
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: badSignature })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "quarantined", code: "quarantined" });
    const malformedSignature = { ...snapshot, signature: { ...signature, value: "invalid" } };
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: malformedSignature })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "quarantined" });
    const badDigest = { ...snapshot, digest: "sha256:invalid" } as unknown as SignedRegistrySnapshot;
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: badDigest })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "quarantined" });
    expect(await new SignedRegistryResolver(fetcher(), async () => false, resolverNow).resolve(request())).toMatchObject({ state: "quarantined" });
    const verify = vi.fn(verifier);
    expect(await new SignedRegistryResolver(fetcher(), verify, resolverNow).resolve(request())).toMatchObject({ state: "resolved" });
    expect(verify).toHaveBeenCalledWith(expect.objectContaining({ digest: snapshot.digest, signature }), expect.objectContaining({ correlationId: expect.any(String) }));
  });

  it("resolves a verified pinned route and uses ETag only inside declared TTL", async () => {
    let now = resolverNow(), calls = 0;
    const fetch: RegistryFetcher = async ({ etag, correlationId }) => { calls += 1; return etag ? response(correlationId, { snapshot: undefined, notModified: true, maxAgeSeconds: 15 }) : response(correlationId, { maxAgeSeconds: 15 }); };
    const resolver = new SignedRegistryResolver(fetch, verifier, () => now);
    const first = await resolver.resolve(request());
    expect(first).toMatchObject({ state: "resolved", digest: snapshot.entries[0].artifactDigest, etag: '"v1"' });
    expect(first.state === "resolved" && Object.isFrozen(first.manifest.entrypoints.ui?.root)).toBe(true);
    expect(await resolver.resolve(request())).toMatchObject({ state: "resolved" });
    expect(calls).toBe(1);
    now += 30_000;
    const revalidated = await resolver.resolve(request());
    expect(revalidated).toMatchObject({ state: "resolved" });
    expect(calls).toBe(2);
    now += 15_000;
    const cappedAtSnapshotExpiry = await resolver.resolve(request());
    expect(cappedAtSnapshotExpiry).toMatchObject({ state: "resolved" });
    expect(calls).toBe(3);
    now += 30_000;
    const expiredSnapshot = await resolver.resolve(request());
    expect(expiredSnapshot).toMatchObject({ state: "stale", code: "stale_revision" });
    expect(calls).toBe(4);
  });

  it("rejects orphan and expired 304 responses without reviving expired content", async () => {
    const orphan = new SignedRegistryResolver(async ({ correlationId }) => response(correlationId, { snapshot: undefined, notModified: true }), verifier, resolverNow);
    expect(await orphan.resolve(request())).toMatchObject({ state: "stale", code: "stale_revision", traceId: "trace-1" });
    let now = resolverNow();
    const cached = new SignedRegistryResolver(fetcher(), verifier, () => now);
    expect(await cached.resolve(request())).toMatchObject({ state: "resolved" });
    now = Date.parse(issuedAt) + 60_001;
    expect(await cached.resolve(request())).toMatchObject({ state: "stale", code: "stale_revision" });
  });

  it("rejects response correlation mismatch and discards invalidated in-flight responses", async () => {
    const mismatch = new SignedRegistryResolver(async () => response("trace-other.request-other.1"), verifier, resolverNow);
    expect(await mismatch.resolve(request())).toMatchObject({ state: "quarantined", code: "correlation_mismatch" });
    let release!: (value: RegistryResponse) => void;
    const pending = new SignedRegistryResolver(({ correlationId }) => new Promise((resolve) => { release = () => resolve(response(correlationId)); }), verifier, resolverNow);
    const resolving = pending.resolve(request());
    pending.onContextChanged();
    release(response("trace-1.request-1.1"));
    expect(await resolving).toMatchObject({ state: "stale", code: "stale_revision", traceId: "trace-1" });
  });

  it("rejects a repeated response correlation even when the next request has a new ID", async () => {
    let mode = "normal";
    let previousCorrelation = "";
    const fetch: RegistryFetcher = async ({ correlationId }) => {
      if (mode === "replay") return response(previousCorrelation);
      previousCorrelation = correlationId;
      return response(correlationId);
    };
    const resolver = new SignedRegistryResolver(fetch, verifier, resolverNow);
    expect(await resolver.resolve(request())).toMatchObject({ state: "resolved" });
    mode = "replay";
    const repeated = await resolver.resolve({ ...request(), context: createRequestContext({ tenantId: "tenant-1", cabinetId: "cabinet-2", projectId: "project-1", sessionId: "session-1", traceId: "trace-1", requestId: "request-2" }) });
    expect(repeated).toMatchObject({ state: "quarantined", code: "replay_detected" });
  });

  it("exposes explicit unknown, invalid, stale, denied, and quarantined states", async () => {
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [] } })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "unknown_app", code: "unknown_app" });
    expect(await new SignedRegistryResolver(fetcher(), verifier, resolverNow).resolve({ ...request(), routeId: "missing" })).toMatchObject({ state: "unknown_route", code: "unknown_route" });
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], status: "disabled" }] } })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "stale", code: "stale_revision" });
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], manifest: { protocol: "2.0" } }] } })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "invalid", code: "validation_error" });
    expect(await new SignedRegistryResolver(fetcher((id) => response(id, { snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], status: "quarantined" }] } })), verifier, resolverNow).resolve(request())).toMatchObject({ state: "quarantined", code: "quarantined" });
  });
});
