// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { mountIsolatedPlugin, pluginRefusalCode, type PluginHostOptions } from "./plugin-host";

const DIGEST = `sha256:${"a".repeat(64)}`;
const PRODUCTION = "https://plugins.example";
const PREVIEW = "https://preview.plugins.invalid";
const SHELL = "https://app.example.com";

function base(overrides: Partial<PluginHostOptions> = {}): PluginHostOptions {
  const container = document.createElement("div");
  document.body.append(container);
  return {
    document,
    container,
    src: `${PRODUCTION}/plugin.js`,
    allowedOrigins: new Set([PRODUCTION]),
    identity: { pluginId: "demo-plugin", version: "1.0.0", digest: DIGEST },
    broker: { allowedCapabilities: new Set<string>(), methods: new Map(), adapter: async () => null },
    timeoutMs: 5_000,
    ...overrides,
  };
}

function refusalCode(options: PluginHostOptions): string | undefined {
  try {
    mountIsolatedPlugin(options);
    return undefined;
  } catch (error) {
    return pluginRefusalCode(error) ?? (error instanceof Error ? error.message : "unexpected");
  }
}

function emit(target: Window, event: { origin: string; source: unknown; data: unknown }): void {
  target.dispatchEvent(new MessageEvent("message", { ...event, source: event.source as MessageEventSource }));
}

describe("MIG-CAP-GATEWAY-02 plugin host origin isolation", () => {
  it("denies a wildcard allow-list and a non-HTTPS or query-bearing plugin URL", () => {
    expect(refusalCode(base({ allowedOrigins: new Set(["*"]) as unknown as ReadonlySet<string> }))).toBe("plugin_origin_allowlist_invalid");
    expect(refusalCode(base({ allowedOrigins: new Set([`${PRODUCTION}`, "*"]) as unknown as ReadonlySet<string> }))).toBe("plugin_origin_allowlist_invalid");
    expect(refusalCode(base({ src: "http://plugins.example/plugin.js" }))).toBe("plugin_origin_not_allowlisted");
    expect(refusalCode(base({ src: `${PRODUCTION}/plugin.js?token=leak` }))).toBe("plugin_origin_not_allowlisted");
    expect(refusalCode(base({ allowedOrigins: new Set(["https://other.example"]) }))).toBe("plugin_origin_not_allowlisted");
  });

  it("mounts only the exact registered origin with a non-same-origin sandbox", () => {
    const failures: string[] = [];
    const rejected: string[] = [];
    const options = base({ onFailure: (code) => failures.push(code), onRejectedMessage: (reason) => rejected.push(reason) });
    const mounted = mountIsolatedPlugin(options);
    expect(mounted.frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(mounted.frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(mounted.frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(new URL(mounted.frame.src).origin).toBe(PRODUCTION);

    // A message from another origin is ignored, not processed: no failure, no state change.
    emit(window, { origin: "https://evil.example", source: mounted.frame.contentWindow, data: { protocol: "plugin-rpc/1", type: "plugin.ready" } });
    expect(rejected).toEqual(["origin"]);
    expect(failures).toEqual([]);
    expect(mounted.frame.isConnected).toBe(true);

    // The exact registered origin is accepted; a malformed envelope then fails closed.
    emit(window, { origin: PRODUCTION, source: mounted.frame.contentWindow, data: { protocol: "not-plugin-rpc" } });
    expect(failures).toEqual(["PLUGIN_RPC_INVALID"]);
    expect(mounted.frame.isConnected).toBe(false);
    mounted.destroy();
  });

  it("keeps preview isolated: own origin list, opaque sandbox only, no mutation capabilities", () => {
    const previewOptions = base({
      src: `${PREVIEW}/plugin.js`,
      mode: "preview",
      allowedOrigins: new Set([PRODUCTION]),
      previewOrigins: new Set([PREVIEW]),
      shellOrigin: SHELL,
      broker: { allowedCapabilities: new Set(["platform.queries"]), methods: new Map([["queries.list", "platform.queries"]]), adapter: async () => ({ rows: [] }) },
    });
    const failures: string[] = [];
    const rejected: string[] = [];
    const mounted = mountIsolatedPlugin({ ...previewOptions, onFailure: (code) => failures.push(code), onRejectedMessage: (reason) => rejected.push(reason) });

    // A production origin is not a preview peer.
    emit(window, { origin: PRODUCTION, source: mounted.frame.contentWindow, data: { protocol: "plugin-rpc/1" } });
    expect(rejected).toEqual(["origin"]);
    expect(failures).toEqual([]);

    // The sandboxed preview frame has an opaque origin; it is accepted only because the host also
    // binds the exact WindowProxy and the instance nonce.
    emit(window, { origin: "null", source: mounted.frame.contentWindow, data: { protocol: "plugin-rpc/1" } });
    expect(failures).toEqual(["PLUGIN_RPC_INVALID"]);
    mounted.destroy();
  });

  it("refuses preview configurations that are not isolated", () => {
    const preview = (overrides: Partial<PluginHostOptions>) => base({
      src: `${PREVIEW}/plugin.js`,
      mode: "preview",
      allowedOrigins: new Set([PRODUCTION]),
      previewOrigins: new Set([PREVIEW]),
      shellOrigin: SHELL,
      ...overrides,
    });
    // Preview allow-list overlapping production is not an isolated policy.
    expect(refusalCode(preview({ previewOrigins: new Set([PRODUCTION]) }))).toBe("plugin_preview_policy_invalid");
    // A production origin cannot be mounted as preview.
    expect(refusalCode(preview({ src: `${PRODUCTION}/plugin.js` }))).toBe("plugin_preview_policy_invalid");
    // A preview origin that could share shell cookies is refused.
    expect(refusalCode(preview({ src: "https://preview.example.com/plugin.js", previewOrigins: new Set(["https://preview.example.com"]), shellOrigin: SHELL }))).toBe("plugin_preview_policy_invalid");
    // Preview may not reach mutation capabilities.
    expect(refusalCode(preview({ broker: { allowedCapabilities: new Set(["platform.commands"]), methods: new Map(), adapter: async () => null } }))).toBe("plugin_preview_capability_denied");
    // Production cannot opt into an opaque peer.
    expect(refusalCode(base({ allowOpaqueOrigin: true }))).toBe("plugin_isolation_policy_invalid");
  });

  it("rejects an unsupported isolation mode instead of falling back to production", () => {
    expect(refusalCode(base({ mode: "staging" as never }))).toBe("plugin_isolation_policy_invalid");
  });

  it("does not accept a URL supplied by the plugin itself", () => {
    const container = document.createElement("div");
    const attempt = vi.fn();
    const mounted = mountIsolatedPlugin(base({ container, broker: { allowedCapabilities: new Set<string>(), methods: new Map(), adapter: attempt } }));
    // The frame `src` comes from the artifact allow-list, never from manifest/plugin input.
    expect(new URL(mounted.frame.src).origin).toBe(PRODUCTION);
    expect(mounted.frame.getAttribute("srcdoc")).toBeNull();
    mounted.destroy();
  });
});
