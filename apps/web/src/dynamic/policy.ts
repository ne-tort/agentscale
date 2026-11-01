import { validateRouteRequirements, type ContextDimension, type RouteRequirements, type ValidatedRoute } from "./schema";
import type { RequestContext } from "./protocol";
import { missingContext } from "./protocol";

export type RouteDecision =
  | { readonly allowed: true; readonly route: ValidatedRoute }
  | { readonly allowed: false; readonly code: "missing_context" | "route_scope_mismatch" | "capability_denied" };

export interface RoutePolicyInput {
  readonly appTenantId: string;
  readonly appCabinetId?: string;
  readonly appProjectId?: string;
  readonly requirements: RouteRequirements;
  readonly effectiveCapabilities: ReadonlySet<string>;
}

export function evaluateRoute(context: RequestContext, route: ValidatedRoute, policy: RoutePolicyInput): RouteDecision {
  if (!validateRouteRequirements(policy.requirements) || route.requirements !== policy.requirements) return { allowed: false, code: "route_scope_mismatch" };
  const missing = missingContext(context, policy.requirements.context);
  if (missing.length) return { allowed: false, code: "missing_context" };
  if (context.tenantId !== policy.appTenantId || (policy.appCabinetId !== undefined && context.cabinetId !== policy.appCabinetId) || (policy.appProjectId !== undefined && context.projectId !== policy.appProjectId)) return { allowed: false, code: "route_scope_mismatch" };
  if ((policy.requirements.capabilities ?? []).some((capability) => !policy.effectiveCapabilities.has(capability))) return { allowed: false, code: "capability_denied" };
  return { allowed: true, route };
}

export function defaultRouteRequirements(): RouteRequirements {
  const context: readonly ContextDimension[] = ["tenantId"];
  return Object.freeze({ context });
}
