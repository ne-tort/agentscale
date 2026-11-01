import { describe, expect, it } from "vitest";
import { createInitialDynamicAppsState, reduceDynamicAppsState } from "./state";
import { createDynamicAppRegistryContract, DYNAMIC_APP_METHODS } from "./gateway";
import type { CapabilityGateway } from "../capabilities/types";

const page = { items: [], nextCursor: "next" } as const;
const detail = {
  appId: "demo-app",
  manifestVersion: "1.0.0",
  displayName: "Demo",
  publication: "published" as const,
  lifecycle: "running" as const,
  availability: "available" as const,
  requestedCapabilityCount: 0,
  updatedAt: "2026-01-01T00:00:00.000Z",
  manifest: { protocol: "1.0" as const, appId: "demo-app", version: "1.0.0", displayName: "Demo", entrypoints: {}, requestedCapabilities: [] },
  lifecycleEvents: [],
};

describe("dynamic app view state", () => {
  it("preserves registry data while entering a detail loading state", () => {
    const ready = reduceDynamicAppsState(
      reduceDynamicAppsState(createInitialDynamicAppsState(), { type: "load", query: {} }),
      { type: "loaded", page },
    );
    const loading = reduceDynamicAppsState(ready, { type: "load-detail", appId: "demo-app" });
    expect(loading).toMatchObject({ kind: "detail-loading", appId: "demo-app", page });
  });

  it("retains page and detail through loading and mutation failure", () => {
    const ready = reduceDynamicAppsState(
      reduceDynamicAppsState(createInitialDynamicAppsState(), { type: "load", query: {} }),
      { type: "loaded", page },
    );
    const detailReady = reduceDynamicAppsState(ready, { type: "detail-loaded", detail });
    const loading = reduceDynamicAppsState(detailReady, { type: "load", query: { publication: "published" } });
    expect(loading).toMatchObject({ kind: "loading", previous: page });
    const failed = reduceDynamicAppsState(
      reduceDynamicAppsState(loading, { type: "mutate", appId: detail.appId }),
      { type: "failed", message: "denied" },
    );
    expect(failed).toMatchObject({ kind: "error", message: "denied", page, detail });
  });
});

describe("dynamic app gateway contract", () => {
  it("maps lifecycle operations to typed capability-protected methods", async () => {
    const calls: Array<{ method: string; params: unknown; capability?: string }> = [];
    const gateway: CapabilityGateway = {
      invoke: async (method, params, options) => { calls.push({ method, params, capability: options?.capability }); return detail; },
      notify: () => undefined,
    };
    const registry = createDynamicAppRegistryContract({ gateway, appId: detail.appId, manifestVersion: detail.manifestVersion });
    await registry.list({ publication: "published" });
    await registry.detail(detail.appId);
    await registry.publish({ manifest: detail.manifest });
    await registry.grant({ appId: detail.appId, manifestVersion: detail.manifestVersion, capability: { name: "ui.render", decision: "granted", grantedBy: "administrator" } });
    await registry.revoke({ appId: detail.appId, manifestVersion: detail.manifestVersion, capability: "ui.render" });
    expect(calls.map(({ method }) => method)).toEqual(Object.values(DYNAMIC_APP_METHODS));
    expect(calls.map(({ capability }) => capability)).toEqual([
      "platform.queries", "platform.queries", "platform.commands", "platform.commands", "platform.commands",
    ]);
  });

  it("rejects app/version scope changes before transport", async () => {
    const gateway: CapabilityGateway = { invoke: async () => detail, notify: () => undefined };
    const registry = createDynamicAppRegistryContract({ gateway, appId: detail.appId, manifestVersion: detail.manifestVersion });
    expect(() => registry.detail("other.app")).toThrow("scope");
    expect(() => registry.grant({ appId: detail.appId, manifestVersion: "2.0.0", capability: { name: "ui.render", decision: "granted", grantedBy: "administrator" } })).toThrow("version");
  });
});
