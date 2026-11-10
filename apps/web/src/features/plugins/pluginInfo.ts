/**
 * PluginInfoPort: employee/public bootstrap (task01) + legacy admin fallback.
 *
 * Основной путь — GET /api/v1/companies/{company}/plugins/{plugin}/bootstrap
 * (и /public/plugins/{company}/{plugin}/bootstrap для publicPage). Backend уже
 * проверил membership и enabled-флаг, поэтому platform-admin проверка на клиенте
 * не нужна. Только private fallback может использовать старый admin-only
 * listAdminPlugins; public никогда не наследует admin authority.
 */
import type { ApiClient, JsonObject } from "../../api/types";
import { PluginFrameError } from "./errors";
import {
  buildPluginSrc,
  isCanonicalPluginRoutePath,
  normalizePluginRoutePaths,
  normalizeRpcOperations,
} from "./bootstrap";
import {
  resolveStaticOrigin,
  type ConnectedPluginInfo,
  type PluginInfo,
  type PluginInfoPort,
  type PluginInfoQuery,
} from "./usePluginFrame";

export type PluginInfoClient = Pick<ApiClient, "me">;

export interface AdminPluginsApi {
  listAdminPlugins(companyId: string): Promise<unknown>;
}

/** Employee/public bootstrap API (task01 backend bootstrap). */
export interface PluginBootstrapApi {
  getPluginBootstrap(companyId: string, pluginId: string, routePath?: string): Promise<unknown>;
  getPublicPluginBootstrap(companyId: string, pluginId: string, routePath?: string): Promise<unknown>;
}

export interface PluginInfoOptions {
  readonly api?: PluginInfoClient;
  readonly shellOrigin?: string;
  readonly env?: Readonly<Record<string, unknown>>;
  readonly allowedStaticOrigins?: readonly string[];
  /**
   * Относится ТОЛЬКО к legacy fallback listAdminPlugins: позволяет не-admin
   * читать admin endpoint при явном разрешении вызывающей стороны. Основной
   * bootstrap path этот флаг не использует — membership проверяет backend.
   */
  readonly allowNonPlatformAdmin?: boolean;
}

interface InstalledPluginWire {
  readonly plugin_id: string;
  readonly manifest_version: string;
  readonly artifact_digest: string;
  readonly grant_status: string;
  readonly enabled: boolean;
  readonly capabilities: readonly string[];
}

