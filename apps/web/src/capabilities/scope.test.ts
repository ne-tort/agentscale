import { describe, expect, it } from "vitest";
import {
  ScopeNotAuthoritativeError,
  isVerifiedScope,
  missingScopeDimensions,
  requireAuthoritativeScope,
  scopeAuthorizes,
  scopeMismatch,
  shellScope,
  verifyScope,
  type ScopeAttestation,
} from "./scope";

const attestation: ScopeAttestation = {
  bridge: "web-api.session",
  attestationId: "att-00000001",
  verifiedAt: "2026-11-04T00:00:00.000Z",
  requestId: "request-0001",
};

function codeOf(run: () => unknown): string | undefined {
  try {
    run();
    return undefined;
  } catch (error) {
    return error instanceof ScopeNotAuthoritativeError ? error.code : "unexpected";
  }
}

describe("MIG-CAP-GATEWAY-02 UX scope versus proven scope", () => {
  it("marks shell/work context as unverified even when every dimension is present", () => {
    const scope = shellScope({ tenantId: "tenant-1", cabinetId: "cabinet-1", projectId: "project-1", sessionId: "session-1" });
    expect(scope.trust).toBe("unverified");
    expect(scope.attestationId).toBeUndefined();
    expect(isVerifiedScope(scope)).toBe(false);
    expect(scopeAuthorizes(scope, ["tenantId"])).toBe(false);
    expect(codeOf(() => requireAuthoritativeScope(scope, ["tenantId"]))).toBe("scope_unverified");
  });

  it("cannot be promoted by a caller-supplied trust marker", () => {
    const forged = shellScope({ tenantId: "tenant-1", trust: "server-verified", attestationId: "att-forged" } as never);
    expect(forged.trust).toBe("unverified");
    expect(forged.attestationId).toBeUndefined();
    expect(scopeAuthorizes(forged, ["tenantId"])).toBe(false);
  });

  it("requires server-bridge evidence to mint a verified scope", () => {
    expect(codeOf(() => verifyScope({ tenantId: "tenant-1" }, { ...attestation, attestationId: "short" }))).toBe("scope_attestation_invalid");
    expect(codeOf(() => verifyScope({ tenantId: "tenant-1" }, { ...attestation, bridge: "" }))).toBe("scope_attestation_invalid");
    expect(codeOf(() => verifyScope({ tenantId: "tenant-1" }, { ...attestation, verifiedAt: "not-a-date" }))).toBe("scope_attestation_invalid");

    const verified = verifyScope({ tenantId: "tenant-1", cabinetId: "cabinet-1" }, attestation);
    expect(verified.trust).toBe("server-verified");
    expect(verified.attestationId).toBe("att-00000001");
    expect(verified.attestedAt).toBe(attestation.verifiedAt);
    expect(isVerifiedScope(verified)).toBe(true);
    expect(scopeAuthorizes(verified, ["tenantId", "cabinetId"])).toBe(true);
  });

  it("denies an operation when a dimension the operation needs is missing from a verified scope", () => {
    const verified = verifyScope({ tenantId: "tenant-1" }, attestation);
    expect(missingScopeDimensions(verified, ["tenantId", "projectId"])).toEqual(["projectId"]);
    expect(codeOf(() => requireAuthoritativeScope(verified, ["projectId"]))).toBe("scope_missing");
    expect(codeOf(() => requireAuthoritativeScope(undefined, ["tenantId"]))).toBe("scope_unverified");
  });

  it("treats a plugin/claim scope mismatch as a denial", () => {
    const verified = verifyScope({ tenantId: "tenant-1", cabinetId: "cabinet-1" }, attestation);
    expect(scopeMismatch({ cabinetId: "cabinet-1" }, verified)).toEqual([]);
    expect(scopeMismatch({ cabinetId: "cabinet-2" }, verified)).toEqual(["cabinetId"]);
    expect(scopeMismatch({ tenantId: "tenant-2" }, verified)).toEqual(["tenantId"]);
    expect(scopeMismatch({ projectId: "project-1" }, undefined)).toEqual(["projectId"]);
  });

  it("rejects malformed scope identifiers instead of coercing them", () => {
    expect(() => shellScope({ tenantId: "tenant 1" })).toThrow("invalid_tenantId");
    expect(() => shellScope({ tenantId: "tenant-1", sessionId: "a".repeat(300) })).toThrow("invalid_sessionId");
    expect(shellScope({ tenantId: "   " }).tenantId).toBeUndefined();
  });
});
