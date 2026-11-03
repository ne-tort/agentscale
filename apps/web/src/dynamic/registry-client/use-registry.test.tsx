import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { createDynamicRegistryController } from "./controller";
import { useDynamicRegistry } from "./use-registry";

describe("useDynamicRegistry", () => {
  it("exposes loading for a query without rendering stale data from another query", () => {
    const client = createDynamicRegistryController({
      fetchSnapshot: vi.fn(async () => { throw new Error("SSR must not fetch"); }),
      verifySnapshot: async () => true,
    });
    const query = {
      appId: "demo-app",
      version: "1.2.0",
      routeId: "main",
      context: { tenantId: "tenant-1", traceId: "trace-1", requestId: "request-0001" },
      effectiveCapabilities: new Set<string>(["platform.commands"]),
    };
    function Probe() {
      const registry = useDynamicRegistry(client, query);
      return createElement("output", null, registry.status);
    }

    expect(renderToStaticMarkup(createElement(Probe))).toContain(">loading</output>");
  });

  it("keeps the hook idle when there is no active route request", () => {
    const fetchSnapshot = vi.fn();
    const client = createDynamicRegistryController({ fetchSnapshot, verifySnapshot: async () => true });
    function Probe() {
      const registry = useDynamicRegistry(client);
      return createElement("output", null, registry.status);
    }

    expect(renderToStaticMarkup(createElement(Probe))).toContain(">idle</output>");
    expect(fetchSnapshot).not.toHaveBeenCalled();
  });
});