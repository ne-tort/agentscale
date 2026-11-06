/**
 * Exact-origin policy for the two cross-origin peers of the web shell: the capability host
 * transport (plugin/app frame <-> shell RPC) and the isolated code-plugin host.
 *
 * Production accepts one exact peer origin and one exact `WindowProxy`. There is no wildcard and no
 * "unknown origin" fallback: a peer is either the registered origin or it is not a peer. Preview is
 * a separate policy with its own allow-list that must not overlap production origins or the shell
 * origin/site; only preview may opt into an opaque (`sandbox` without `allow-same-origin`) origin,
 * and even then acceptance is bound to the exact frame `WindowProxy` plus the instance nonce.
 */

export type OriginPolicyMode = "production" | "preview";

export type OriginPolicyErrorCode =
  | "origin_required"
  | "origin_invalid"
  | "origin_insecure"
  | "origin_not_exact"
  | "wildcard_origin_denied"
  | "origin_list_invalid"
  | "peer_origin_not_allowlisted"
  | "peer_origin_is_production"
  | "preview_allowlist_required"
  | "preview_allowlist_not_isolated"
  | "preview_shares_shell_origin"
  | "preview_shares_shell_site"
  | "opaque_origin_denied_in_production"
  | "origin_policy_mode_invalid";

export class OriginPolicyError extends Error {
  readonly code: OriginPolicyErrorCode;

  constructor(code: OriginPolicyErrorCode, detail?: string) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "OriginPolicyError";
    this.code = code;
  }
}

export interface PeerOriginPolicyInput {
  /** Exact origin of the peer (already derived from the peer URL by the caller). */
  readonly peerOrigin: string;
  /** Defaults to `production`: fail closed for anything that does not opt into preview. */
  readonly mode?: OriginPolicyMode;
  /** Production deployment origins. Required; wildcard entries are rejected. */
  readonly allowedOrigins?: ReadonlySet<string> | readonly string[];
  /** Preview-only allow-list. Required in preview mode; must be disjoint from production. */
  readonly previewOrigins?: ReadonlySet<string> | readonly string[];
  /** Shell origin, used to prove preview isolation from shell cookies/storage. */
  readonly shellOrigin?: string;
  /** Preview only: accept the opaque `"null"` origin produced by a sandboxed frame. */
  readonly allowOpaqueOrigin?: boolean;
}

export interface PeerOriginPolicy {
  readonly mode: OriginPolicyMode;
  readonly peerOrigin: string;
  /** True only for preview with an explicit opaque-origin exception. */
  readonly allowsOpaqueOrigin: boolean;
  /** Exact `postMessage` target origin; `"*"` only for the opaque preview exception. */
  readonly targetOrigin: string;
  /** True when this policy will accept a message with the given `event.origin`. */
  accepts(origin: unknown): boolean;
}

const OPAQUE_ORIGIN = "null";
const WILDCARD = "*";

/** Parses an exact `https://host[:port]` origin. Wildcards, paths, queries, hashes and userinfo are rejected. */
export function parseExactOrigin(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new OriginPolicyError("origin_required");
  const candidate = value.trim().replace(/\/$/, "");
  if (candidate === WILDCARD) throw new OriginPolicyError("wildcard_origin_denied", value);
  if (candidate === OPAQUE_ORIGIN) throw new OriginPolicyError("origin_invalid", "opaque origin is not a registrable origin");
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new OriginPolicyError("origin_invalid", value);
  }
  if (url.protocol !== "https:") throw new OriginPolicyError("origin_insecure", value);
  if (url.username || url.password) throw new OriginPolicyError("origin_invalid", "credentials are not allowed in an origin");
  if (url.origin !== candidate) throw new OriginPolicyError("origin_not_exact", value);
  return url.origin;
}

/** Validates a configured allow-list. Any wildcard or non-exact entry invalidates the whole list. */
export function normalizeOriginAllowList(
  values: ReadonlySet<string> | readonly string[] | undefined,
  label: string,
): ReadonlySet<string> {
  const list = values instanceof Set ? [...values] : Array.isArray(values) ? [...values] : undefined;
  if (!list) throw new OriginPolicyError("origin_list_invalid", `${label} is required`);
  const normalized = new Set<string>();
  for (const value of list) {
    if (value === WILDCARD || value === OPAQUE_ORIGIN) throw new OriginPolicyError("wildcard_origin_denied", `${label} entry ${String(value)}`);
    let origin: string;
    try {
      origin = parseExactOrigin(value);
    } catch (error) {
      const code = error instanceof OriginPolicyError ? error.code : "origin_invalid";
      throw new OriginPolicyError("origin_list_invalid", `${label} entry ${String(value)} (${code})`);
    }
    normalized.add(origin);
  }
  return normalized;
}

