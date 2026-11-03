import { describe, expect, it, vi } from "vitest";
import { createDynamicRegistryController, type RegistryClientRequest } from "./controller";
import { routeToSnapshotPointer } from "./route-adapter";
import type { RegistryResponse, RegistryFetcher, SignedRegistrySnapshot } from "../registry";

const issuedAt = "2026-10-20T00:00:00.000Z";
const manifest = {
  protocol: "1.0",
  appId: "demo-app",
  version: "1.2.0",
  displayName: "Demo",
  entrypoints: {
    ui: {
      kind: "declarative-ui",
      root: { type: "stack", id: "root", children: [{ type: "text", id: "label", props: { text: "Demo" } }] },
      routes: [{ id: "main", path: "/demo", title: "Demo", root: "root" }],
    },
  },
  requestedCapabilities: [{ name: "platform.commands", reason: "Requested by demo app" }],
};
const route = { id: "main", path: "/demo", title: "Demo", root: "root", requirements: { context: ["tenantId"], capabilities: ["platform.commands"] }, signatureStatus: "verified" as const };
const snapshot = {
  schemaVersion: "1.0",
  apiContractVersion: "2026-10",
  digest: `sha256:${"a".repeat(64)}`,
  signatureStatus: "verified",
  signature: { algorithm: "Ed25519", keyId: "registry-key", value: "A".repeat(86) },
  issuedAt,
  ttlSeconds: 60,
  entries: [{
    appId: "demo-app",
    version: "1.2.0",
    artifactDigest: `sha256:${"b".repeat(64)}`,
    schemaVersion: "1.0",
    status: "published",
    tenantId: "tenant-1",
    manifest,
    routes: [route],
    grants: ["platform.commands"],
  }],
} as unknown as SignedRegistrySnapshot;

function request(overrides: Partial<RegistryClientRequest> = {}): RegistryClientRequest {
  return {
    appId: "demo-app",
    version: "1.2.0",
    routeId: "main",
    context: { tenantId: "tenant-1", traceId: "trace-1", requestId: "request-0001" },
    effectiveCapabilities: new Set(["platform.commands"]),
    ...overrides,
  };
}

function response(correlationId: string, overrides: Partial<RegistryResponse> = {}): RegistryResponse {
  return { snapshot, etag: '"registry-v1"', correlationId, ...overrides };
}

function fetcher(build?: (correlationId: string, etag?: string) => RegistryResponse): RegistryFetcher {
  return async ({ correlationId, etag }) => build?.(correlationId, etag) ?? response(correlationId);
}

function controller(fetchSnapshot: RegistryFetcher, now = () => Date.parse(issuedAt) + 1000) {
  return createDynamicRegistryController({ fetchSnapshot, verifySnapshot: async () => true, now });
}

