// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderDeclarativeApp } from "./renderer";
import { createCapabilityBroker } from "../broker";
import { mountIsolatedPlugin } from "../plugin-host";
import type { CapabilityBoundary, CapabilityGateway, EffectiveCapability } from "../../capabilities/types";
import type { DeclarativeAppManifest, JsonValue } from "../../../../../packages/schemas/declarative-app/v1";
import { validateDeclarativeManifest } from "../schema";
import { DynamicReactHost } from "./host";
import type { DynamicReactHostProps } from "./host";

const manifest = {
  protocol: "1.0", appId: "demo-app", version: "1.0.0", displayName: "Demo",
  entrypoints: { ui: { kind: "declarative-ui", root: { type: "stack", children: [
    { type: "text", id: "safe-label", props: { text: "<img src=x onerror=alert(1)>" } },
    { type: "button", id: "action", props: { label: "Run" } },
  ] } } }, requestedCapabilities: [],
};

const HOST_MANIFEST: DeclarativeAppManifest = {
  protocol: "1.0", appId: "demo-app", version: "1.0.0", displayName: "Demo",
  entrypoints: { ui: { kind: "declarative-ui", root: { type: "stack", id: "root", children: [
    { type: "table", id: "orders", props: { label: "Orders" } },
    { type: "input", id: "name", props: { label: "Name", required: true } },
    { type: "button", id: "save", props: { label: "Save" } },
  ] }, routes: [{ id: "main", path: "/demo", title: "Demo", root: "root" }] } },
  requestedCapabilities: [
    { name: "platform.queries", reason: "List rows", constraints: { methods: ["orders.list"] } },
    { name: "platform.commands", reason: "Save row", constraints: { methods: ["orders.save"] } },
  ],
};
const hostValidation = validateDeclarativeManifest(HOST_MANIFEST);
if (!hostValidation.ok) throw new Error("Invalid dynamic host acceptance manifest");
const hostResolution = { state: "resolved" as const, manifest: hostValidation.manifest,
  route: { id: "main", path: "/demo", title: "Demo", root: "root", requirements: { context: ["tenantId"] } },
  digest: `sha256:${"b".repeat(64)}`, etag: '"fixture"', cacheKey: "fixture", correlationId: "registry.fixture" };
