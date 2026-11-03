import { describe, expect, it, vi } from "vitest";
import { CapabilityDeniedError, createCapabilityBoundary } from "./boundary";
import { createCapabilityGateway, createHostMessageTransport, methodBindingsFrom } from "./gateway";
import { parseAppManifest } from "./manifest";
import { OriginPolicyError } from "./origin-policy";
import { ScopeNotAuthoritativeError, shellScope, verifyScope, type ScopeAttestation } from "./scope";
import type { CapabilityGatewayTransport } from "./types";

const manifest = {
  protocol: "1.0" as const,
  appId: "app.test",
  version: "1.0.0",
  displayName: "Test app",
  entrypoints: {},
  requestedCapabilities: [{ name: "platform.commands" as const, reason: "run command", constraints: { methods: ["command.run"] } }],
};
const grantSet = {
  appId: "app.test",
  manifestVersion: "1.0.0",
  issuedAt: "2026-01-01T00:00:00.000Z",
  grants: [{ name: "platform.commands" as const, decision: "granted" as const, grantedBy: "administrator" as const, constraints: { methods: ["command.run"] } }],
};

function transport(): CapabilityGatewayTransport {
  return { request: vi.fn(async () => null), notify: vi.fn() };
}

describe("capability gateway", () => {
  it("requires a capability for domain/data/action invoke and notify", async () => {
    const tx = transport();
    const gateway = createCapabilityGateway(tx, () => createCapabilityBoundary(manifest, grantSet, Date.parse("2026-02-01T00:00:00Z")));
    await expect(gateway.invoke("command.run")).rejects.toThrow("Capability is required");
    expect(() => gateway.notify("command.run")).toThrow("Capability is required");
    await expect(gateway.invoke("command.run", undefined, { capability: "platform.commands" })).resolves.toBeNull();
  });

  it("allows only explicit lifecycle/control methods without a capability", async () => {
    const tx = transport();
    const gateway = createCapabilityGateway(tx, () => undefined);
    await expect(gateway.invoke("lifecycle.start")).resolves.toBeNull();
    expect(() => gateway.notify("control.ping")).not.toThrow();
    await expect(gateway.invoke("unknown.control")).rejects.toThrow("Capability is required");
  });

  it("enforces method constraints", async () => {
    const tx = transport();
    const gateway = createCapabilityGateway(tx, () => createCapabilityBoundary(manifest, grantSet, Date.parse("2026-02-01T00:00:00Z")));
    await expect(gateway.invoke("command.delete", undefined, { capability: "platform.commands" })).rejects.toThrow("not allowed");
  });

  it("fails closed for app/version, expiry, and malformed grants", () => {
    const expired = { ...grantSet, expiresAt: "2026-01-02T00:00:00Z" };
    expect(createCapabilityBoundary(manifest, expired, Date.parse("2026-02-03T00:00:00Z")).effective.size).toBe(0);
    expect(createCapabilityBoundary(manifest, { ...grantSet, appId: "other" }, Date.parse("2026-02-01T00:00:00Z")).effective.size).toBe(0);
    expect(createCapabilityBoundary(manifest, { ...grantSet, grants: [{ ...grantSet.grants[0], expiresAt: "not-a-date" }] }, Date.parse("2026-02-01T00:00:00Z")).effective.size).toBe(0);
  });

  it("rejects malformed manifests", () => {
    expect(() => parseAppManifest({ ...manifest, requestedCapabilities: [{ name: "platform.commands", reason: "" }] })).toThrow();
    expect(() => parseAppManifest({ ...manifest, entrypoints: undefined })).toThrow();
  });

  it("does not post an already-aborted request", async () => {
    const tx = transport();
    const controller = new AbortController();
    controller.abort();
    const gateway = createCapabilityGateway(tx, () => undefined);
    await expect(gateway.invoke("control.ping", undefined, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(tx.request).not.toHaveBeenCalled();
  });
});

interface FakeWindow {
  readonly listeners: ((event: MessageEvent) => void)[];
  readonly posted: { readonly message: unknown; readonly targetOrigin: string }[];
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  removeEventListener(type: string, listener: (event: MessageEvent) => void): void;
  postMessage(message: unknown, targetOrigin: string): void;
}

function fakeWindow(): FakeWindow & { emit(event: { origin: string; source?: unknown; data: unknown }): void } {
  const listeners: ((event: MessageEvent) => void)[] = [];
  const posted: { message: unknown; targetOrigin: string }[] = [];
  return {
    listeners,
    posted,
    addEventListener: (_type, listener) => { listeners.push(listener); },
    removeEventListener: (_type, listener) => { const index = listeners.indexOf(listener); if (index >= 0) listeners.splice(index, 1); },
    postMessage: (message, targetOrigin) => { posted.push({ message, targetOrigin }); },
    emit: (event) => { for (const listener of [...listeners]) listener(event as unknown as MessageEvent); },
  };
}

function policyCode(run: () => unknown): string | undefined {
  try {
    run();
    return undefined;
  } catch (error) {
    return error instanceof OriginPolicyError ? error.code : "unexpected";
  }
}

const attestation: ScopeAttestation = { bridge: "web-api.session", attestationId: "att-00000001", verifiedAt: "2026-11-04T00:00:00.000Z" };
const grantedBoundary = () => createCapabilityBoundary(manifest, grantSet, Date.parse("2026-02-01T00:00:00Z"));

describe("MIG-CAP-GATEWAY-02 host transport exact origin", () => {
  it("denies a wildcard peer origin instead of matching every sender", () => {
    const host = fakeWindow();
    expect(policyCode(() => createHostMessageTransport(host as unknown as Window, "*"))).toBe("wildcard_origin_denied");
    expect(policyCode(() => createHostMessageTransport({ target: host as unknown as Window, peerOrigin: "*", allowedOrigins: ["https://plugins.example"] }))).toBe("wildcard_origin_denied");
    expect(host.listeners.length).toBe(0);
    expect(host.posted.length).toBe(0);
  });

  it("posts and accepts only the exact registered origin", async () => {
    const host = fakeWindow();
    const transport = createHostMessageTransport({ target: host as unknown as Window, peerOrigin: "https://plugins.example", allowedOrigins: ["https://plugins.example"] });
    const pending = transport.request({ method: "command.run", params: {} });
    const request = host.posted[0].message as { id: string };
    expect(host.posted[0].targetOrigin).toBe("https://plugins.example");

    // A sibling origin, a sub-domain and an opaque origin are all different peers.
    host.emit({ origin: "https://evil.example", data: { protocol: "1.0", type: "response", id: request.id, result: "evil" } });
    host.emit({ origin: "https://plugins.example.evil.test", data: { protocol: "1.0", type: "response", id: request.id, result: "evil" } });
    host.emit({ origin: "null", data: { protocol: "1.0", type: "response", id: request.id, result: "evil" } });
    host.emit({ origin: "https://plugins.example", data: { protocol: "1.0", type: "response", id: request.id, result: "ok" } });

    await expect(pending).resolves.toBe("ok");
  });

  it("refuses a response whose WindowProxy is not the registered frame", async () => {
    const host = fakeWindow();
    const frame = {};
    const rejected: string[] = [];
    const transport = createHostMessageTransport({
      target: host as unknown as Window,
      peerOrigin: "https://plugins.example",
      allowedOrigins: ["https://plugins.example"],
      source: frame as unknown as Window,
      onRejectedMessage: (reason) => rejected.push(reason),
    });
    const pending = transport.request({ method: "command.run" });
    const request = host.posted[0].message as { id: string };
    host.emit({ origin: "https://plugins.example", source: {}, data: { protocol: "1.0", type: "response", id: request.id, result: "spoofed" } });
    expect(rejected).toEqual(["source"]);
    host.emit({ origin: "https://plugins.example", source: frame, data: { protocol: "1.0", type: "response", id: request.id, result: "ok" } });
    await expect(pending).resolves.toBe("ok");
  });

  it("keeps preview isolated: opaque origin only in preview, exact origin never shared with production", async () => {
    const host = fakeWindow();
    expect(policyCode(() => createHostMessageTransport({
      target: host as unknown as Window,
      peerOrigin: "https://preview.plugins.invalid",
      mode: "preview",
      allowedOrigins: ["https://plugins.example"],
      previewOrigins: ["https://plugins.example"],
      shellOrigin: "https://app.example.com",
      allowOpaqueOrigin: true,
    }))).toBe("preview_allowlist_not_isolated");

    const transport = createHostMessageTransport({
      target: host as unknown as Window,
      peerOrigin: "https://preview.plugins.invalid",
      mode: "preview",
      allowedOrigins: ["https://plugins.example"],
      previewOrigins: ["https://preview.plugins.invalid"],
      shellOrigin: "https://app.example.com",
      allowOpaqueOrigin: true,
    });
    const pending = transport.request({ method: "queries.list" });
    const request = host.posted[0].message as { id: string };
    expect(host.posted[0].targetOrigin).toBe("*");
    host.emit({ origin: "https://plugins.example", data: { protocol: "1.0", type: "response", id: request.id, result: "production" } });
    host.emit({ origin: "null", data: { protocol: "1.0", type: "response", id: request.id, result: "preview" } });
    await expect(pending).resolves.toBe("preview");
  });
});

describe("MIG-CAP-GATEWAY-02 capability claim is never authorization", () => {
  it("denies an unverified shell context for a capability that needs authoritative scope", async () => {
    const tx = transport();
    const gateway = createCapabilityGateway(tx, grantedBoundary, { scope: () => shellScope({ tenantId: "tenant-1" }) });
    const failure = await gateway.invoke("command.run", undefined, { capability: "platform.commands" }).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ScopeNotAuthoritativeError);
    expect((failure as ScopeNotAuthoritativeError).code).toBe("scope_unverified");
    expect(tx.request).not.toHaveBeenCalled();
  });

  it("allows the same call once the server bridge attests the scope", async () => {
    const tx = transport();
    const scope = verifyScope({ tenantId: "tenant-1" }, attestation);
    const gateway = createCapabilityGateway(tx, grantedBoundary, { scope: () => scope });
    await expect(gateway.invoke("command.run", undefined, { capability: "platform.commands" })).resolves.toBeNull();
  });

  it("denies an unknown capability and a claim that contradicts the verified method binding", async () => {
    const tx = transport();
    const bindings = methodBindingsFrom([{ method: "command.run", capability: "platform.commands" }]);
    const gateway = createCapabilityGateway(tx, grantedBoundary, { methodBinding: (method) => bindings.get(method) });

    const unknown = await gateway.invoke("command.run", undefined, { capability: "admin.superuser" as never }).catch((error: unknown) => error);
    expect(unknown).toBeInstanceOf(CapabilityDeniedError);
    expect((unknown as CapabilityDeniedError).code).toBe("unknown_capability");

    const mismatch = await gateway.invoke("command.run", undefined, { capability: "platform.queries" }).catch((error: unknown) => error);
    expect(mismatch).toBeInstanceOf(CapabilityDeniedError);
    expect((mismatch as CapabilityDeniedError).code).toBe("binding_mismatch");

    const unbound = await gateway.invoke("command.delete", undefined, { capability: "platform.commands" }).catch((error: unknown) => error);
    expect((unbound as CapabilityDeniedError).code).toBe("binding_mismatch");
    expect(tx.request).not.toHaveBeenCalled();
  });

  it("keeps the legacy unscoped behaviour when no scope wiring is supplied", async () => {
    const tx = transport();
    const gateway = createCapabilityGateway(tx, grantedBoundary);
    await expect(gateway.invoke("command.run", undefined, { capability: "platform.commands" })).resolves.toBeNull();
    expect(tx.request).toHaveBeenCalledTimes(1);
  });
});
