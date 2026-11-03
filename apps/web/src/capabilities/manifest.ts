import type { CapabilityRequest, DeclarativeAppManifest, ProtocolVersion } from "../../../../packages/schemas/declarative-app/v1";
import { DECLARATIVE_APP_PROTOCOL_VERSION } from "../../../../packages/schemas/declarative-app/v1";
import { capabilityConstraintMismatch, isKnownCapability } from "./contracts";

export class InvalidManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidManifestError";
  }
}

function validRequest(request: Partial<CapabilityRequest>): boolean {
  if (!isKnownCapability(request.name)) return false;
  if (typeof request.reason !== "string" || request.reason.length === 0) return false;
  return capabilityConstraintMismatch(request.name, request.constraints) === undefined;
}

export function parseAppManifest(value: unknown): DeclarativeAppManifest {
  if (!value || typeof value !== "object") throw new InvalidManifestError("Manifest must be an object");
  const manifest = value as Partial<DeclarativeAppManifest>;
  if (manifest.protocol !== DECLARATIVE_APP_PROTOCOL_VERSION) throw new InvalidManifestError("Unsupported manifest protocol");
  if (typeof manifest.appId !== "string" || !manifest.appId || typeof manifest.version !== "string" || !manifest.version ||
      typeof manifest.displayName !== "string" || !manifest.displayName || !Array.isArray(manifest.requestedCapabilities) ||
      !manifest.requestedCapabilities.every((request) => request && typeof request === "object" && validRequest(request))) {
    throw new InvalidManifestError("Manifest is missing or has invalid required fields");
  }
  if (!manifest.entrypoints || typeof manifest.entrypoints !== "object") throw new InvalidManifestError("Manifest entrypoints are required");
  return manifest as DeclarativeAppManifest;
}

export function isManifestProtocol(value: unknown): value is ProtocolVersion {
  return value === DECLARATIVE_APP_PROTOCOL_VERSION;
}

export async function loadAppManifest(response: Response): Promise<DeclarativeAppManifest> {
  if (!response.ok) throw new InvalidManifestError(`Manifest request failed with HTTP ${response.status}`);
  return parseAppManifest(await response.json());
}
