import { deepFreeze, validateRoute, validateRouteRequirements, type ContextDimension, type RouteRequirements, type ValidatedRoute } from "./schema";
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
  const ids = new Set([route.root]);
  if (!validateRouteRequirements(policy.requirements) || !validateRoute(route, ids) || !sameRequirements(route.requirements, policy.requirements)) return { allowed: false, code: "route_scope_mismatch" };
  const missing = missingContext(context, policy.requirements.context);
  if (missing.length) return { allowed: false, code: "missing_context" };
  if (!policy.appTenantId || context.tenantId !== policy.appTenantId ||
      (policy.appCabinetId !== undefined && (!context.cabinetId || context.cabinetId !== policy.appCabinetId)) ||
      (policy.appProjectId !== undefined && (!context.projectId || context.projectId !== policy.appProjectId))) return { allowed: false, code: "route_scope_mismatch" };
  if ((policy.requirements.capabilities ?? []).some((capability) => !policy.effectiveCapabilities.has(capability))) return { allowed: false, code: "capability_denied" };
  return { allowed: true, route: deepFreeze({ ...route, requirements: { context: [...route.requirements.context], ...(route.requirements.capabilities ? { capabilities: [...route.requirements.capabilities] } : {}) } }) };
}

function sameRequirements(left: RouteRequirements, right: RouteRequirements): boolean {
  const same = (a: readonly string[] | undefined, b: readonly string[] | undefined) =>
    (a ?? []).length === (b ?? []).length && (a ?? []).every((value, index) => value === (b ?? [])[index]);
  return same(left.context, right.context) && same(left.capabilities, right.capabilities);
}

export function defaultRouteRequirements(): RouteRequirements {
  const context: readonly ContextDimension[] = ["tenantId"];
  return Object.freeze({ context });
}
