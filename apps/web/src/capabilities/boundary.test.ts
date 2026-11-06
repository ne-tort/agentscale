import { describe, expect, it } from "vitest";
import type { DeclarativeAppManifest, GrantSet } from "../../../../packages/schemas/declarative-app/v1";
import { CapabilityDeniedError, constraintKeysFor, constraintsAllow, createCapabilityBoundary, denialReason, hasCapability, requireCapability } from "./boundary";
import { capabilityConstraintMismatch, isKnownCapability, unsupportedConstraintKeys } from "./contracts";
import { parseAppManifest } from "./manifest";

const manifest = (requestedCapabilities: DeclarativeAppManifest["requestedCapabilities"]): DeclarativeAppManifest => ({
  protocol: "1.0",
  appId: "app.test",
  version: "1.0.0",
  displayName: "Test app",
  entrypoints: {},
  requestedCapabilities,
});

const grantSet = (grants: GrantSet["grants"]): GrantSet => ({
  appId: "app.test",
  manifestVersion: "1.0.0",
  issuedAt: "2026-01-01T00:00:00.000Z",
  grants,
});

const NOW = Date.parse("2026-02-01T00:00:00Z");

describe("MIG-CAP-GATEWAY-02 capability name <-> constraint contract", () => {
  it("declares every v1 capability name and denies names outside it", () => {
    expect(isKnownCapability("platform.commands")).toBe(true);
    expect(isKnownCapability("admin.superuser")).toBe(false);
    expect(isKnownCapability("")).toBe(false);
    expect(capabilityConstraintMismatch("admin.superuser", undefined)).toBe("unknown_capability");
    expect(capabilityConstraintMismatch("__proto__", undefined)).toBe("unknown_capability");
  });

  it("maps each capability to the constraint keys it actually enforces", () => {
    expect(constraintKeysFor("platform.commands")).toEqual(["methods"]);
    expect(constraintKeysFor("platform.queries")).toEqual(["methods"]);
    expect(constraintKeysFor("network.fetch")).toEqual(["domains", "methods"]);
    expect(constraintKeysFor("secrets.read")).toEqual(["resources"]);
    expect(unsupportedConstraintKeys("secrets.read", { methods: ["vault.read"] })).toEqual(["methods"]);
    expect(capabilityConstraintMismatch("secrets.read", { methods: ["vault.read"] })).toBe("unsupported_constraint:methods");
    expect(capabilityConstraintMismatch("platform.commands", { domains: ["api.example"] })).toBe("unsupported_constraint:domains");
    expect(capabilityConstraintMismatch("ui.render", { maxBytesPerRequest: 10 })).toBe("unsupported_constraint:maxBytesPerRequest");
    expect(capabilityConstraintMismatch("platform.commands", { methods: ["command.run"] })).toBeUndefined();
  });

  it("denies an unknown capability at the boundary even when a grant names it", () => {
    const boundary = createCapabilityBoundary(
      manifest([]),
      grantSet([]),
      NOW,
    );
    expect(boundary.effective.size).toBe(0);
    const unknown = createCapabilityBoundary(
      { ...manifest([{ name: "admin.superuser" as never, reason: "escalate" }]) },
      grantSet([{ name: "admin.superuser" as never, decision: "granted", grantedBy: "platform" }]),
      NOW,
    );
    expect(unknown.effective.size).toBe(0);
    expect(requireCapabilityOrCode(unknown, "admin.superuser" as never)).toBe("unknown_capability");
    expect(hasCapability(unknown, "admin.superuser" as never)).toBe(false);
  });

  it("denies a grant whose constraint key the capability does not support", () => {
    const unsupportedRequest = createCapabilityBoundary(
      manifest([{ name: "secrets.read", reason: "read a secret", constraints: { methods: ["vault.read"] } }]),
      grantSet([{ name: "secrets.read", decision: "granted", grantedBy: "administrator" }]),
      NOW,
    );
    expect(unsupportedRequest.effective.size).toBe(0);
    expect(denialReason(unsupportedRequest, "secrets.read")).toBe("unsupported_constraint");

    const unsupportedGrant = createCapabilityBoundary(
      manifest([{ name: "platform.commands", reason: "run a command", constraints: { methods: ["command.run"] } }]),
      grantSet([{ name: "platform.commands", decision: "granted", grantedBy: "administrator", constraints: { domains: ["api.example"] } }]),
      NOW,
    );
    expect(unsupportedGrant.effective.size).toBe(0);
    expect(denialReason(unsupportedGrant, "platform.commands")).toBe("unsupported_constraint");
  });

  it("denies an unbounded method-scoped grant instead of reading it as a broad allow", () => {
    const boundary = createCapabilityBoundary(
      manifest([{ name: "platform.commands", reason: "run any command" }]),
      grantSet([{ name: "platform.commands", decision: "granted", grantedBy: "administrator" }]),
      NOW,
    );
    expect(boundary.effective.size).toBe(0);
    expect(denialReason(boundary, "platform.commands")).toBe("unbounded_grant");
    expect(constraintsAllow(boundary, "platform.commands", "methods", "command.run")).toBe(false);
  });

  it("never reads an inapplicable constraint dimension as an allow", () => {
    const boundary = createCapabilityBoundary(
      manifest([{ name: "ui.render", reason: "render", constraints: { resources: ["screen"] } }]),
      grantSet([{ name: "ui.render", decision: "granted", grantedBy: "user", constraints: { resources: ["screen"] } }]),
      NOW,
    );
    expect(hasCapability(boundary, "ui.render")).toBe(true);
    // `ui.render` has no method dimension: a method check must not pass through "no constraint".
    expect(constraintsAllow(boundary, "ui.render", "methods", "anything")).toBe(false);
    expect(constraintsAllow(boundary, "ui.render", "resources", "screen")).toBe(true);
    expect(constraintsAllow(boundary, "ui.render", "resources", "other")).toBe(false);
  });

  it("denies intersect failures and requested-only capabilities", () => {
    const narrowing = createCapabilityBoundary(
      manifest([{ name: "platform.commands", reason: "run", constraints: { methods: ["command.run"] } }]),
      grantSet([{ name: "platform.commands", decision: "granted", grantedBy: "user", constraints: { methods: ["command.delete"] } }]),
      NOW,
    );
    expect(narrowing.effective.size).toBe(0);
    expect(denialReason(narrowing, "platform.commands")).toBe("constraint_denied");

    const requestedOnly = createCapabilityBoundary(
      manifest([{ name: "platform.commands", reason: "run", constraints: { methods: ["command.run"] } }]),
      grantSet([]),
      NOW,
    );
    expect(requestedOnly.effective.size).toBe(0);
    expect(denialReason(requestedOnly, "platform.commands")).toBe("not_granted");
  });

  it("rejects manifests whose constraint bag does not belong to the capability", () => {
    expect(() => parseAppManifest({ ...manifest([{ name: "secrets.read", reason: "read", constraints: { methods: ["vault.read"] } }]), entrypoints: {} })).toThrow("Manifest is missing or has invalid required fields");
    expect(() => parseAppManifest({ ...manifest([{ name: "unknown.capability" as never, reason: "read" }]), entrypoints: {} })).toThrow();
    expect(() => parseAppManifest({ ...manifest([{ name: "platform.commands", reason: "run", constraints: { methods: ["command.run"] } }]), entrypoints: {} })).not.toThrow();
  });
});

function requireCapabilityOrCode(boundary: ReturnType<typeof createCapabilityBoundary>, capability: Parameters<typeof requireCapability>[1]): string | undefined {
  try {
    requireCapability(boundary, capability);
    return undefined;
  } catch (error) {
    return error instanceof CapabilityDeniedError ? error.code : "unexpected";
  }
}
