import type { APIRequestContext, APIResponse } from "@playwright/test";
import { expect } from "@playwright/test";

import { PLUGIN_ACCEPTANCE } from "./environment";

export type JsonObject = Record<string, unknown>;

export function gatewayUrl(path: string): string {
  return new URL(path, `${PLUGIN_ACCEPTANCE.gatewayOrigin}/`).toString();
}

export function privateBootstrapPath(pluginId: string, companyId: string, routePath: string): string {
  return `/api/v1/companies/${encodeURIComponent(companyId)}/plugins/${encodeURIComponent(pluginId)}/bootstrap?route_path=${encodeURIComponent(routePath)}`;
}

export function publicBootstrapPath(pluginId: string, companyId: string, routePath: string): string {
  return `/api/v1/public/plugins/${encodeURIComponent(companyId)}/${encodeURIComponent(pluginId)}/bootstrap?route_path=${encodeURIComponent(routePath)}`;
}

export function pluginpagePath(pluginId: string, companyId: string, backendPath: string): string {
  return `/pluginpage/${encodeURIComponent(pluginId)}/${encodeURIComponent(companyId)}${backendPath}`;
}

export function authHeaders(accessToken: string): Record<string, string> {
  return { Authorization: `Bearer ${accessToken}`, Accept: "application/json" };
}

export async function loginAccessToken(
  request: APIRequestContext,
  username: string,
  password: string,
): Promise<string> {
  const response = await request.post(gatewayUrl("/api/v1/auth/login"), {
    data: { username, password },
    headers: { Accept: "application/json" },
    timeout: PLUGIN_ACCEPTANCE.httpTimeoutMs,
    maxRedirects: 0,
  });
  expect(response.status(), "real login must succeed without redirect").toBe(200);
  const body: unknown = await response.json();
  if (!isRecord(body) || typeof body.access_token !== "string" || body.access_token.length === 0) {
    throw new Error("real login returned no access token; secret response material is not logged");
  }
  return body.access_token;
}

export function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function readObject(response: APIResponse, label: string): Promise<JsonObject> {
  const value: unknown = await response.json();
  if (!isRecord(value)) throw new Error(`${label} must be a JSON object`);
  return value;
}

export function expectDeniedStatus(response: APIResponse, label: string): void {
  expect(
    [401, 403],
    `${label} must be denied by Gateway; status ${response.status()} is not an allow/deny acceptance result`,
  ).toContain(response.status());
}

export async function expectGatewayDenial(
  response: APIResponse,
  label: string,
  status: 401 | 403,
  code: string,
): Promise<void> {
  expect(response.status(), `${label} must stop at Gateway`).toBe(status);
  const body = await readObject(response, label);
  expect(body.code, `${label} must have the typed pre-backend Gateway denial`).toBe(code);
}

function assertNoSecretFields(value: unknown, path = "bootstrap"): void {
  if (Array.isArray(value)) {
    for (const item of value) assertNoSecretFields(item, path);
    return;
  }
  if (!isRecord(value)) return;
  for (const [key, nested] of Object.entries(value)) {
    if (/token|secret|password|authorization|claims|dsn|receipt/i.test(key)) {
      throw new Error(`${path} leaked a forbidden credential/admin field named ${key}`);
    }
    assertNoSecretFields(nested, path);
  }
}

interface BootstrapExpectation {
  readonly pluginId: string;
  readonly pluginVersion: string;
  readonly artifactDigest: string;
  readonly companyId: string;
  readonly cabinetId?: string;
  readonly projectId?: string;
  readonly grantExpiresAt: Date;
  readonly routePath: string;
  readonly frontendEntry: string;
  readonly frontendBasePath: string;
  readonly capability: string;
  readonly publicPage?: boolean;
}

