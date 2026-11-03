import type { ContextDimension } from "./schema";

export interface RequestContextInput {
  readonly tenantId: string;
  readonly cabinetId?: string;
  readonly projectId?: string;
  readonly sessionId?: string;
  readonly traceId: string;
  readonly requestId?: string;
}
export type RequestContext = Readonly<RequestContextInput>;
export type ArtifactErrorCode = "unknown_app" | "unknown_route" | "invalid_app" | "capability_denied" | "stale_revision" | "validation_error" | "missing_context" | "route_scope_mismatch" | "quarantined" | "correlation_mismatch" | "replay_detected";
export type ArtifactError =
  | { readonly state: "unknown_app"; readonly code: "unknown_app"; readonly traceId: string }
  | { readonly state: "unknown_route"; readonly code: "unknown_route"; readonly traceId: string }
  | { readonly state: "invalid"; readonly code: "invalid_app" | "validation_error"; readonly traceId: string }
  | { readonly state: "denied"; readonly code: "capability_denied" | "missing_context" | "route_scope_mismatch"; readonly traceId: string }
  | { readonly state: "stale"; readonly code: "stale_revision"; readonly traceId: string }
  | { readonly state: "quarantined"; readonly code: "quarantined" | "correlation_mismatch" | "replay_detected"; readonly traceId: string };

export function createRequestContext(input: RequestContextInput): RequestContext {
  let prototype: object | null;
  try { prototype = Object.getPrototypeOf(input); } catch { throw new TypeError("invalid_context_shape"); }
  if (typeof input !== "object" || input === null || Array.isArray(input) || (prototype !== Object.prototype && prototype !== null)) throw new TypeError("invalid_context_shape");
  const allowed = new Set(["tenantId", "cabinetId", "projectId", "sessionId", "traceId", "requestId"]);
  if (Object.getOwnPropertySymbols(input).length > 0 || Object.getOwnPropertyNames(input).some((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    return !allowed.has(key) || !descriptor || !("value" in descriptor) || !descriptor.enumerable;
  })) throw new TypeError("invalid_context_shape");
  for (const field of ["tenantId", "traceId"] as const) if (typeof input[field] !== "string" || !input[field].trim() || input[field].length > 256) throw new TypeError(`missing_${field}`);
  for (const field of ["cabinetId", "projectId", "sessionId"] as const) if (input[field] !== undefined && (typeof input[field] !== "string" || !input[field]!.trim() || input[field]!.length > 256)) throw new TypeError(`invalid_${field}`);
  if (input.requestId !== undefined && (typeof input.requestId !== "string" || !/^[A-Za-z0-9._:-]{8,128}$/.test(input.requestId))) throw new TypeError("invalid_requestId");
  const snapshot: Record<string, string> = Object.create(null) as Record<string, string>;
  snapshot.tenantId = input.tenantId;
  snapshot.traceId = input.traceId;
  for (const field of ["cabinetId", "projectId", "sessionId", "requestId"] as const) {
    const value = input[field];
    if (value !== undefined) snapshot[field] = value;
  }
  return Object.freeze(snapshot) as RequestContext;
}

export function missingContext(context: RequestContext, dimensions: readonly ContextDimension[]): ContextDimension[] {
  return dimensions.filter((dimension) => typeof context[dimension] !== "string" || !context[dimension]!.trim());
}
