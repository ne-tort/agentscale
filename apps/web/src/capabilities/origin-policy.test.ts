import { describe, expect, it } from "vitest";
import {
  OriginPolicyError,
  normalizeOriginAllowList,
  parseExactOrigin,
  resolvePeerOriginPolicy,
  sameSite,
} from "./origin-policy";

function codeOf(run: () => unknown): string | undefined {
  try {
    run();
    return undefined;
  } catch (error) {
    return error instanceof OriginPolicyError ? error.code : "unexpected";
  }
}

describe("MIG-CAP-GATEWAY-02 exact-origin policy", () => {
  it("denies a wildcard origin instead of treating it as a matcher", () => {
    expect(codeOf(() => parseExactOrigin("*"))).toBe("wildcard_origin_denied");
    expect(codeOf(() => resolvePeerOriginPolicy({ peerOrigin: "*", allowedOrigins: ["https://plugins.example"] }))).toBe("wildcard_origin_denied");
    expect(codeOf(() => normalizeOriginAllowList(["*"], "allowedOrigins"))).toBe("wildcard_origin_denied");
    expect(codeOf(() => normalizeOriginAllowList(["https://plugins.example", "*"], "allowedOrigins"))).toBe("wildcard_origin_denied");
    // A list containing a wildcard is invalid as a whole: one bad entry cannot leave the rest usable.
    expect(codeOf(() => resolvePeerOriginPolicy({ peerOrigin: "https://plugins.example", allowedOrigins: new Set(["https://plugins.example", "*"]) }))).toBe("wildcard_origin_denied");
  });

  it("denies insecure, non-exact and opaque origins in the production path", () => {
    expect(codeOf(() => parseExactOrigin("http://plugins.example"))).toBe("origin_insecure");
    expect(codeOf(() => parseExactOrigin("https://plugins.example/some/path"))).toBe("origin_not_exact");
    expect(codeOf(() => parseExactOrigin("https://plugins.example?token=x"))).toBe("origin_not_exact");
    expect(codeOf(() => parseExactOrigin("https://user:pass@plugins.example"))).toBe("origin_invalid");
    expect(codeOf(() => parseExactOrigin("null"))).toBe("origin_invalid");
    expect(codeOf(() => parseExactOrigin(""))).toBe("origin_required");
    expect(codeOf(() => resolvePeerOriginPolicy({ peerOrigin: "https://plugins.example", allowOpaqueOrigin: true, allowedOrigins: ["https://plugins.example"] }))).toBe("opaque_origin_denied_in_production");
  });

  it("allows exactly the registered production origin and nothing else", () => {
    const policy = resolvePeerOriginPolicy({ peerOrigin: "https://plugins.example", allowedOrigins: ["https://plugins.example"] });
    expect(policy.mode).toBe("production");
    expect(policy.targetOrigin).toBe("https://plugins.example");
    expect(policy.accepts("https://plugins.example")).toBe(true);
    expect(policy.accepts("https://plugins.example.evil.test")).toBe(false);
    expect(policy.accepts("https://plugins.example:8443")).toBe(false);
    expect(policy.accepts("null")).toBe(false);
    expect(policy.accepts(undefined)).toBe(false);
    expect(policy.allowsOpaqueOrigin).toBe(false);

    // Sub-domains and sibling ports are different origins, not "close enough".
    expect(codeOf(() => resolvePeerOriginPolicy({ peerOrigin: "https://evil.plugins.example", allowedOrigins: ["https://plugins.example"] }))).toBe("peer_origin_not_allowlisted");
    expect(codeOf(() => resolvePeerOriginPolicy({ peerOrigin: "https://plugins.example", allowedOrigins: [] }))).toBe("peer_origin_not_allowlisted");
  });

  it("requires an isolated preview allow-list, disjoint from production and the shell", () => {
    const shellOrigin = "https://app.example.com";
    const production = ["https://plugins.example"];

    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "preview", peerOrigin: "https://preview.plugins.invalid", previewOrigins: [], allowedOrigins: production, shellOrigin }))).toBe("preview_allowlist_required");
    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "preview", peerOrigin: "https://plugins.example", previewOrigins: ["https://plugins.example"], allowedOrigins: production, shellOrigin }))).toBe("preview_allowlist_not_isolated");
    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "preview", peerOrigin: "https://plugins.example", previewOrigins: ["https://preview.plugins.invalid"], allowedOrigins: production, shellOrigin }))).toBe("peer_origin_is_production");
    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "preview", peerOrigin: "https://app.example.com", previewOrigins: ["https://app.example.com"], allowedOrigins: production, shellOrigin }))).toBe("preview_shares_shell_origin");
    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "preview", peerOrigin: "https://preview.example.com", previewOrigins: ["https://preview.example.com"], allowedOrigins: production, shellOrigin }))).toBe("preview_shares_shell_site");
    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "preview", peerOrigin: "https://unknown.invalid", previewOrigins: ["https://preview.plugins.invalid"], allowedOrigins: production, shellOrigin }))).toBe("peer_origin_not_allowlisted");
    expect(codeOf(() => resolvePeerOriginPolicy({ mode: "staging" as never, peerOrigin: "https://preview.plugins.invalid", allowedOrigins: production }))).toBe("origin_policy_mode_invalid");
  });

  it("binds an isolated preview to the opaque sandbox origin and no credential-bearing wildcard elsewhere", () => {
    const preview = resolvePeerOriginPolicy({
      mode: "preview",
      peerOrigin: "https://preview.plugins.invalid",
      previewOrigins: ["https://preview.plugins.invalid"],
      allowedOrigins: ["https://plugins.example"],
      shellOrigin: "https://app.example.com",
      allowOpaqueOrigin: true,
    });
    expect(preview.mode).toBe("preview");
    expect(preview.allowsOpaqueOrigin).toBe(true);
    // A sandboxed opaque frame cannot be addressed by an exact target origin; this exception is
    // preview-only and relies on the exact WindowProxy plus instance nonce for acceptance.
    expect(preview.targetOrigin).toBe("*");
    expect(preview.accepts("null")).toBe(true);
    expect(preview.accepts("https://preview.plugins.invalid")).toBe(true);
    expect(preview.accepts("https://plugins.example")).toBe(false);

    const previewWithoutOpaque = resolvePeerOriginPolicy({
      mode: "preview",
      peerOrigin: "https://preview.plugins.invalid",
      previewOrigins: ["https://preview.plugins.invalid"],
      allowedOrigins: ["https://plugins.example"],
      shellOrigin: "https://app.example.com",
    });
    expect(previewWithoutOpaque.targetOrigin).toBe("https://preview.plugins.invalid");
    expect(previewWithoutOpaque.accepts("null")).toBe(false);
  });

  it("treats same-site conservatively so preview never shares shell cookies", () => {
    expect(sameSite("https://app.example.com", "https://app.example.com")).toBe(true);
    expect(sameSite("https://plugins.example.com", "https://app.example.com")).toBe(true);
    expect(sameSite("https://plugins.example.com", "https://example.org")).toBe(false);
    expect(sameSite("https://preview.invalid", "https://app.example.com")).toBe(false);
  });
});
