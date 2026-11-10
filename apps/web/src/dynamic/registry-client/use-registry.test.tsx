// @vitest-environment jsdom
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDynamicRegistryController, type DynamicRegistryController } from "./controller";
import {
  useDynamicRegistry,
  useDynamicRegistryRoute,
  type DynamicRegistryHookState,
  type DynamicRegistryQuery,
  type DynamicRegistryRouteQuery,
} from "./use-registry";
import type { RegistryFetcher, RegistryResponse, SignedRegistrySnapshot } from "../registry";

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
  requestedCapabilities: [{ name: "platform.commands", reason: "Authoritative registry hook regression" }],
};
const route = {
  id: "main",
  path: "/demo",
  title: "Demo",
  root: "root",
  requirements: { context: ["tenantId"], capabilities: ["platform.commands"] },
  signatureStatus: "verified" as const,
};
const snapshot = {
  schemaVersion: "1.0",
  apiContractVersion: "2026-10",
  digest: "sha256:" + "a".repeat(64),
  signatureStatus: "verified",
  signature: { algorithm: "Ed25519", keyId: "registry-key", value: "A".repeat(86) },
  issuedAt,
  ttlSeconds: 60,
  entries: [{
    appId: "demo-app",
    version: "1.2.0",
    artifactDigest: "sha256:" + "b".repeat(64),
    schemaVersion: "1.0",
    status: "published",
    tenantId: "tenant-1",
    manifest,
    routes: [route],
    grants: ["platform.commands"],
  }],
} as unknown as SignedRegistrySnapshot;

function response(correlationId: string): RegistryResponse {
  return { snapshot, etag: '"registry-v1"', correlationId };
}

function client(): DynamicRegistryController {
  const fetchSnapshot: RegistryFetcher = async ({ correlationId }) => response(correlationId);
  return createDynamicRegistryController({
    fetchSnapshot,
    verifySnapshot: async () => true,
    now: () => Date.parse(issuedAt) + 1000,
  });
}

function appQuery(overrides: Partial<DynamicRegistryQuery> = {}): DynamicRegistryQuery {
  return {
    appId: "demo-app",
    version: "1.2.0",
    routeId: "main",
    context: { tenantId: "tenant-1", traceId: "trace-1", requestId: "request-0001" },
    effectiveCapabilities: new Set(["platform.commands"]),
    ...overrides,
  };
}

function routeQuery(overrides: Partial<DynamicRegistryRouteQuery> = {}): DynamicRegistryRouteQuery {
  return {
    pathname: "/demo",
    snapshotHint: snapshot,
    context: { tenantId: "tenant-1", traceId: "trace-1", requestId: "request-0001" },
    effectiveCapabilities: new Set(["platform.commands"]),
    ...overrides,
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let latest: DynamicRegistryHookState | undefined;

function AppCapture({ controller, query }: { controller: DynamicRegistryController; query: DynamicRegistryQuery }) {
  latest = useDynamicRegistry(controller, query);
  return null;
}

function RouteCapture({ controller, query }: { controller: DynamicRegistryController; query: DynamicRegistryRouteQuery }) {
  latest = useDynamicRegistryRoute(controller, query);
  return null;
}

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  latest = undefined;
  await act(async () => { root?.render(element); });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function rerender(element: ReactElement): Promise<void> {
  await act(async () => { root?.render(element); });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  root = undefined;
  host?.remove();
  host = undefined;
  latest = undefined;
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("useDynamicRegistry", () => {
  it("exposes loading for a query without rendering stale data from another query", () => {
    const controller = createDynamicRegistryController({
      fetchSnapshot: vi.fn(async () => { throw new Error("SSR must not fetch"); }),
      verifySnapshot: async () => true,
    });
    function Probe() {
      const registry = useDynamicRegistry(controller, appQuery());
      return createElement("output", null, registry.status);
    }
    expect(renderToStaticMarkup(createElement(Probe))).toContain(">loading</output>");
  });

  it("keeps the hook idle when there is no active route request", () => {
    const fetchSnapshot = vi.fn();
    const controller = createDynamicRegistryController({ fetchSnapshot, verifySnapshot: async () => true });
    function Probe() {
      const registry = useDynamicRegistry(controller);
      return createElement("output", null, registry.status);
    }
    expect(renderToStaticMarkup(createElement(Probe))).toContain(">idle</output>");
    expect(fetchSnapshot).not.toHaveBeenCalled();
  });

  it("forwards authoritative=true to resolver and denies unverified scope", async () => {
    const controller = client();
    const resolve = vi.spyOn(controller, "resolve");
    await mount(<AppCapture controller={controller} query={appQuery({ requiresAuthoritativeScope: true })} />);

    expect(resolve).toHaveBeenCalledTimes(1);
    expect(resolve.mock.calls[0][0]).toMatchObject({ requiresAuthoritativeScope: true });
    expect(latest).toMatchObject({
      status: "denied",
      resolution: { state: "denied", code: "unverified_context" },
    });
  });

  it("normalizes false and undefined to one non-authoritative key/effect", async () => {
    const controller = client();
    const resolve = vi.spyOn(controller, "resolve");
    await mount(<AppCapture controller={controller} query={appQuery({ requiresAuthoritativeScope: false })} />);
    expect(latest?.status).toBe("resolved");
    expect(resolve).toHaveBeenCalledTimes(1);

    await rerender(<AppCapture controller={controller} query={appQuery()} />);

    expect(latest?.status).toBe("resolved");
    expect(resolve).toHaveBeenCalledTimes(1);
    expect(resolve.mock.calls[0][0].requiresAuthoritativeScope).toBe(false);
  });

  it("changes effect and never shares unverified cache when authoritative flag toggles", async () => {
    const controller = client();
    const resolve = vi.spyOn(controller, "resolve");
    await mount(<AppCapture controller={controller} query={appQuery({ requiresAuthoritativeScope: false })} />);
    expect(latest?.status).toBe("resolved");

    await rerender(<AppCapture controller={controller} query={appQuery({ requiresAuthoritativeScope: true })} />);
    expect(latest).toMatchObject({ status: "denied", resolution: { code: "unverified_context" } });
    expect(resolve).toHaveBeenCalledTimes(2);
    expect(resolve.mock.calls[1][0]).toMatchObject({ requiresAuthoritativeScope: true });

    await rerender(<AppCapture controller={controller} query={appQuery({ requiresAuthoritativeScope: false })} />);
    expect(latest?.status).toBe("resolved");
    expect(resolve).toHaveBeenCalledTimes(3);
  });
});

describe("useDynamicRegistryRoute authoritative scope", () => {
  it("forwards the flag through route resolution and changes effect on toggle", async () => {
    const controller = client();
    const resolveRoute = vi.spyOn(controller, "resolveRoute");
    await mount(<RouteCapture controller={controller} query={routeQuery()} />);
    expect(latest?.status).toBe("resolved");
    expect(resolveRoute).toHaveBeenCalledTimes(1);

    await rerender(<RouteCapture controller={controller} query={routeQuery({ requiresAuthoritativeScope: true })} />);

    expect(resolveRoute).toHaveBeenCalledTimes(2);
    expect(resolveRoute.mock.calls[1][0]).toMatchObject({ requiresAuthoritativeScope: true });
    expect(latest).toMatchObject({
      status: "denied",
      resolution: { state: "denied", code: "unverified_context" },
    });
  });
});
