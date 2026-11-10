/**
 * Explicit plugin acceptance environment.
 *
 * There is deliberately no default endpoint, account, grant, digest or publication.
 * A missing value fails configuration before Chromium starts: this layer must never
 * silently fall back to dev/prod/shared state or skip its way green.
 */

const HTTPS_ORIGIN = /^https:\/\/[A-Za-z0-9.-]+(?::[0-9]+)?$/;
const SAFE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const PLUGIN_ID = /^[a-z][a-z0-9_-]{0,63}$/;
const CAPABILITY_ID = /^[a-z][a-z0-9._-]{0,127}$/;
const SEMVER = /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$/;
const CAPABILITY_VERSION = /^[0-9]+[.][0-9]+$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const INSTANCE = /^[A-Za-z0-9_-]{16,128}$/;
const OPERATION = /^[a-z][a-z0-9_-]{0,63}$/;
const BACKEND_PATH = /^\/api\/[A-Za-z0-9._/-]+$/;
const ROUTE_PATH = /^\/[A-Za-z0-9._/-]*$/;

export interface PluginAcceptanceEnvironment {
  readonly required: true;
  readonly disposableEnvironmentId: string;
  readonly webOrigin: string;
  readonly gatewayOrigin: string;
  readonly staticOrigin: string;
  readonly shellOrigin: string;
  readonly forbiddenOrigin: string;
  readonly httpTimeoutMs: number;
  readonly expiryMaxWaitMs: number;

  readonly memberUsername: string;
  readonly memberPassword: string;
  readonly adminUsername: string;
  readonly adminPassword: string;

  readonly pluginId: string;
  readonly pluginVersion: string;
  readonly artifactDigest: string;
  readonly pluginInstanceId: string;
  readonly grantId: string;
  readonly grantExpiresAt: Date;
  readonly companyId: string;
  readonly cabinetId: string;
  readonly projectId: string;
  readonly privateRoutePath: string;
  readonly frontendEntry: string;
  readonly frontendBasePath: string;
  readonly capabilityId: string;
  readonly capabilityVersion: string;
  readonly listOperation: string;
  readonly greetOperation: string;
  readonly listBackendPath: string;
  readonly greetBackendPath: string;

  readonly expiringCompanyId: string;
  readonly expiringCabinetId: string;
  readonly expiringProjectId: string;
  readonly expiringGrantId: string;
  readonly expiringGrantExpiresAt: Date;
  readonly expiringPluginInstanceId: string;

  readonly revokeCompanyId: string;
  readonly revokeCabinetId: string;
  readonly revokeProjectId: string;
  readonly revokeGrantId: string;
  readonly revokePluginInstanceId: string;
  readonly wrongCompanyId: string;

  readonly publicCompanyId: string;
  readonly publicPluginId: string;
  readonly publicPluginVersion: string;
  readonly publicArtifactDigest: string;
  readonly publicPluginInstanceId: string;
  readonly publicGrantId: string;
  readonly publicGrantExpiresAt: Date;
  readonly publicRoutePath: string;
  readonly publicFrontendEntry: string;
  readonly publicFrontendBasePath: string;
  readonly publicCapabilityId: string;
  readonly publicCapabilityVersion: string;
  readonly publicOperation: string;
  readonly publicGetPath: string;
  readonly publicPostPath: string;
  readonly publicPrivateGetPath: string;
}

function raw(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`plugin acceptance requires ${name}`);
  return value;
}

