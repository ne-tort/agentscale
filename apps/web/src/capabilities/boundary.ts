import type {
  CapabilityConstraints,
  CapabilityGrant,
  CapabilityName,
  CapabilityRequest,
  DeclarativeAppManifest,
  GrantSet,
} from "../../../../packages/schemas/declarative-app/v1";
import type { CapabilityBoundary, EffectiveCapability } from "./types";

export class CapabilityDeniedError extends Error {
  constructor(public readonly capability: CapabilityName, reason = "Capability is not granted") {
    super(`${reason}: ${capability}`);
    this.name = "CapabilityDeniedError";
  }
}

const listKeys = ["resources", "methods", "domains", "eventTypes"] as const;
type ConstraintKey = (typeof listKeys)[number];
const capabilityNames = new Set<CapabilityName>([
  "ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch",
  "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read",
]);

function validDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function validConstraints(value: unknown): value is CapabilityConstraints | undefined {
  if (value === undefined) return true;
  if (!value || typeof value !== "object") return false;
  const constraints = value as CapabilityConstraints;
  for (const key of listKeys) {
    if (constraints[key] !== undefined && (!Array.isArray(constraints[key]) || constraints[key].some((item) => typeof item !== "string"))) return false;
  }
  return constraints.maxBytesPerRequest === undefined || (Number.isFinite(constraints.maxBytesPerRequest) && constraints.maxBytesPerRequest >= 0);
}

function validRequest(request: unknown): request is CapabilityRequest {
  if (!request || typeof request !== "object") return false;
  const value = request as CapabilityRequest;
  return capabilityNames.has(value.name) && typeof value.reason === "string" && value.reason.length > 0 &&
    (value.optional === undefined || typeof value.optional === "boolean") && validConstraints(value.constraints);
}

function validManifest(manifest: DeclarativeAppManifest): boolean {
  return typeof manifest.appId === "string" && manifest.appId.length > 0 && typeof manifest.version === "string" &&
    manifest.version.length > 0 && Array.isArray(manifest.requestedCapabilities) && manifest.requestedCapabilities.every(validRequest);
}

function validGrant(grant: unknown): grant is CapabilityGrant {
  if (!grant || typeof grant !== "object") return false;
  const value = grant as CapabilityGrant;
  return capabilityNames.has(value.name) && (value.decision === "granted" || value.decision === "denied") &&
    ["platform", "administrator", "user"].includes(value.grantedBy) && validConstraints(value.constraints) &&
    (value.expiresAt === undefined || validDate(value.expiresAt)) &&
     (value.revokedAt === undefined || validDate(value.revokedAt));
}

function validGrantSet(grantSet: GrantSet, now: number): boolean {
  return typeof grantSet.appId === "string" && typeof grantSet.manifestVersion === "string" && validDate(grantSet.issuedAt) &&
    Date.parse(grantSet.issuedAt) <= now && (grantSet.expiresAt === undefined || (validDate(grantSet.expiresAt) && Date.parse(grantSet.expiresAt) > now)) &&
    Array.isArray(grantSet.grants) && grantSet.grants.every(validGrant) &&
    new Set(grantSet.grants.map((grant) => grant.name)).size === grantSet.grants.length;
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

export function createCapabilityBoundary(manifest: DeclarativeAppManifest, grantSet: GrantSet, now = Date.now()): CapabilityBoundary {
  const empty = { appId: manifest.appId, manifestVersion: manifest.version, requested: manifest.requestedCapabilities, effective: new Map<CapabilityName, EffectiveCapability>() };
  if (!validManifest(manifest) || !validGrantSet(grantSet, now) || grantSet.appId !== manifest.appId || grantSet.manifestVersion !== manifest.version) return empty;
  const effective = new Map<CapabilityName, EffectiveCapability>();
  for (const request of manifest.requestedCapabilities) {
    const grant = grantSet.grants.find((candidate) => candidate.name === request.name);
    if (!grant || grant.decision !== "granted" || (grant.expiresAt !== undefined && Date.parse(grant.expiresAt) <= now) || (grant.revokedAt !== undefined && Date.parse(grant.revokedAt) <= now) || !hasCompatibleConstraints(request.constraints, grant.constraints)) continue;
    const constraints = intersectConstraints(request.constraints, grant.constraints);
    if (request.constraints && !constraints) continue;
    effective.set(request.name, { name: request.name, constraints, source: grant.grantedBy, expiresAt: grant.expiresAt });
  }
  return { ...empty, effective };
}

export function requireCapability(boundary: CapabilityBoundary | undefined, capability: CapabilityName): EffectiveCapability {
  const effective = boundary?.effective.get(capability);
  if (!effective) throw new CapabilityDeniedError(capability);
  return effective;
}

export function hasCapability(boundary: CapabilityBoundary | undefined, capability: CapabilityName): boolean {
  return boundary?.effective.has(capability) ?? false;
}

export function constraintsAllow(boundary: CapabilityBoundary | undefined, capability: CapabilityName, key: ConstraintKey, value: string): boolean {
  const effective = requireCapability(boundary, capability);
  const values = effective.constraints?.[key];
  return !values || values.includes(value);
}