interface PluginListWire {
  readonly company_id: string;
  readonly items: readonly InstalledPluginWire[];
  readonly enabled_modules: readonly string[];
}

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const SEMVER = /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)(?:[.](?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:[+]([0-9a-zA-Z-]+(?:[.][0-9a-zA-Z-]+)*))?$/;
const CAPABILITY = /^[a-z][a-z0-9._-]{0,127}(?:@[0-9A-Za-z][0-9A-Za-z._+-]{0,63})?$/;

export function notConnectedPluginInfo(): PluginInfo {
  return { connected: false, granted: false };
}

function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readEnv(options: PluginInfoOptions): Readonly<Record<string, unknown>> {
  if (options.env) return options.env;
  try {
    return (import.meta.env as Readonly<Record<string, unknown>> | undefined) ?? {};
  } catch {
    return {};
  }
}

export function assertPluginStaticOrigin(options: PluginInfoOptions = {}): string {
  const shell = options.shellOrigin;
  const config: Parameters<typeof resolveStaticOrigin>[0] = {
    env: readEnv(options),
    ...(options.allowedStaticOrigins ? { allowedStaticOrigins: options.allowedStaticOrigins } : {}),
    ...(shell ? { shellOrigin: shell } : {}),
  };
  try {
    return resolveStaticOrigin(config);
  } catch (cause) {
    if (cause instanceof PluginFrameError) throw cause;
    throw new PluginFrameError("originMismatch", "plugin_static_origin_invalid");
  }
}

function normalizeList(payload: unknown, companyId: string): PluginListWire | undefined {
  if (!isRecord(payload) || payload.company_id !== companyId || !Array.isArray(payload.items)) {
    return undefined;
  }
  const items: InstalledPluginWire[] = [];
  for (const raw of payload.items) {
    if (!isRecord(raw)
      || typeof raw.plugin_id !== "string"
      || typeof raw.manifest_version !== "string"
      || typeof raw.artifact_digest !== "string"
      || typeof raw.grant_status !== "string"
      || typeof raw.enabled !== "boolean"
      || !Array.isArray(raw.capabilities)
      || raw.capabilities.some((item) => typeof item !== "string" || !CAPABILITY.test(item))) {
      return undefined;
    }
    items.push({
      plugin_id: raw.plugin_id,
      manifest_version: raw.manifest_version,
      artifact_digest: raw.artifact_digest,
      grant_status: raw.grant_status,
      enabled: raw.enabled,
      capabilities: [...raw.capabilities],
    });
  }
  if (!Array.isArray(payload.enabled_modules)
    || payload.enabled_modules.some((item) => typeof item !== "string")) {
    return undefined;
  }
  return { company_id: companyId, items, enabled_modules: [...payload.enabled_modules] };
}

function toPluginInfo(list: PluginListWire | undefined, query: PluginInfoQuery): PluginInfo {
  if (!list) return notConnectedPluginInfo();
  const matches = list.items.filter((item) => item.plugin_id === query.pluginId);
  if (matches.length !== 1) return notConnectedPluginInfo();
  const item = matches[0];
  if (!SEMVER.test(item.manifest_version) || !DIGEST.test(item.artifact_digest)) {
    return notConnectedPluginInfo();
  }
  const granted = item.grant_status === "active"
    && item.enabled
    && list.enabled_modules.includes(query.pluginId);
  const info: ConnectedPluginInfo = {
    connected: true,
    granted,
    manifest_version: item.manifest_version,
    artifact_digest: item.artifact_digest,
    capabilities: [...item.capabilities],
    scope: { company_id: query.companyId },
  };
  return info;
}

function normalizeBootstrap(
  payload: unknown,
  query: PluginInfoQuery,
  publicPage: boolean,
  options: PluginInfoOptions,
): PluginInfo {
  const fail = notConnectedPluginInfo();
  if (!isRecord(payload) || payload.plugin_id !== query.pluginId) return fail;
  const manifestVersion = payload.manifest_version;
  const digest = payload.artifact_digest;
  if (typeof manifestVersion !== "string" || !SEMVER.test(manifestVersion)) return fail;
  if (typeof digest !== "string" || !DIGEST.test(digest)) return fail;
  const rawCapabilities = payload.capabilities;
  if (!Array.isArray(rawCapabilities) || rawCapabilities.length > 64
    || new Set(rawCapabilities).size !== rawCapabilities.length
    || rawCapabilities.some((item) => typeof item !== "string" || !CAPABILITY.test(item))) {
    return fail;
  }
  const capabilities = rawCapabilities as string[];
  const requestedRoute = query.routePath ?? "/";
  if (!isCanonicalPluginRoutePath(requestedRoute) || payload.route_path !== requestedRoute) return fail;
  const publicPages = normalizePluginRoutePaths(payload.public_pages);
  if (!publicPages || (publicPage && !publicPages.includes(requestedRoute))) return fail;
  const rpcOperations = normalizeRpcOperations(payload.rpc_operations, capabilities, publicPage);
  if (!rpcOperations) return fail;
  if (typeof payload.static_origin !== "string" || typeof payload.plugin_shell_origin !== "string"
    || typeof payload.frontend_entry !== "string" || typeof payload.frontend_base_path !== "string") {
    return fail;
  }
  const shell = payload.plugin_shell_origin;
  try {
    const staticOrigin = resolveStaticOrigin({
      staticOrigin: payload.static_origin,
      ...(options.allowedStaticOrigins ? { allowedStaticOrigins: options.allowedStaticOrigins } : {}),
      shellOrigin: shell,
    });
    buildPluginSrc(staticOrigin, {
      entry: payload.frontend_entry,
      basePath: payload.frontend_base_path,
      shellOrigin: shell,
    });
  } catch {
    return fail;
  }
  const rawScope = payload.scope;
  if (!isRecord(rawScope) || rawScope.company_id !== query.companyId
    || Object.keys(rawScope).some((key) => !["company_id", "cabinet_id", "project_id", "session_id"].includes(key))) {
    return fail;
  }
  const scope: ConnectedPluginInfo["scope"] = {
    company_id: query.companyId,
    ...(typeof rawScope.cabinet_id === "string" ? { cabinet_id: rawScope.cabinet_id } : {}),
    ...(typeof rawScope.project_id === "string" ? { project_id: rawScope.project_id } : {}),
    ...(typeof rawScope.session_id === "string" ? { session_id: rawScope.session_id } : {}),
  };
  const expires = payload.grant_expires_at;
  if (expires !== undefined && expires !== null && typeof expires !== "string") return fail;
  return {
    connected: true,
    granted: !publicPage,
    manifest_version: manifestVersion,
    artifact_digest: digest,
    capabilities: [...capabilities],
    scope,
    static_origin: payload.static_origin,
    plugin_shell_origin: shell,
    frontend_entry: payload.frontend_entry,
    frontend_base_path: payload.frontend_base_path,
    route_path: requestedRoute,
    rpc_operations: rpcOperations,
    public_pages: publicPages,
    ...(typeof expires === "string" ? { grant_expires_at: expires } : {}),
  };
}

export function createPluginInfoPort(options: PluginInfoOptions = {}): PluginInfoPort {
  const api = options.api;

  async function getPluginInfo(query: PluginInfoQuery): Promise<PluginInfo> {
    const fail = notConnectedPluginInfo();
    try {
      if (!api || !query.pluginId.trim() || !query.companyId.trim()) return fail;

      const client = api as PluginInfoClient & Partial<AdminPluginsApi & PluginBootstrapApi>;
      const publicPage = query.publicPage === true;

      // Предпочитаем employee/public bootstrap: membership и enabled-флаг уже
      // проверены backend'ом, поэтому локальная platform-admin проверка не нужна.
      if (publicPage) {
        if (typeof client.getPublicPluginBootstrap !== "function") return fail;
        const payload: unknown = await client.getPublicPluginBootstrap(query.companyId, query.pluginId, query.routePath ?? "/");
        return normalizeBootstrap(payload, query, true, options);
      }
      if (typeof client.getPluginBootstrap === "function") {
        const payload: unknown = await client.getPluginBootstrap(query.companyId, query.pluginId, query.routePath ?? "/");
        return normalizeBootstrap(payload, query, false, options);
      }

      // Private-only legacy fallback. Public pages never inherit admin authority.
      assertPluginStaticOrigin(options);
      const me = await api.me();
      const isPlatformAdmin = me.active === true
        && (me.contours?.includes("platform_admin") === true
          || me.roles?.includes("platform.admin") === true);
      if (!isPlatformAdmin && !options.allowNonPlatformAdmin) return fail;
      if (typeof client.listAdminPlugins !== "function") return fail;
      const payload: unknown = await client.listAdminPlugins(query.companyId);
      return toPluginInfo(normalizeList(payload, query.companyId), query);
    } catch {
      return fail;
    }
  }

  return { getPluginInfo };
}

export function createPluginInfoPortFor(api: PluginInfoClient | undefined): PluginInfoPort {
  return createPluginInfoPort({ api });
}