function hostname(origin: string): string {
  try {
    return new URL(origin).hostname.toLowerCase();
  } catch {
    return "";
  }
}

/**
 * Approximate same-site test (last two labels, plus subdomain containment). It is deliberately
 * conservative: when the shell origin is supplied, a preview origin that could share cookies with
 * the shell is rejected rather than quietly allowed.
 */
export function sameSite(left: string, right: string): boolean {
  const a = hostname(left);
  const b = hostname(right);
  if (!a || !b) return true;
  if (a === b) return true;
  if (a.endsWith(`.${b}`) || b.endsWith(`.${a}`)) return true;
  const isIp = (value: string): boolean => /^\d{1,3}(?:\.\d{1,3}){3}$/.test(value) || value.startsWith("[");
  if (isIp(a) || isIp(b) || a === "localhost" || b === "localhost") return false;
  const tail = (value: string): string => value.split(".").slice(-2).join(".");
  return tail(a) === tail(b);
}

function assertPreviewIsolated(preview: ReadonlySet<string>, production: ReadonlySet<string>, shellOrigin: string | undefined): void {
  for (const origin of preview) {
    if (production.has(origin)) throw new OriginPolicyError("preview_allowlist_not_isolated", origin);
    if (shellOrigin !== undefined) {
      if (origin === shellOrigin) throw new OriginPolicyError("preview_shares_shell_origin", origin);
      if (sameSite(origin, shellOrigin)) throw new OriginPolicyError("preview_shares_shell_site", origin);
    }
  }
}

/**
 * Resolves the policy for one peer. Production requires an exact allow-listed origin; preview
 * requires its own non-overlapping allow-list and is the only mode that may accept an opaque
 * sandbox origin.
 */
export function resolvePeerOriginPolicy(input: PeerOriginPolicyInput): PeerOriginPolicy {
  const mode = input.mode ?? "production";
  if (mode !== "production" && mode !== "preview") throw new OriginPolicyError("origin_policy_mode_invalid", String(mode));
  const peerOrigin = parseExactOrigin(input.peerOrigin);
  const production = input.allowedOrigins === undefined ? new Set<string>() : normalizeOriginAllowList(input.allowedOrigins, "allowedOrigins");
  const shellOrigin = input.shellOrigin === undefined ? undefined : parseExactOrigin(input.shellOrigin);

  if (mode === "production") {
    if (input.allowOpaqueOrigin === true) throw new OriginPolicyError("opaque_origin_denied_in_production", peerOrigin);
    if (!production.has(peerOrigin)) throw new OriginPolicyError("peer_origin_not_allowlisted", peerOrigin);
    return Object.freeze({
      mode,
      peerOrigin,
      allowsOpaqueOrigin: false,
      targetOrigin: peerOrigin,
      accepts: (origin: unknown) => origin === peerOrigin,
    });
  }

  const preview = input.previewOrigins === undefined ? new Set<string>() : normalizeOriginAllowList(input.previewOrigins, "previewOrigins");
  if (preview.size === 0) throw new OriginPolicyError("preview_allowlist_required", peerOrigin);
  assertPreviewIsolated(preview, production, shellOrigin);
  if (production.has(peerOrigin)) throw new OriginPolicyError("peer_origin_is_production", peerOrigin);
  if (!preview.has(peerOrigin)) throw new OriginPolicyError("peer_origin_not_allowlisted", peerOrigin);
  const allowsOpaqueOrigin = input.allowOpaqueOrigin === true;
  return Object.freeze({
    mode,
    peerOrigin,
    allowsOpaqueOrigin,
    // A sandboxed opaque frame cannot be addressed by an exact target origin; the exception is
    // preview-only and the message payload carries no credential material.
    targetOrigin: allowsOpaqueOrigin ? WILDCARD : peerOrigin,
    accepts: (origin: unknown) => origin === peerOrigin || (allowsOpaqueOrigin && origin === OPAQUE_ORIGIN),
  });
}