function exactHttpsOrigin(name: string): string {
  const value = raw(name);
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${name} must be an exact HTTPS origin`);
  }
  if (
    parsed.protocol !== "https:"
    || parsed.origin !== value
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || !HTTPS_ORIGIN.test(value)
  ) {
    throw new Error(`${name} must be an exact HTTPS origin without path, query, credentials or fragment`);
  }
  return value;
}

function safeId(name: string, pattern: RegExp = SAFE_ID): string {
  const value = raw(name);
  if (!pattern.test(value)) throw new Error(`${name} has an invalid identifier shape`);
  return value;
}

function digest(name: string): string {
  const value = raw(name);
  if (!DIGEST.test(value)) throw new Error(`${name} must be sha256:<64 lowercase hex>`);
  return value;
}

function backendPath(name: string): string {
  const value = raw(name);
  if (!BACKEND_PATH.test(value) || value.includes("..") || value.split("/").includes(".")) {
    throw new Error(`${name} must be a canonical /api/ backend path`);
  }
  return value;
}

function routePath(name: string): string {
  const value = raw(name);
  if (!ROUTE_PATH.test(value) || value.includes("..") || (value.length > 1 && value.endsWith("/"))) {
    throw new Error(`${name} must be a canonical route path`);
  }
  return value;
}

function frontendEntry(name: string): string {
  const value = raw(name);
  if (!/^[A-Za-z0-9._/-]+$/.test(value) || value.startsWith("/") || value.includes("..")
    || value.split("/").some((part) => part === "" || part === ".")) {
    throw new Error(`${name} must be a relative published frontend entry`);
  }
  return value;
}

function frontendBasePath(name: string): string {
  const value = raw(name);
  if (!/^\/[A-Za-z0-9._/-]*$/.test(value) || value.includes("..") || value.includes("//")
    || value.split("/").includes(".")) {
    throw new Error(`${name} must be a canonical frontend base path`);
  }
  return value;
}

function futureDate(name: string): Date {
  const value = raw(name);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
    throw new Error(`${name} must be an ISO-8601 instant in the future`);
  }
  return parsed;
}

function integer(name: string, minimum: number, maximum: number, fallback?: number): number {
  const source = process.env[name]?.trim();
  const value = source === undefined || source === "" ? fallback : Number(source);
  if (value === undefined || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer in [${minimum}, ${maximum}]`);
  }
  return value;
}