export function expectBootstrap(body: JsonObject, expected: BootstrapExpectation): void {
  assertNoSecretFields(body);
  expect(Object.keys(body).sort()).toEqual([
    "artifact_digest",
    "capabilities",
    "frontend_base_path",
    "frontend_entry",
    "grant_expires_at",
    "manifest_version",
    "plugin_id",
    "plugin_shell_origin",
    "public_pages",
    "route_path",
    "rpc_operations",
    "scope",
    "static_origin",
  ]);
  expect(body.plugin_id).toBe(expected.pluginId);
  expect(body.manifest_version).toBe(expected.pluginVersion);
  expect(body.artifact_digest).toBe(expected.artifactDigest);
  expect(body.static_origin).toBe(PLUGIN_ACCEPTANCE.staticOrigin);
  expect(body.plugin_shell_origin).toBe(PLUGIN_ACCEPTANCE.shellOrigin);
  expect(body.frontend_entry).toBe(expected.frontendEntry);
  expect(body.frontend_base_path).toBe(expected.frontendBasePath);
  expect(body.route_path).toBe(expected.routePath);
  expect(Date.parse(String(body.grant_expires_at))).toBe(expected.grantExpiresAt.getTime());

  const scope = body.scope;
  if (!isRecord(scope)) throw new Error("bootstrap.scope must be an object");
  if (expected.publicPage) {
    expect(scope).toEqual({ company_id: expected.companyId });
  } else {
    // Strict acceptance target: list/greet get all three IDs from verified grant.
    expect(scope).toEqual({
      company_id: expected.companyId,
      cabinet_id: expected.cabinetId,
      project_id: expected.projectId,
    });
  }

  expect(body.capabilities).toEqual([expected.capability]);
  const operations = body.rpc_operations;
  if (!Array.isArray(operations)) throw new Error("bootstrap.rpc_operations must be an array");
  if (expected.publicPage) {
    expect(operations).toEqual([{
      backend_path: PLUGIN_ACCEPTANCE.publicGetPath,
      capability_id: PLUGIN_ACCEPTANCE.publicCapabilityId,
      capability_version: PLUGIN_ACCEPTANCE.publicCapabilityVersion,
      http_method: "GET",
      operation: PLUGIN_ACCEPTANCE.publicOperation,
      public: true,
    }]);
    expect(body.public_pages).toEqual(expect.arrayContaining([
      PLUGIN_ACCEPTANCE.publicRoutePath,
      PLUGIN_ACCEPTANCE.publicGetPath,
    ]));
  } else {
    expect(operations).toEqual(expect.arrayContaining([
      {
        backend_path: PLUGIN_ACCEPTANCE.listBackendPath,
        capability_id: PLUGIN_ACCEPTANCE.capabilityId,
        capability_version: PLUGIN_ACCEPTANCE.capabilityVersion,
        http_method: "GET",
        operation: PLUGIN_ACCEPTANCE.listOperation,
        public: false,
      },
      {
        backend_path: PLUGIN_ACCEPTANCE.greetBackendPath,
        capability_id: PLUGIN_ACCEPTANCE.capabilityId,
        capability_version: PLUGIN_ACCEPTANCE.capabilityVersion,
        http_method: "POST",
        operation: PLUGIN_ACCEPTANCE.greetOperation,
        public: false,
      },
    ]));
  }
}

export async function assertAdminInstallation(
  request: APIRequestContext,
  adminToken: string,
  expectation: {
    readonly companyId: string;
    readonly pluginId: string;
    readonly pluginVersion: string;
    readonly artifactDigest: string;
    readonly pluginInstanceId: string;
    readonly grantId: string;
  },
): Promise<void> {
  const path = `/api/v1/admin/plugins/?company_id=${encodeURIComponent(expectation.companyId)}`;
  const response = await request.get(gatewayUrl(path), {
    headers: authHeaders(adminToken),
    timeout: PLUGIN_ACCEPTANCE.httpTimeoutMs,
    maxRedirects: 0,
  });
  expect(response.status(), "platform-admin installation preflight must return 200").toBe(200);
  const body = await readObject(response, "admin installation list");
  expect(body.company_id).toBe(expectation.companyId);
  expect(body.enabled_modules).toEqual(expect.arrayContaining([expectation.pluginId]));
  if (!Array.isArray(body.items)) throw new Error("admin installation list has no items array");
  const matches = body.items.filter((item) => isRecord(item) && item.plugin_id === expectation.pluginId);
  expect(matches, "exactly one installed plugin must match the disposable fixture").toHaveLength(1);
  const installed = matches[0];
  if (!isRecord(installed)) throw new Error("admin installation item must be an object");
  expect(installed.manifest_version).toBe(expectation.pluginVersion);
  expect(installed.artifact_digest).toBe(expectation.artifactDigest);
  expect(installed.plugin_instance_id).toBe(expectation.pluginInstanceId);
  expect(installed.grant_id).toBe(expectation.grantId);
  expect(installed.grant_status).toBe("active");
  expect(installed.enabled).toBe(true);
}

export function staticEntryUrl(entry: string, basePath: string): string {
  const prefix = basePath === "/" ? "" : basePath.replace(/\/$/, "");
  return `${PLUGIN_ACCEPTANCE.staticOrigin}${prefix}/${entry}`;
}

export function expectStaticHeaders(response: APIResponse, artifactDigest: string): void {
  expect(response.status(), "static GET/HEAD must return 200 without redirect").toBe(200);
  expect(response.headers()["x-agentscale-bundle-digest"]).toBe(artifactDigest);
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["content-security-policy"]).toBe(
    `default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors ${PLUGIN_ACCEPTANCE.shellOrigin}`,
  );
  expect(response.headers()["referrer-policy"]).toBe("no-referrer");
  expect(response.headers()["permissions-policy"]).toBe("camera=(), microphone=(), geolocation=()");
  expect(response.headers()["set-cookie"]).toBeUndefined();
}
