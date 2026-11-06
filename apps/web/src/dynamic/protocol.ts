import {
  requireAuthoritativeScope,
  verifyScope,
  type ScopeAttestation,
  type ScopeDimension,
  type ScopedContext,
  type ScopeTrust,
} from "../capabilities/scope";
import type { ContextDimension } from "./schema";

export interface RequestContextInput {
  readonly tenantId: string;
  readonly cabinetId?: string;
  readonly projectId?: string;
  readonly sessionId?: string;
  readonly traceId: string;
  readonly requestId?: string;
}
export type RequestContext = Readonly<RequestContextInput> & {
  /** `unverified` for shell-read context; `server-verified` only via a bridge attestation. */
  readonly trust?: ScopeTrust;
  readonly attestationId?: string;
};
export type ArtifactErrorCode = "unknown_app" | "unknown_route" | "invalid_app" | "capability_denied" | "stale_revision" | "validation_error" | "missing_context" | "route_scope_mismatch" | "unverified_context" | "quarantined" | "correlation_mismatch" | "replay_detected";
export type ArtifactError =
  | { readonly state: "unknown_app"; readonly code: "unknown_app"; readonly traceId: string }
  | { readonly state: "unknown_route"; readonly code: "unknown_route"; readonly traceId: string }
  | { readonly state: "invalid"; readonly code: "invalid_app" | "validation_error"; readonly traceId: string }
  | { readonly state: "denied"; readonly code: "capability_denied" | "missing_context" | "route_scope_mismatch" | "unverified_context"; readonly traceId: string }
  | { readonly state: "stale"; readonly code: "stale_revision"; readonly traceId: string }
  | { readonly state: "quarantined"; readonly code: "quarantined" | "correlation_mismatch" | "replay_detected"; readonly traceId: string };

/** Contexts produced by `createServerVerifiedContext`; a plain object cannot forge the marker. */
const verifiedContexts = new WeakSet<object>();
const allowedKeys = new Set(["tenantId", "cabinetId", "projectId", "sessionId", "traceId", "requestId", "trust", "attestationId"]);

const SCOPE_DIMENSIONS: readonly ContextDimension[] = ["tenantId", "cabinetId", "projectId", "sessionId"];

function snapshotOf(input: RequestContextInput, trust: ScopeTrust, attestationId?: string): RequestContext {
  const snapshot: Record<string, string> = Object.create(null) as Record<string, string>;
  snapshot.tenantId = input.tenantId;
  snapshot.traceId = input.traceId;
  for (const field of ["cabinetId", "projectId", "sessionId", "requestId"] as const) {
    const value = input[field];
    if (value !== undefined) snapshot[field] = value;
  }
  snapshot.trust = trust;
  if (attestationId !== undefined) snapshot.attestationId = attestationId;
  return Object.freeze(snapshot) as RequestContext;
}

/**
 * Shell-read context. It is always `unverified`: a caller-supplied trust marker is ignored unless
 * the input object was minted by `createServerVerifiedContext` in this module.
 */
export function createRequestContext(input: RequestContextInput): RequestContext {
  let prototype: object | null;
  try { prototype = Object.getPrototypeOf(input); } catch { throw new TypeError("invalid_context_shape"); }
  if (typeof input !== "object" || input === null || Array.isArray(input) || (prototype !== Object.prototype && prototype !== null)) throw new TypeError("invalid_context_shape");
  if (Object.getOwnPropertySymbols(input).length > 0 || Object.getOwnPropertyNames(input).some((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    return !allowedKeys.has(key) || !descriptor || !("value" in descriptor) || !descriptor.enumerable;
  })) throw new TypeError("invalid_context_shape");
  for (const field of ["tenantId", "traceId"] as const) if (typeof input[field] !== "string" || !input[field].trim() || input[field].length > 256) throw new TypeError(`missing_${field}`);
  for (const field of ["cabinetId", "projectId", "sessionId"] as const) if (input[field] !== undefined && (typeof input[field] !== "string" || !input[field]!.trim() || input[field]!.length > 256)) throw new TypeError(`invalid_${field}`);
  if (input.requestId !== undefined && (typeof input.requestId !== "string" || !/^[A-Za-z0-9._:-]{8,128}$/.test(input.requestId))) throw new TypeError("invalid_requestId");
  const verified = verifiedContexts.has(input) && (input as RequestContext).trust === "server-verified";
  const context = snapshotOf(input, verified ? "server-verified" : "unverified", verified ? (input as RequestContext).attestationId : undefined);
  if (verified) verifiedContexts.add(context);
  return context;
}

/** Re-resolves the scope through the server bridge; the only way to obtain a verified context. */
export function createServerVerifiedContext(input: RequestContextInput, attestation: ScopeAttestation): RequestContext {
  if (attestation.requestId !== undefined && attestation.requestId !== input.requestId) throw new TypeError("attestation_request_mismatch");
  const base = snapshotOf(input, "unverified");
  const scope = verifyScope({
    tenantId: base.tenantId,
    cabinetId: base.cabinetId,
    projectId: base.projectId,
    sessionId: base.sessionId,
  }, attestation);
  const context = snapshotOf(input, "server-verified", scope.attestationId);
  verifiedContexts.add(context);
  return context;
}

export function isServerVerifiedContext(context: RequestContext | object | undefined): boolean {
  return Boolean(context && typeof context === "object" && verifiedContexts.has(context) && (context as RequestContext).trust === "server-verified");
}

export function contextTrust(context: RequestContext | undefined): ScopeTrust {
  return isServerVerifiedContext(context) ? "server-verified" : "unverified";
}

/** Projects a request context onto the capability-scope model used by the gateway/broker gates. */
export function contextScope(context: RequestContext): ScopedContext {
  const trust = contextTrust(context);
  const scope: { -readonly [K in ScopeDimension]?: string } & { trust: ScopeTrust; attestationId?: string } = { trust };
  for (const dimension of SCOPE_DIMENSIONS) {
    const value = context[dimension];
    if (typeof value === "string" && value) scope[dimension] = value;
  }
  if (trust === "server-verified" && context.attestationId) scope.attestationId = context.attestationId;
  return Object.freeze(scope) as ScopedContext;
}

/**
 * Refuses an operation that needs authoritative scope when the context is only shell-read state.
 * Throws `ScopeNotAuthoritativeError` (`scope_unverified` / `scope_missing`).
 */
export function requireProvenContext(context: RequestContext | undefined, dimensions: readonly ContextDimension[]): RequestContext {
  if (!context) throw new TypeError("missing_context");
  requireAuthoritativeScope(contextScope(context), dimensions);
  return context;
}

export function missingContext(context: RequestContext, dimensions: readonly ContextDimension[]): ContextDimension[] {
  return dimensions.filter((dimension) => typeof context[dimension] !== "string" || !context[dimension]!.trim());
}
