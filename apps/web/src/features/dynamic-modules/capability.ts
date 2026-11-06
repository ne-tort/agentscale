/**
 * Shell-side capability gate for the dynamic-module CRUD screens.
 *
 * The gate is a UX guard, never an authorization boundary: the gateway re-checks the
 * effective grant, tenant scope and resource ACL on every call. A missing boundary is
 * treated as "nothing granted" (fail closed), because an unauthenticated or unbound
 * shell must not render registry commands.
 */

import { CapabilityDeniedError, hasCapability } from "../../capabilities/boundary";
import type { CapabilityBoundary, CapabilityName } from "../../capabilities/types";

export type DynamicModuleOperation = "read" | "manage";

export const DYNAMIC_MODULE_CAPABILITIES: Readonly<Record<DynamicModuleOperation, CapabilityName>> = Object.freeze({
  read: "platform.queries",
  manage: "platform.commands",
});

export type DynamicModuleAccessReason = "granted" | "capability_denied" | "capability_context_unavailable";

export interface DynamicModuleAccess {
  readonly canRead: boolean;
  readonly canManage: boolean;
  readonly granted: readonly CapabilityName[];
  readonly missing: readonly CapabilityName[];
  readonly reason: DynamicModuleAccessReason;
}

/** Default when no `CapabilityBoundary` is mounted by the host: deny everything. */
export const DENY_DYNAMIC_MODULE_ACCESS: DynamicModuleAccess = Object.freeze({
  canRead: false,
  canManage: false,
  granted: Object.freeze([]) as readonly CapabilityName[],
  missing: Object.freeze([DYNAMIC_MODULE_CAPABILITIES.read, DYNAMIC_MODULE_CAPABILITIES.manage]) as readonly CapabilityName[],
  reason: "capability_context_unavailable",
});

/**
 * Projects an effective capability boundary onto the two operations these screens
 * perform. Reads require `platform.queries`; publish/install/grant/revoke/archive
 * require `platform.commands`.
 */
export function createDynamicModuleAccess(boundary?: CapabilityBoundary): DynamicModuleAccess {
  if (!boundary) return DENY_DYNAMIC_MODULE_ACCESS;
  const canRead = hasCapability(boundary, DYNAMIC_MODULE_CAPABILITIES.read);
  const canManage = hasCapability(boundary, DYNAMIC_MODULE_CAPABILITIES.manage);
  const granted = [DYNAMIC_MODULE_CAPABILITIES.read, DYNAMIC_MODULE_CAPABILITIES.manage].filter((capability) =>
    hasCapability(boundary, capability),
  );
  const missing = [DYNAMIC_MODULE_CAPABILITIES.read, DYNAMIC_MODULE_CAPABILITIES.manage].filter((capability) =>
    !hasCapability(boundary, capability),
  );
  return {
    canRead,
    canManage,
    granted,
    missing,
    reason: canRead && canManage ? "granted" : "capability_denied",
  };
}

/** Throws `CapabilityDeniedError` before any request is attempted. */
export function requireDynamicModuleAccess(access: DynamicModuleAccess, operation: DynamicModuleOperation): void {
  const allowed = operation === "read" ? access.canRead : access.canManage;
  if (!allowed) throw new CapabilityDeniedError(DYNAMIC_MODULE_CAPABILITIES[operation]);
}

/** True when the operation is refused only because the host mounted no boundary. */
export function isCapabilityContextMissing(access: DynamicModuleAccess): boolean {
  return access.reason === "capability_context_unavailable";
}