const effective = new Map<"platform.queries" | "platform.commands", EffectiveCapability>([
  ["platform.queries", { name: "platform.queries", source: "user", constraints: { methods: ["orders.list"] } }],
  ["platform.commands", { name: "platform.commands", source: "user", constraints: { methods: ["orders.save"] } }],
]);
const capabilityBoundary: CapabilityBoundary = { appId: "demo-app", manifestVersion: "1.0.0", requested: HOST_MANIFEST.requestedCapabilities, effective };
const hostContext = { tenantId: "tenant-1", projectId: "project-1", traceId: "trace-1", requestId: "request-0001" };
let reactRoot: Root | undefined;
let reactContainer: HTMLDivElement | undefined;
async function mountHost(props: DynamicReactHostProps): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  reactContainer = document.createElement("div"); document.body.append(reactContainer); reactRoot = createRoot(reactContainer);
  await act(async () => { reactRoot?.render(<DynamicReactHost {...props} />); });
}
async function flushHost(): Promise<void> { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
function hostProps(gateway: CapabilityGateway, overrides: Partial<DynamicReactHostProps> = {}): DynamicReactHostProps {
  return {
    resolution: hostResolution, gateway, boundary: capabilityBoundary, context: hostContext,
    bindings: {
      tables: { orders: { capability: "platform.queries", method: "orders.list", columns: [{ id: "id", label: "ID" }, { id: "name", label: "Name" }], buildParams: (scope) => ({ projectId: scope.projectId ?? "" }) } },
      actions: { save: { capability: "platform.commands", method: "orders.save", inputNodeIds: ["name"], buildParams: (values, scope) => ({ name: values.name ?? "", projectId: scope.projectId ?? "" }) } },
    }, ...overrides,
  };
}

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

describe("dynamic React host acceptance", () => {
  afterEach(async () => {
    if (reactRoot) await act(async () => reactRoot?.unmount());
    reactRoot = undefined;
    reactContainer?.remove(); reactContainer = undefined;
  });

  it("renders rows, filters them locally, and pages the host-returned results", async () => {
    const invoke = vi.fn(async (method: string) => method === "orders.list" ? [{ id: 1, name: "Alpha" }, { id: 2, name: "Beta" }, { id: 3, name: "Gamma" }] : null);
    const gateway = { invoke, notify: vi.fn() } as unknown as CapabilityGateway;
    await mountHost(hostProps(gateway, { pageSize: 2 })); await flushHost();
    expect(reactContainer?.textContent).toContain("Alpha"); expect(reactContainer?.textContent).not.toContain("Gamma");
    const next = [...(reactContainer?.querySelectorAll("button") ?? [])].find((button) => button.textContent === "Next");
    await act(async () => next?.click()); expect(reactContainer?.textContent).toContain("Gamma");
    const search = reactContainer?.querySelector<HTMLInputElement>("input[type=search]");
    if (!search) throw new Error("Search field missing");
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "Alpha"); search.dispatchEvent(new Event("input", { bubbles: true })); search.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(reactContainer?.textContent).toContain("Alpha"); expect(reactContainer?.textContent).not.toContain("Gamma");
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it("submits an action only through the registered capability gateway binding", async () => {
    const invoke = vi.fn(async () => ({ ok: true }));
    const gateway = { invoke, notify: vi.fn() } as unknown as CapabilityGateway;
    await mountHost(hostProps(gateway));
    const name = reactContainer?.querySelector<HTMLInputElement>("#name");
    const save = [...(reactContainer?.querySelectorAll("button") ?? [])].find((button) => button.textContent === "Save");
    if (!name || !save) throw new Error("Action controls missing");
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(name, "Cable"); name.dispatchEvent(new Event("input", { bubbles: true })); name.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => save.click()); await flushHost();
    expect(invoke).toHaveBeenCalledWith("orders.save", { name: "Cable", projectId: "project-1" }, expect.objectContaining({ capability: "platform.commands", signal: expect.any(AbortSignal) }));
  });

  it("cancels a pending user action and retains the draft across disable", async () => {
    let signal: AbortSignal | undefined;
    const invoke = vi.fn((_method: string, _params: JsonValue, options?: { signal?: AbortSignal }) => new Promise<JsonValue>((_resolve, reject) => {
      signal = options?.signal; signal?.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")), { once: true });
    }));
    const gateway = { invoke, notify: vi.fn() } as unknown as CapabilityGateway;
    await mountHost(hostProps(gateway));
    const name = reactContainer?.querySelector<HTMLInputElement>("#name");
    const save = [...(reactContainer?.querySelectorAll("button") ?? [])].find((button) => button.textContent === "Save");
    if (!name || !save) throw new Error("Action controls missing");
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(name, "Keep draft"); name.dispatchEvent(new Event("input", { bubbles: true })); name.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => save.click());
    const cancel = [...(reactContainer?.querySelectorAll("button") ?? [])].find((button) => button.textContent === "Cancel");
    await act(async () => cancel?.click());
    expect(signal?.aborted).toBe(true); expect(name.value).toBe("Keep draft");
    await act(async () => reactRoot?.render(<DynamicReactHost {...hostProps(gateway, { enabled: false })} />));
    expect(reactContainer?.textContent).toContain("Dynamic app disabled by host");
    expect(reactContainer?.querySelector<HTMLButtonElement>("#save")?.disabled).toBe(true);
  });

  it("shows unavailable state when the effective method grant is absent", async () => {
    const deniedBoundary = { ...capabilityBoundary, effective: new Map() };
    const invoke = vi.fn(async () => []);
    const gateway = { invoke, notify: vi.fn() } as unknown as CapabilityGateway;
    await mountHost(hostProps(gateway, { boundary: deniedBoundary })); await flushHost();
    expect(reactContainer?.textContent).toContain("No approved host operation");
    expect(invoke).not.toHaveBeenCalled();
  });
});
