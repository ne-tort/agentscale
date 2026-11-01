import { describe, expect, it, vi } from "vitest";
import { createCapabilityBoundary } from "./boundary";
import { createCapabilityGateway } from "./gateway";
import { parseAppManifest } from "./manifest";
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