describe("DynamicRegistryController", () => {
  it("uses ETag revalidation only after the cached max-age and honors the snapshot expiry", async () => {
    let now = Date.parse(issuedAt) + 1000;
    const calls: Array<string | undefined> = [];
    const fetch: RegistryFetcher = async ({ correlationId, etag }) => {
      calls.push(etag);
      return etag
        ? response(correlationId, { snapshot: undefined, notModified: true, maxAgeSeconds: 20 })
        : response(correlationId, { maxAgeSeconds: 1 });
    };
    const client = controller(fetch, () => now);

    expect(await client.resolve(request())).toMatchObject({ state: "resolved", etag: '"registry-v1"' });
    expect(await client.resolve(request())).toMatchObject({ state: "resolved" });
    expect(calls).toEqual([undefined]);
    now += 1001;
    expect(await client.resolve(request())).toMatchObject({ state: "resolved" });
    expect(calls).toEqual([undefined, '"registry-v1"']);
    now = Date.parse(issuedAt) + 60_001;
    expect(await client.resolve(request())).toMatchObject({ state: "stale", code: "stale_revision" });
    expect(calls).toEqual([undefined, '"registry-v1"', undefined]);
  });

  it("copies and freezes request context before awaiting the transport", async () => {
    let release!: (value: RegistryResponse) => void;
    let receivedContext: unknown;
    const pending = new Promise<RegistryResponse>((resolve) => { release = resolve; });
    const fetch: RegistryFetcher = async ({ context, correlationId }) => {
      receivedContext = context;
      return pending.then(() => response(correlationId));
    };
    const client = controller(fetch);
    const mutableContext = { tenantId: "tenant-1", traceId: "trace-1", requestId: "request-0001" };
    const resolving = client.resolve({ ...request(), context: mutableContext });
    mutableContext.tenantId = "tenant-other";
    release(response("transport-correlation-is-ignored"));
    expect(await resolving).toMatchObject({ state: "resolved" });
    expect(receivedContext).toMatchObject({ tenantId: "tenant-1" });
    expect(Object.isFrozen(receivedContext)).toBe(true);
  });

  it("does not reuse a tenant snapshot cache for another request context", async () => {
    const requests: Array<{ readonly tenantId: string; readonly cabinetId?: string }> = [];
    const fetch: RegistryFetcher = async ({ context, correlationId }) => {
      requests.push(context);
      return response(correlationId);
    };
    const client = controller(fetch);
    expect(await client.resolve(request())).toMatchObject({ state: "resolved" });
    expect(await client.resolve(request({ context: { tenantId: "tenant-1", cabinetId: "cabinet-2", traceId: "trace-1", requestId: "request-0002" } })))
      .toMatchObject({ state: "resolved" });
    expect(requests).toHaveLength(2);
    expect(requests.map(({ cabinetId }) => cabinetId)).toEqual([undefined, "cabinet-2"]);
  });

  it("discards a response invalidated by rollback while the request is in flight", async () => {
    let release!: (value: RegistryResponse) => void;
    const client = controller(async ({ correlationId }) => new Promise((resolve) => { release = () => resolve(response(correlationId)); }));
    const resolving = client.resolve(request());
    client.onRollback();
    release(response("late-response"));
    expect(await resolving).toMatchObject({ state: "stale", code: "stale_revision" });
  });

  it("requires effective capability separately from the manifest request", async () => {
    const client = controller(fetcher());
    expect(await client.resolve(request({ effectiveCapabilities: new Set() })))
      .toMatchObject({ state: "denied", code: "capability_denied" });

    const noGrantSnapshot = {
      ...snapshot,
      entries: [{ ...snapshot.entries[0], grants: [] }],
    } as unknown as SignedRegistrySnapshot;
    const deniedGrant = controller(fetcher((correlationId) => response(correlationId, { snapshot: noGrantSnapshot })));
    expect(await deniedGrant.resolve(request())).toMatchObject({ state: "denied", code: "capability_denied" });

    const runtimeGrantSnapshot = {
      ...snapshot,
      entries: [{ ...snapshot.entries[0], manifest: { ...manifest, requestedCapabilities: [...manifest.requestedCapabilities, { name: "platform.queries", reason: "Requested for controller coverage" }] }, grants: ["platform.commands", "platform.queries"] }],
    } as unknown as SignedRegistrySnapshot;
    const missingRuntimeGrant = controller(fetcher((correlationId) => response(correlationId, { snapshot: runtimeGrantSnapshot })));
    expect(await missingRuntimeGrant.resolve(request())).toMatchObject({ state: "denied", code: "capability_denied" });
  });

  it("keeps invalid, expired, and quarantined registry states explicit", async () => {
    const invalid = { ...snapshot, signatureStatus: "missing" } as SignedRegistrySnapshot;
    const invalidClient = controller(fetcher((correlationId) => response(correlationId, { snapshot: invalid })));
    expect(await invalidClient.resolve(request())).toMatchObject({ state: "quarantined", code: "quarantined" });

    const expiredClient = controller(fetcher(), () => Date.parse(issuedAt) + 60_001);
    expect(await expiredClient.resolve(request())).toMatchObject({ state: "stale", code: "stale_revision" });

    const quarantined = { ...snapshot, entries: [{ ...snapshot.entries[0], status: "quarantined" }] } as unknown as SignedRegistrySnapshot;
    const quarantinedClient = controller(fetcher((correlationId) => response(correlationId, { snapshot: quarantined })));
    expect(await quarantinedClient.resolve(request())).toMatchObject({ state: "quarantined", code: "quarantined" });
  });

  it("resolves a route pointer only through the signed resolver and reports ambiguous paths", async () => {
    expect(routeToSnapshotPointer(snapshot, "/demo")).toMatchObject({ state: "match", pointer: { appId: "demo-app", version: "1.2.0", routeId: "main" } });
    expect(routeToSnapshotPointer(snapshot, "/%2e%2e/admin")).toMatchObject({ state: "invalid_path" });
    expect(routeToSnapshotPointer({ entries: [...snapshot.entries, snapshot.entries[0]] }, "/demo")).toMatchObject({ state: "ambiguous" });

    const fetch = vi.fn(fetcher());
    const client = controller(fetch);
    const result = await client.resolveRoute({
      pathname: "/demo",
      snapshotHint: snapshot,
      context: request().context,
      effectiveCapabilities: new Set(["platform.commands"]),
    });
    expect(result).toMatchObject({ state: "resolved", route: { path: "/demo" } });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("maps invalid and ambiguous route paths to explicit failure states", async () => {
    const duplicate = { entries: [...snapshot.entries, { ...snapshot.entries[0], appId: "other-app" }] };
    const client = controller(fetcher());
    expect(await client.resolveRoute({ pathname: "/demo", snapshotHint: duplicate, context: request().context, effectiveCapabilities: new Set(["platform.commands"]) }))
      .toMatchObject({ state: "ambiguous_route", code: "ambiguous_route" });
    expect(await client.resolveRoute({ pathname: "//admin", snapshotHint: snapshot, context: request().context, effectiveCapabilities: new Set() }))
      .toMatchObject({ state: "invalid", code: "validation_error" });

    const throwingHint = new Proxy({}, { get() { throw new Error("untrusted route hint"); } });
    expect(routeToSnapshotPointer(throwingHint, "/demo")).toMatchObject({ state: "invalid_snapshot" });
  });

  it("propagates cancellation and returns stale instead of exposing a late resolution", async () => {
    let release!: (value: RegistryResponse) => void;
    const client = controller(async ({ correlationId }) => new Promise((resolve) => { release = () => resolve(response(correlationId)); }));
    const abort = new AbortController();
    const resolving = client.resolve({ ...request(), signal: abort.signal });
    abort.abort();
    release(response("late-response"));
    expect(await resolving).toMatchObject({ state: "stale", code: "stale_revision" });
  });

  it("turns transport failures into a stale state without falling back", async () => {
    const client = controller(async () => { throw new Error("offline"); });
    expect(await client.resolve(request())).toMatchObject({ state: "stale", code: "stale_revision" });
  });

  it("invalidates cached ETags on grant revocation and observes the invalidation event", async () => {
    const etags: Array<string | undefined> = [];
    const fetch: RegistryFetcher = async ({ correlationId, etag }) => {
      etags.push(etag);
      return response(correlationId);
    };
    const client = controller(fetch);
    const observed = vi.fn();
    client.subscribe(observed);
    await client.resolve(request());
    await client.resolve(request());
    expect(etags).toEqual([undefined]);

    client.onGrantRevoked();
    expect(observed).toHaveBeenCalledWith(1, "grant_revoked");
    expect(await client.resolve(request())).toMatchObject({ state: "resolved" });
    expect(etags).toEqual([undefined, undefined]);
  });
});