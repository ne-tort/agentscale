import type { CapabilityName, DeclarativeAppManifest, ProtocolVersion } from "../../../../packages/schemas/declarative-app/v1";
import { DECLARATIVE_APP_PROTOCOL_VERSION } from "../../../../packages/schemas/declarative-app/v1";

export class InvalidManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidManifestError";
  }
}

const capabilityNames = new Set<CapabilityName>([
  "ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch",
  "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read",
]);

export function parseAppManifest(value: unknown): DeclarativeAppManifest {
  if (!value || typeof value !== "object") throw new InvalidManifestError("Manifest must be an object");
  const manifest = value as Partial<DeclarativeAppManifest>;
  if (manifest.protocol !== DECLARATIVE_APP_PROTOCOL_VERSION) throw new InvalidManifestError("Unsupported manifest protocol");
  if (typeof manifest.appId !== "string" || !manifest.appId || typeof manifest.version !== "string" || !manifest.version ||
      typeof manifest.displayName !== "string" || !manifest.displayName || !Array.isArray(manifest.requestedCapabilities) ||
      !manifest.requestedCapabilities.every((request) => request && typeof request === "object" && capabilityNames.has(request.name) && typeof request.reason === "string" && request.reason.length > 0)) {
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
