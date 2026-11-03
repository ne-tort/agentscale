import type {
  CapabilityConstraints,
  CapabilityGrant,
  CapabilityName,
  CapabilityRequest,
  DeclarativeAppManifest,
  GrantSet,
} from "../../../../packages/schemas/declarative-app/v1";
import {
  CONSTRAINT_KEYS,
  METHOD_SCOPED_CAPABILITIES,
  capabilityAcceptsConstraint,
  capabilityConstraintMismatch,
  isKnownCapability,
  unsupportedConstraintKeys,
  type ConstraintKey,
} from "./contracts";
import type { CapabilityBoundary, EffectiveCapability } from "./types";

export type CapabilityDenialCode =
  | "not_granted"
  | "unknown_capability"
  | "unsupported_constraint"
  | "unbounded_grant"
  | "binding_mismatch"
  | "constraint_denied"
  | "scope_required"
  | "malformed_grant_set";

export class CapabilityDeniedError extends Error {
  readonly code: CapabilityDenialCode;

  constructor(public readonly capability: CapabilityName, reason = "Capability is not granted", code: CapabilityDenialCode = "not_granted") {
    super(`${reason}: ${capability}`);
    this.name = "CapabilityDeniedError";
    this.code = code;
  }
}

const listKeys = ["resources", "methods", "domains", "eventTypes"] as const;

function validDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

/** Validates the constraint bag shape and rejects keys the capability does not understand. */
function constraintsProblem(name: string, value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)) return "invalid_constraints";
  const constraints = value as CapabilityConstraints;
  for (const key of listKeys) {
    const list = constraints[key];
    if (list !== undefined && (!Array.isArray(list) || list.some((item) => typeof item !== "string" || !item.trim() || item.length > 256))) return "invalid_constraints";
  }
  if (constraints.maxBytesPerRequest !== undefined && !(Number.isFinite(constraints.maxBytesPerRequest) && constraints.maxBytesPerRequest >= 0)) return "invalid_constraints";
  const mismatch = capabilityConstraintMismatch(name, constraints);
  return mismatch === undefined ? undefined : mismatch;
}

function requestProblem(request: unknown): string | undefined {
  if (!request || typeof request !== "object") return "invalid_request";
  const value = request as CapabilityRequest;
  if (!isKnownCapability(value.name)) return "unknown_capability";
  if (typeof value.reason !== "string" || value.reason.length === 0) return "invalid_request";
  if (value.optional !== undefined && typeof value.optional !== "boolean") return "invalid_request";
  return constraintsProblem(value.name, value.constraints);
}

function grantProblem(grant: unknown, now: number): string | undefined {
  if (!grant || typeof grant !== "object") return "invalid_grant";
  const value = grant as CapabilityGrant;
  if (!isKnownCapability(value.name)) return "unknown_capability";
  if (value.decision !== "granted" && value.decision !== "denied") return "invalid_grant";
  if (!["platform", "administrator", "user"].includes(value.grantedBy)) return "invalid_grant";
  if (value.expiresAt !== undefined && !validDate(value.expiresAt)) return "invalid_grant";
  if (value.revokedAt !== undefined && !validDate(value.revokedAt)) return "invalid_grant";
  const problem = constraintsProblem(value.name, value.constraints);
  return problem;
}

function validManifest(manifest: DeclarativeAppManifest): string | undefined {
  if (typeof manifest.appId !== "string" || manifest.appId.length === 0) return "invalid_manifest";
  if (typeof manifest.version !== "string" || manifest.version.length === 0) return "invalid_manifest";
  if (!Array.isArray(manifest.requestedCapabilities)) return "invalid_manifest";
  for (const request of manifest.requestedCapabilities) {
    const problem = requestProblem(request);
    if (problem !== undefined) return problem;
  }
  return undefined;
}

function validGrantSet(grantSet: GrantSet, now: number): string | undefined {
  if (typeof grantSet.appId !== "string" || typeof grantSet.manifestVersion !== "string") return "malformed_grant_set";
  if (!validDate(grantSet.issuedAt) || Date.parse(grantSet.issuedAt) > now) return "malformed_grant_set";
  if (grantSet.expiresAt !== undefined && (!validDate(grantSet.expiresAt) || Date.parse(grantSet.expiresAt) <= now)) return "malformed_grant_set";
  if (!Array.isArray(grantSet.grants)) return "malformed_grant_set";
  for (const grant of grantSet.grants) {
    const problem = grantProblem(grant, now);
    if (problem !== undefined) return problem;
  }
  if (new Set(grantSet.grants.map((grant) => grant.name)).size !== grantSet.grants.length) return "malformed_grant_set";
  return undefined;
}

function intersectConstraints(requested?: CapabilityConstraints, granted?: CapabilityConstraints): CapabilityConstraints | undefined {
  if (!requested && !granted) return undefined;
  const result: CapabilityConstraints = {};
  for (const key of listKeys) {
    const requestValues = requested?.[key];
    const grantValues = granted?.[key];
    if (requestValues && grantValues) {
      const values = requestValues.filter((value) => grantValues.includes(value));
      if (values.length === 0) return undefined;
      result[key] = values;
    } else if (requestValues || grantValues) result[key] = [...(requestValues ?? grantValues ?? [])];
  }
  if (requested?.maxBytesPerRequest !== undefined || granted?.maxBytesPerRequest !== undefined) {
    result.maxBytesPerRequest = Math.min(requested?.maxBytesPerRequest ?? Infinity, granted?.maxBytesPerRequest ?? Infinity);
  }
  return result;
}

