/**
 * Provenance model for tenant/cabinet/project/session scope in the web shell.
 *
 * The shell reads its scope from React state (`WorkContext`), which is UX input only: it is a
 * selection the user made in this tab. It is *not* evidence that the server resolved the same
 * tenant/cabinet/project/session for the current session. Until the server bridge re-resolves the
 * scope and returns an attestation, a scope is `unverified`, and every operation that needs
 * authoritative scope must refuse it.
 *
 * Nothing in this module is an authorization boundary by itself. The server remains authoritative;
 * this is the client-side gate that stops the shell from *pretending* it has server context.
 */

export const SCOPE_DIMENSIONS = Object.freeze(["tenantId", "cabinetId", "projectId", "sessionId"] as const);
export type ScopeDimension = (typeof SCOPE_DIMENSIONS)[number];

export type ScopeTrust = "unverified" | "server-verified";

export interface ScopeInput {
  readonly tenantId?: string;
  readonly cabinetId?: string;
  readonly projectId?: string;
  readonly sessionId?: string;
}

export interface ScopedContext extends ScopeInput {
  readonly trust: ScopeTrust;
  /** Present only for `server-verified` scopes: the bridge attestation this scope came from. */
  readonly attestationId?: string;
  /** Present only for `server-verified` scopes: when the server bridge resolved the scope. */
  readonly attestedAt?: string;
}

/** Evidence returned by the server bridge when it resolves the scope for the current session. */
export interface ScopeAttestation {
  readonly bridge: string;
  readonly attestationId: string;
  readonly verifiedAt: string;
  readonly requestId?: string;
}

export type ScopeDenialCode = "scope_unverified" | "scope_missing" | "scope_attestation_invalid";

export class ScopeNotAuthoritativeError extends Error {
  readonly code: ScopeDenialCode;
  readonly dimensions: readonly ScopeDimension[];

  constructor(code: ScopeDenialCode, dimensions: readonly ScopeDimension[] = []) {
    super(dimensions.length ? `${code}: ${dimensions.join(",")}` : code);
    this.name = "ScopeNotAuthoritativeError";
    this.code = code;
    this.dimensions = Object.freeze([...dimensions]);
  }
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$/;
const ATTESTATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const BRIDGE_ID = /^[a-z][a-z0-9._-]{2,63}$/;
const DIMENSIONS = new Set<string>(SCOPE_DIMENSIONS);

export function isScopeDimension(value: unknown): value is ScopeDimension {
  return typeof value === "string" && DIMENSIONS.has(value);
}

function normalizeIdentifier(value: unknown, field: ScopeDimension): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new TypeError(`invalid_${field}`);
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (!IDENTIFIER.test(trimmed)) throw new TypeError(`invalid_${field}`);
  return trimmed;
}

function copyScope(input: ScopeInput | undefined): ScopeInput {
  if (input === undefined) return Object.freeze({});
  if (typeof input !== "object" || input === null || Array.isArray(input)) throw new TypeError("invalid_scope_shape");
  const scope: { -readonly [K in ScopeDimension]?: string } = {};
  for (const dimension of SCOPE_DIMENSIONS) {
    const value = normalizeIdentifier(input[dimension], dimension);
    if (value !== undefined) scope[dimension] = value;
  }
  return Object.freeze(scope);
}

/**
 * Shell-selected scope. Always `unverified`, regardless of what the caller claims: a trust marker
 * supplied by the caller is discarded instead of being promoted.
 */
export function shellScope(input?: ScopeInput): ScopedContext {
  return Object.freeze({ ...copyScope(input), trust: "unverified" as const });
}

function validAttestation(attestation: ScopeAttestation | undefined): attestation is ScopeAttestation {
  if (!attestation || typeof attestation !== "object") return false;
  const { bridge, attestationId, verifiedAt, requestId } = attestation as ScopeAttestation;
  return typeof bridge === "string" && BRIDGE_ID.test(bridge) &&
    typeof attestationId === "string" && ATTESTATION_ID.test(attestationId) &&
    typeof verifiedAt === "string" && Number.isFinite(Date.parse(verifiedAt)) &&
    (requestId === undefined || (typeof requestId === "string" && ATTESTATION_ID.test(requestId)));
}

/** Scope re-resolved by the server bridge. Refuses to mint a verified scope without evidence. */
export function verifyScope(input: ScopeInput | undefined, attestation: ScopeAttestation): ScopedContext {
  if (!validAttestation(attestation)) throw new ScopeNotAuthoritativeError("scope_attestation_invalid");
  const scope = copyScope(input);
  return Object.freeze({
    ...scope,
    trust: "server-verified" as const,
    attestationId: attestation.attestationId,
    attestedAt: attestation.verifiedAt,
  });
}

export function isVerifiedScope(value: ScopedContext | undefined): value is ScopedContext & { readonly trust: "server-verified" } {
  return Boolean(value && value.trust === "server-verified" && typeof value.attestationId === "string" && Boolean(value.attestationId));
}

export function missingScopeDimensions(value: ScopedContext | undefined, dimensions: readonly ScopeDimension[]): ScopeDimension[] {
  if (!value) return [...dimensions];
  return dimensions.filter((dimension) => typeof value[dimension] !== "string" || !value[dimension]!.trim());
}

/**
 * Gate for operations that need authoritative scope. An unverified (shell) context always fails,
 * even when every dimension happens to be present.
 */
export function requireAuthoritativeScope(value: ScopedContext | undefined, dimensions: readonly ScopeDimension[] = []): ScopedContext {
  if (!isVerifiedScope(value)) throw new ScopeNotAuthoritativeError("scope_unverified", dimensions);
  const missing = missingScopeDimensions(value, dimensions);
  if (missing.length) throw new ScopeNotAuthoritativeError("scope_missing", missing);
  return value;
}

export function scopeAuthorizes(value: ScopedContext | undefined, dimensions: readonly ScopeDimension[]): boolean {
  try {
    requireAuthoritativeScope(value, dimensions);
    return true;
  } catch {
    return false;
  }
}

/** Compares only the dimensions both sides define; used to detect a plugin/claim swap. */
export function scopeMismatch(
  claimed: ScopeInput | undefined,
  proven: ScopedContext | undefined,
  dimensions: readonly ScopeDimension[] = SCOPE_DIMENSIONS,
): ScopeDimension[] {
  if (!claimed) return [];
  return dimensions.filter((dimension) => {
    const value = claimed[dimension];
    if (value === undefined) return false;
    return !proven || proven[dimension] !== value;
  });
}
