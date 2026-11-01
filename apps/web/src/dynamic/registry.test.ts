import { describe, expect, it } from "vitest";
import { createRequestContext } from "./protocol";
import { evaluateRoute } from "./policy";
import { SignedRegistryResolver, type RegistryFetcher, type SignedRegistrySnapshot } from "./registry";
import { validateDeclarativeManifest, type ValidatedRoute } from "./schema";

const manifest = { protocol: "1.0", appId: "demo-app", version: "1.2.0", displayName: "Demo", entrypoints: { ui: { kind: "declarative-ui", root: { type: "stack", id: "root", children: [{ type: "text", id: "label", props: { text: "Demo" } }] }, routes: [{ id: "main", path: "/demo", title: "Demo", root: "root" }] } }, requestedCapabilities: [{ name: "platform.commands", reason: "needed by app" }, { name: "platform.queries", reason: "requested only" }] };
const route: ValidatedRoute = { id: "main", path: "/demo", title: "Demo", root: "root", requirements: { context: ["tenantId", "cabinetId", "projectId", "sessionId"], capabilities: ["platform.commands"] } };
const snapshot: SignedRegistrySnapshot = { schemaVersion: "1.0", apiContractVersion: "2026-10", digest: `sha256:${"a".repeat(64)}`, signatureStatus: "verified", issuedAt: "2026-10-20T00:00:00Z", ttlSeconds: 60, entries: [{ appId: "demo-app", version: "1.2.0", artifactDigest: `sha256:${"b".repeat(64)}`, schemaVersion: "1.0", status: "published", tenantId: "tenant-1", cabinetId: "cabinet-1", projectId: "project-1", manifest, routes: [{ ...route, signatureStatus: "verified" }], grants: ["platform.commands"] }] };
const context = () => createRequestContext({ tenantId: "tenant-1", cabinetId: "cabinet-1", projectId: "project-1", sessionId: "session-1", traceId: "trace-1", requestId: "request-1" });
const request = () => ({ appId: "demo-app", version: "1.2.0", routeId: "main", context: context(), effectiveCapabilities: new Set<string>(["platform.commands"]) });
const fetcher = (answer: { snapshot?: SignedRegistrySnapshot; etag: string; notModified?: boolean } = { snapshot, etag: '"v1"' }): RegistryFetcher => async () => answer;

 describe("MIG-WEB-01 acceptance fixtures", () => {
  it("rejects unsupported protocol versions, unsafe discriminants, and malformed manifest routes", () => {
    expect(validateDeclarativeManifest({ ...manifest, protocol: "2.0" })).toMatchObject({ ok: false, code: "unsupported_version" });
    const bad = structuredClone(manifest); (bad.entrypoints.ui.root.children[0] as { type: string }).type = "html";
    expect(validateDeclarativeManifest(bad)).toMatchObject({ ok: false, code: "invalid_schema" });
    const duplicate = structuredClone(manifest); duplicate.entrypoints.ui.root.children.push({ type: "text", id: "label", props: {} });
    expect(validateDeclarativeManifest(duplicate)).toMatchObject({ ok: false, code: "invalid_schema" });
    const malformedRoute = { ...manifest, entrypoints: { ...manifest.entrypoints, ui: { ...manifest.entrypoints.ui, routes: [{ ...manifest.entrypoints.ui.routes[0], requirements: { context: ["tenant", "projectId"] } }] } } };
    expect(validateDeclarativeManifest(malformedRoute)).toMatchObject({ ok: false, code: "invalid_schema" });
  });

  it("creates an immutable request snapshot and rejects missing required dimensions", () => {
    const input = { tenantId: "tenant-1", traceId: "trace-1", projectId: "project-1" };
    const immutable = createRequestContext(input);
    expect(Object.isFrozen(immutable)).toBe(true);
    expect(evaluateRoute(immutable, route, { appTenantId: "tenant-1", requirements: route.requirements, effectiveCapabilities: new Set(["orders.read"]) })).toMatchObject({ allowed: false, code: "missing_context" });
  });

  it("denies route scope mismatch and manifest-request-as-grant", async () => {
    const mismatch = evaluateRoute(context(), route, { appTenantId: "tenant-1", appCabinetId: "other-cabinet", requirements: route.requirements, effectiveCapabilities: new Set(["platform.commands"]) });
    expect(mismatch).toMatchObject({ allowed: false, code: "route_scope_mismatch" });
    const denied = await new SignedRegistryResolver(fetcher(), async () => true).resolve({ ...request(), effectiveCapabilities: new Set() });
    expect(denied).toMatchObject({ state: "error", code: "capability_denied" });
    const requestedOnly = await new SignedRegistryResolver(fetcher({ snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], grants: ["platform.commands"] }] }, etag: '"requested-only"' }), async () => true).resolve({ ...request(), effectiveCapabilities: new Set(["platform.commands", "platform.queries"]) });
    expect(requestedOnly).toMatchObject({ state: "resolved" });
    const mismatchedRouteGrant = await new SignedRegistryResolver(fetcher({ snapshot: { ...snapshot, entries: [{ ...snapshot.entries[0], grants: [] }] }, etag: '"route-denied"' }), async () => true).resolve({ ...request(), effectiveCapabilities: new Set(["platform.commands"]) });
    expect(mismatchedRouteGrant).toMatchObject({ state: "error", code: "capability_denied" });
  });

  it("requires verified snapshot signature and digest before resolution", async () => {
    const badSignature = { ...snapshot, signatureStatus: "missing" as const };
    expect(await new SignedRegistryResolver(fetcher({ snapshot: badSignature, etag: '"bad"' }), async () => true).resolve(request())).toMatchObject({ state: "error", code: "invalid_app" });
    const badDigest = { ...snapshot, digest: "sha256:invalid" } as SignedRegistrySnapshot;
    expect(await new SignedRegistryResolver(fetcher({ snapshot: badDigest, etag: '"bad"' }), async () => true).resolve(request())).toMatchObject({ state: "error", code: "invalid_app" });
    expect(await new SignedRegistryResolver(fetcher(), async () => false).resolve(request())).toMatchObject({ state: "error", code: "invalid_app" });
  });

  it("resolves a verified pinned route and uses ETag only inside declared TTL", async () => {
    let now = 1000, calls = 0;
    const fetch: RegistryFetcher = async ({ etag }) => { calls += 1; return etag ? { etag, notModified: true, maxAgeSeconds: 30 } : { snapshot, etag: '"v1"', maxAgeSeconds: 45 }; };
    const resolver = new SignedRegistryResolver(fetch, async () => true, () => now);
    expect(await resolver.resolve(request())).toMatchObject({ state: "resolved", digest: snapshot.entries[0].artifactDigest, etag: '"v1"' });
    expect(await resolver.resolve(request())).toMatchObject({ state: "resolved" });
    expect(calls).toBe(1);
    now += 46_000;
    expect(await resolver.resolve(request())).toMatchObject({ state: "resolved" });
    expect(calls).toBe(2);
  });

  it("rejects an orphan 304 and discards an invalidated in-flight response (replay correlation)", async () => {
    const orphan = new SignedRegistryResolver(async () => ({ etag: '"old"', notModified: true }), async () => true);
    expect(await orphan.resolve(request())).toMatchObject({ state: "error", code: "stale_revision", traceId: "trace-1" });
    let release!: (value: { snapshot: SignedRegistrySnapshot; etag: string }) => void;
    const pending = new SignedRegistryResolver(() => new Promise((resolve) => { release = resolve; }), async () => true);
    const resolving = pending.resolve(request());
    pending.onContextChanged();
    release({ snapshot, etag: '"replay"' });
    expect(await resolving).toMatchObject({ state: "error", code: "stale_revision", traceId: "trace-1" });
  });
});
