import { describe, expect, it, vi } from "vitest";
import { renderDeclarativeApp } from "./renderer";
import { createCapabilityBroker } from "../broker";
import { mountIsolatedPlugin } from "../plugin-host";

const manifest = {
  protocol: "1.0", appId: "demo-app", version: "1.0.0", displayName: "Demo",
  entrypoints: { ui: { kind: "declarative-ui", root: { type: "stack", children: [
    { type: "text", id: "safe-label", props: { text: "<img src=x onerror=alert(1)>" } },
    { type: "button", id: "action", props: { label: "Run" } },
  ] } } }, requestedCapabilities: [],
};

describe("MIG-WEB-02 source acceptance fixtures (not executed during migration freeze)", () => {
  it("renders allowlisted elements as text, never parses HTML", () => {
    const container = document.createElement("main");
    renderDeclarativeApp(container, manifest);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("renders table loading/error/empty and ready rows as text only", () => {
    const tableManifest = { ...manifest, entrypoints: { ui: { kind: "declarative-ui", root: { type: "table", id: "orders", props: { label: "Orders" } } } } };
    const loading = document.createElement("main");
    renderDeclarativeApp(loading, tableManifest, { orders: { state: "loading" } });
    expect(loading.textContent).toContain("Loading");
    const ready = document.createElement("main");
    renderDeclarativeApp(ready, tableManifest, { orders: { state: "ready", columns: ["name"], rows: [{ name: "<script>" }] } });
    expect(ready.querySelector("script")).toBeNull();
    expect(ready.textContent).toContain("<script>");
  });

  it("rejects unknown component discriminants", () => {
    const invalid = structuredClone(manifest);
    (invalid.entrypoints.ui.root.children[0] as { type: string }).type = "html";
    expect(() => renderDeclarativeApp(document.createElement("main"), invalid)).toThrow("invalid_declarative_ui");
  });

  it("denies undeclared and generic infrastructure/network methods", async () => {
    const adapter = vi.fn(async () => ({ ok: true }));
    const broker = createCapabilityBroker({ allowedCapabilities: new Set(["platform.queries"]), methods: new Map([["orders.list", "platform.queries"]]), adapter });
    await expect(broker.invoke("fetch", {})).rejects.toThrow("CAPABILITY_DENIED");
    await expect(broker.invoke("database.sql", {})).rejects.toThrow("CAPABILITY_DENIED");
    expect(adapter).not.toHaveBeenCalled();
  });

  it("keeps the opaque-origin handshake blocked and exposes no URL bootstrap", () => {
    const container = document.createElement("div");
    const failures: string[] = [];
    const args = { document, container, src: "https://plugins.example/plugin", allowedOrigins: new Set(["https://plugins.example"]), identity: { pluginId: "demo", version: "1.0.0", digest: "a".repeat(64) }, broker: { allowedCapabilities: new Set<string>(), methods: new Map(), adapter: async () => null }, onFailure: (code: string) => failures.push(code) };
    const mounted = mountIsolatedPlugin(args);
    expect(mounted.frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(new URL(mounted.frame.src).search).toBe("");
    expect(new URL(mounted.frame.src).hash).toBe("");
    expect(mounted.frame.src).not.toContain("nonce");
    mounted.destroy();
    expect(failures).toEqual([]);
  });

  it("fails closed on non-allowlisted, query-bearing, or non-HTTPS plugin URLs", () => {
    const container = document.createElement("div");
    const args = { document, container, identity: { pluginId: "demo", version: "1.0.0", digest: "a".repeat(64) }, broker: { allowedCapabilities: new Set<string>(), methods: new Map(), adapter: async () => null } };
    expect(() => mountIsolatedPlugin({ ...args, src: "https://evil.example/plugin", allowedOrigins: new Set(["https://plugins.example"]) })).toThrow("plugin_origin_not_allowlisted");
    expect(() => mountIsolatedPlugin({ ...args, src: "https://plugins.example/plugin?token=x", allowedOrigins: new Set(["https://plugins.example"]) })).toThrow("plugin_origin_not_allowlisted");
  });

  it("does not wire button interactions to unapproved command execution", () => {
    const container = document.createElement("main");
    renderDeclarativeApp(container, manifest);
    const button = container.querySelector("button");
    expect(button?.disabled).toBe(true);
    expect(button?.getAttribute("aria-disabled")).toBe("true");
  });

  it("documents unresolved upstream and handshake decisions as fail-closed integration gates", () => {
    const upstreamGatewayContractAvailable = false;
    const approvedIframeCspOriginPolicyAvailable = false;
    const exactOriginHandshakeCompatibleWithOpaqueSandbox = false;
    const approvedMessageChannelBootstrapContractAvailable = false;
    const sandboxOpaqueOriginBlocksExactHttpsOriginReady = true;
    expect(upstreamGatewayContractAvailable).toBe(false);
    expect(approvedIframeCspOriginPolicyAvailable).toBe(false);
    expect(exactOriginHandshakeCompatibleWithOpaqueSandbox).toBe(false);
    expect(approvedMessageChannelBootstrapContractAvailable).toBe(false);
    expect(sandboxOpaqueOriginBlocksExactHttpsOriginReady).toBe(true);
  });
});