export function loadPluginAcceptanceEnvironment(): PluginAcceptanceEnvironment {
  if (raw("PLUGIN_ACCEPTANCE_REQUIRED") !== "1") {
    throw new Error("PLUGIN_ACCEPTANCE_REQUIRED must be exactly 1");
  }
  if (raw("PLUGIN_ACCEPTANCE_DISPOSABLE") !== "1") {
    throw new Error("PLUGIN_ACCEPTANCE_DISPOSABLE must be exactly 1 for an isolated disposable fixture");
  }

  const webOrigin = exactHttpsOrigin("PLUGIN_ACCEPTANCE_WEB_ORIGIN");
  const gatewayOrigin = exactHttpsOrigin("PLUGIN_ACCEPTANCE_GATEWAY_ORIGIN");
  const staticOrigin = exactHttpsOrigin("PLUGIN_ACCEPTANCE_STATIC_ORIGIN");
  const shellOrigin = exactHttpsOrigin("PLUGIN_ACCEPTANCE_SHELL_ORIGIN");
  const forbiddenOrigin = exactHttpsOrigin("PLUGIN_ACCEPTANCE_FORBIDDEN_ORIGIN");

  if (shellOrigin !== webOrigin) {
    throw new Error("PLUGIN_ACCEPTANCE_SHELL_ORIGIN must equal the served web shell origin");
  }
  if (staticOrigin === webOrigin || staticOrigin === gatewayOrigin) {
    throw new Error("PLUGIN_ACCEPTANCE_STATIC_ORIGIN must be a separate exact origin");
  }
  if (forbiddenOrigin === webOrigin || forbiddenOrigin === gatewayOrigin
    || forbiddenOrigin === staticOrigin || forbiddenOrigin === shellOrigin) {
    throw new Error("PLUGIN_ACCEPTANCE_FORBIDDEN_ORIGIN must differ from every fixture origin");
  }

  const expiryMaxWaitMs = integer("PLUGIN_ACCEPTANCE_EXPIRY_MAX_WAIT_MS", 30_000, 300_000);
  const now = Date.now();
  const grantExpiresAt = futureDate("PLUGIN_ACCEPTANCE_GRANT_EXPIRES_AT");
  const expiringGrantExpiresAt = futureDate("PLUGIN_ACCEPTANCE_EXPIRING_GRANT_EXPIRES_AT");
  const publicGrantExpiresAt = futureDate("PLUGIN_ACCEPTANCE_PUBLIC_GRANT_EXPIRES_AT");
  if (grantExpiresAt.getTime() <= now + expiryMaxWaitMs + 60_000
    || publicGrantExpiresAt.getTime() <= now + expiryMaxWaitMs + 60_000) {
    throw new Error("primary/public grants must outlive the expiry wait plus the remaining acceptance run");
  }
  if (expiringGrantExpiresAt.getTime() <= now + 15_000
    || expiringGrantExpiresAt.getTime() > now + expiryMaxWaitMs) {
    throw new Error(
      `PLUGIN_ACCEPTANCE_EXPIRING_GRANT_EXPIRES_AT must occur after startup and within ${expiryMaxWaitMs} ms`,
    );
  }

  const environment: PluginAcceptanceEnvironment = {
    required: true,
    disposableEnvironmentId: raw("PLUGIN_ACCEPTANCE_ENVIRONMENT_ID"),
    webOrigin,
    gatewayOrigin,
    staticOrigin,
    shellOrigin,
    forbiddenOrigin,
    httpTimeoutMs: integer("PLUGIN_ACCEPTANCE_HTTP_TIMEOUT_MS", 1_000, 60_000, 30_000),
    expiryMaxWaitMs,

    memberUsername: raw("PLUGIN_ACCEPTANCE_MEMBER_USERNAME"),
    memberPassword: raw("PLUGIN_ACCEPTANCE_MEMBER_PASSWORD"),
    adminUsername: raw("PLUGIN_ACCEPTANCE_ADMIN_USERNAME"),
    adminPassword: raw("PLUGIN_ACCEPTANCE_ADMIN_PASSWORD"),

    pluginId: safeId("PLUGIN_ACCEPTANCE_PLUGIN_ID", PLUGIN_ID),
    pluginVersion: safeId("PLUGIN_ACCEPTANCE_PLUGIN_VERSION", SEMVER),
    artifactDigest: digest("PLUGIN_ACCEPTANCE_ARTIFACT_DIGEST"),
    pluginInstanceId: safeId("PLUGIN_ACCEPTANCE_PLUGIN_INSTANCE_ID", INSTANCE),
    grantId: safeId("PLUGIN_ACCEPTANCE_GRANT_ID", INSTANCE),
    grantExpiresAt,
    companyId: safeId("PLUGIN_ACCEPTANCE_COMPANY_ID"),
    cabinetId: safeId("PLUGIN_ACCEPTANCE_CABINET_ID"),
    projectId: safeId("PLUGIN_ACCEPTANCE_PROJECT_ID"),
    privateRoutePath: routePath("PLUGIN_ACCEPTANCE_PRIVATE_ROUTE_PATH"),
    frontendEntry: frontendEntry("PLUGIN_ACCEPTANCE_FRONTEND_ENTRY"),
    frontendBasePath: frontendBasePath("PLUGIN_ACCEPTANCE_FRONTEND_BASE_PATH"),
    capabilityId: safeId("PLUGIN_ACCEPTANCE_CAPABILITY_ID", CAPABILITY_ID),
    capabilityVersion: safeId("PLUGIN_ACCEPTANCE_CAPABILITY_VERSION", CAPABILITY_VERSION),
    listOperation: safeId("PLUGIN_ACCEPTANCE_LIST_OPERATION", OPERATION),
    greetOperation: safeId("PLUGIN_ACCEPTANCE_GREET_OPERATION", OPERATION),
    listBackendPath: backendPath("PLUGIN_ACCEPTANCE_LIST_BACKEND_PATH"),
    greetBackendPath: backendPath("PLUGIN_ACCEPTANCE_GREET_BACKEND_PATH"),

    expiringCompanyId: safeId("PLUGIN_ACCEPTANCE_EXPIRING_COMPANY_ID"),
    expiringCabinetId: safeId("PLUGIN_ACCEPTANCE_EXPIRING_CABINET_ID"),
    expiringProjectId: safeId("PLUGIN_ACCEPTANCE_EXPIRING_PROJECT_ID"),
    expiringGrantId: safeId("PLUGIN_ACCEPTANCE_EXPIRING_GRANT_ID", INSTANCE),
    expiringGrantExpiresAt,
    expiringPluginInstanceId: safeId("PLUGIN_ACCEPTANCE_EXPIRING_PLUGIN_INSTANCE_ID", INSTANCE),

    revokeCompanyId: safeId("PLUGIN_ACCEPTANCE_REVOKE_COMPANY_ID"),
    revokeCabinetId: safeId("PLUGIN_ACCEPTANCE_REVOKE_CABINET_ID"),
    revokeProjectId: safeId("PLUGIN_ACCEPTANCE_REVOKE_PROJECT_ID"),
    revokeGrantId: safeId("PLUGIN_ACCEPTANCE_REVOKE_GRANT_ID", INSTANCE),
    revokePluginInstanceId: safeId("PLUGIN_ACCEPTANCE_REVOKE_PLUGIN_INSTANCE_ID", INSTANCE),
    wrongCompanyId: safeId("PLUGIN_ACCEPTANCE_WRONG_COMPANY_ID"),

    publicCompanyId: safeId("PLUGIN_ACCEPTANCE_PUBLIC_COMPANY_ID"),
    publicPluginId: safeId("PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_ID", PLUGIN_ID),
    publicPluginVersion: safeId("PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_VERSION", SEMVER),
    publicArtifactDigest: digest("PLUGIN_ACCEPTANCE_PUBLIC_ARTIFACT_DIGEST"),
    publicPluginInstanceId: safeId("PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_INSTANCE_ID", INSTANCE),
    publicGrantId: safeId("PLUGIN_ACCEPTANCE_PUBLIC_GRANT_ID", INSTANCE),
    publicGrantExpiresAt,
    publicRoutePath: routePath("PLUGIN_ACCEPTANCE_PUBLIC_ROUTE_PATH"),
    publicFrontendEntry: frontendEntry("PLUGIN_ACCEPTANCE_PUBLIC_FRONTEND_ENTRY"),
    publicFrontendBasePath: frontendBasePath("PLUGIN_ACCEPTANCE_PUBLIC_FRONTEND_BASE_PATH"),
    publicCapabilityId: safeId("PLUGIN_ACCEPTANCE_PUBLIC_CAPABILITY_ID", CAPABILITY_ID),
    publicCapabilityVersion: safeId("PLUGIN_ACCEPTANCE_PUBLIC_CAPABILITY_VERSION", CAPABILITY_VERSION),
    publicOperation: safeId("PLUGIN_ACCEPTANCE_PUBLIC_OPERATION", OPERATION),
    publicGetPath: backendPath("PLUGIN_ACCEPTANCE_PUBLIC_GET_PATH"),
    publicPostPath: backendPath("PLUGIN_ACCEPTANCE_PUBLIC_POST_PATH"),
    publicPrivateGetPath: backendPath("PLUGIN_ACCEPTANCE_PUBLIC_PRIVATE_GET_PATH"),
  };

  const companies = new Set([
    environment.companyId,
    environment.expiringCompanyId,
    environment.revokeCompanyId,
    environment.wrongCompanyId,
    environment.publicCompanyId,
  ]);
  if (companies.size !== 5) throw new Error("all five fixture company ids must be distinct");

  const grants = new Set([
    environment.grantId,
    environment.expiringGrantId,
    environment.revokeGrantId,
    environment.publicGrantId,
  ]);
  if (grants.size !== 4) throw new Error("all fixture grant ids must be distinct");

  const instances = new Set([
    environment.pluginInstanceId,
    environment.expiringPluginInstanceId,
    environment.revokePluginInstanceId,
    environment.publicPluginInstanceId,
  ]);
  if (instances.size !== 4) throw new Error("all durable plugin instance ids must be distinct");

  if (new Set([environment.listBackendPath, environment.greetBackendPath]).size !== 2) {
    throw new Error("private hello backend paths must be distinct");
  }
  if (environment.publicPostPath !== environment.publicGetPath) {
    throw new Error("PUBLIC_POST_PATH must equal PUBLIC_GET_PATH to prove method denial on the exact public operation");
  }
  if (environment.publicPrivateGetPath === environment.publicGetPath) {
    throw new Error("PUBLIC_PRIVATE_GET_PATH must differ from the exact public GET operation");
  }

  return Object.freeze(environment);
}

/** Config import eagerly fails before any browser or test collection. */
export const PLUGIN_ACCEPTANCE = loadPluginAcceptanceEnvironment();