function hasCompatibleConstraints(requested?: CapabilityConstraints, granted?: CapabilityConstraints): boolean {
  for (const key of listKeys) {
    const requestValues = requested?.[key];
    const grantValues = granted?.[key];
    if (requestValues && grantValues && !requestValues.some((value) => grantValues.includes(value))) return false;
  }
  return requested?.maxBytesPerRequest === undefined || granted?.maxBytesPerRequest === undefined || requested.maxBytesPerRequest <= granted.maxBytesPerRequest;
}

/**
 * A method-scoped capability granted without a method list authorizes every registered method, so
 * it is treated as a malformed grant instead of a broad allow.
 */
function unboundedMethodScope(request: CapabilityRequest, grant: CapabilityGrant): boolean {
  if (!METHOD_SCOPED_CAPABILITIES.has(request.name)) return false;
  return !(grant.constraints?.methods?.length || request.constraints?.methods?.length);
}

export function createCapabilityBoundary(manifest: DeclarativeAppManifest, grantSet: GrantSet, now = Date.now()): CapabilityBoundary {
  const empty = Object.freeze({
    appId: manifest.appId,
    manifestVersion: manifest.version,
    requested: manifest.requestedCapabilities,
    effective: new Map<CapabilityName, EffectiveCapability>(),
    denied: new Map<CapabilityName, CapabilityDenialCode>(),
  });
  const manifestProblem = validManifest(manifest);
  if (manifestProblem !== undefined) return empty;
  const grantProblem = validGrantSet(grantSet, now);
  if (grantProblem !== undefined) return empty;
  if (grantSet.appId !== manifest.appId || grantSet.manifestVersion !== manifest.version) return empty;

  const effective = new Map<CapabilityName, EffectiveCapability>();
  const denied = new Map<CapabilityName, CapabilityDenialCode>();
  for (const request of manifest.requestedCapabilities) {
    const grant = grantSet.grants.find((candidate) => candidate.name === request.name);
    if (!grant) { denied.set(request.name, "not_granted"); continue; }
    if (grant.decision !== "granted") { denied.set(request.name, "not_granted"); continue; }
    if ((grant.expiresAt !== undefined && Date.parse(grant.expiresAt) <= now) || (grant.revokedAt !== undefined && Date.parse(grant.revokedAt) <= now)) { denied.set(request.name, "not_granted"); continue; }
    if (unboundedMethodScope(request, grant)) { denied.set(request.name, "unbounded_grant"); continue; }
    const mismatch = capabilityConstraintMismatch(request.name, request.constraints) ?? capabilityConstraintMismatch(request.name, grant.constraints);
    if (mismatch !== undefined) { denied.set(request.name, "unsupported_constraint"); continue; }
    if (!hasCompatibleConstraints(request.constraints, grant.constraints)) { denied.set(request.name, "constraint_denied"); continue; }
    const constraints = intersectConstraints(request.constraints, grant.constraints);
    if (request.constraints && !constraints) { denied.set(request.name, "constraint_denied"); continue; }
    effective.set(request.name, { name: request.name, constraints, source: grant.grantedBy, expiresAt: grant.expiresAt });
  }
  return { ...empty, effective, denied };
}

export function requireCapability(boundary: CapabilityBoundary | undefined, capability: CapabilityName): EffectiveCapability {
  if (!isKnownCapability(capability)) throw new CapabilityDeniedError(capability, "Unknown capability", "unknown_capability");
  const effective = boundary?.effective.get(capability);
  if (!effective) throw new CapabilityDeniedError(capability, boundary?.denied?.get(capability) ? `Capability is not effective (${boundary.denied.get(capability)})` : "Capability is not granted");
  return effective;
}

export function hasCapability(boundary: CapabilityBoundary | undefined, capability: CapabilityName): boolean {
  if (!isKnownCapability(capability)) return false;
  return boundary?.effective.has(capability) ?? false;
}

/**
 * True only when the effective grant lists `value` for a constraint the capability actually
 * supports. A constraint key the capability does not understand denies instead of allowing:
 * "no constraint" must never be read through an inapplicable dimension.
 */
export function constraintsAllow(boundary: CapabilityBoundary | undefined, capability: CapabilityName, key: ConstraintKey, value: string): boolean {
  if (!isKnownCapability(capability)) return false;
  const effective = boundary?.effective.get(capability);
  if (!effective) return false;
  if (!capabilityAcceptsConstraint(capability, key)) return false;
  if (unsupportedConstraintKeys(capability, effective.constraints).length) return false;
  const values = effective.constraints?.[key];
  return !values || values.includes(value);
}

export function denialReason(boundary: CapabilityBoundary | undefined, capability: CapabilityName): CapabilityDenialCode | undefined {
  if (!isKnownCapability(capability)) return "unknown_capability";
  if (boundary?.effective.has(capability)) return undefined;
  return boundary?.denied?.get(capability) ?? "not_granted";
}

export function constraintKeysFor(capability: CapabilityName): readonly ConstraintKey[] {
  return CONSTRAINT_KEYS.filter((key) => capabilityAcceptsConstraint(capability, key));
}
