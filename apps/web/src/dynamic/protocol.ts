import { deepFreeze, type ContextDimension } from "./schema";

export interface RequestContextInput {
  readonly tenantId: string;
  readonly cabinetId?: string;
  readonly projectId?: string;
  readonly sessionId?: string;
  readonly traceId: string;
  readonly requestId?: string;
}
export type RequestContext = Readonly<RequestContextInput>;
export type ArtifactErrorCode = "unknown_app" | "unknown_route" | "invalid_app" | "capability_denied" | "stale_revision" | "validation_error" | "missing_context";
export interface ArtifactError {
  readonly state: "error";
  readonly code: ArtifactErrorCode;
  readonly traceId: string;
}

export function createRequestContext(input: RequestContextInput): RequestContext {
  const allowed = new Set(["tenantId", "cabinetId", "projectId", "sessionId", "traceId", "requestId"]);
  if (Object.keys(input).some((key) => !allowed.has(key))) throw new TypeError("invalid_context_shape");
  for (const field of ["tenantId", "traceId"] as const) if (typeof input[field] !== "string" || !input[field].trim()) throw new TypeError(`missing_${field}`);
  for (const field of ["cabinetId", "projectId", "sessionId", "requestId"] as const) if (input[field] !== undefined && (typeof input[field] !== "string" || !input[field]!.trim())) throw new TypeError(`invalid_${field}`);
  const snapshot: RequestContextInput = {
    tenantId: input.tenantId,
    traceId: input.traceId,
    ...(input.cabinetId === undefined ? {} : { cabinetId: input.cabinetId }),
    ...(input.projectId === undefined ? {} : { projectId: input.projectId }),
    ...(input.sessionId === undefined ? {} : { sessionId: input.sessionId }),
    ...(input.requestId === undefined ? {} : { requestId: input.requestId }),
  };
  return deepFreeze(snapshot);
}

export function missingContext(context: RequestContext, dimensions: readonly ContextDimension[]): ContextDimension[] {
  return dimensions.filter((dimension) => !context[dimension]);
}
