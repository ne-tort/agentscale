import { useCallback, useEffect, useRef, useState } from "react";
import { PluginFrameError, pluginSessionRequired, toPluginFrameError, type PluginErrorCode } from "./errors";
import { assertSafeSrc } from "./PluginFrame";
import {
  buildPluginSrc,
  isCanonicalPluginRoutePath,
  normalizePluginRoutePaths,
  normalizeRpcOperations,
} from "./bootstrap";
import type { RpcOperationResponse } from "../../api/types";
import { resolvePluginShellOrigin } from "./shellHandoff";
import {
  OPAQUE_ORIGIN,
  createPluginChannel,
  type PluginChannel,
  type PluginChannelFactory,
  type PluginChannelOptions,
  type PluginRequestContext,
  type PluginRequestHandler,
} from "./rpc";

/** Порт trusted registry/grant state. Версия запрашивается у реестра, не у URL. */
export interface PluginInfoPort {
  getPluginInfo(query: PluginInfoQuery): Promise<PluginInfo>;
}

export interface PluginInfoQuery {
  readonly pluginId: string;
  readonly companyId: string;
  readonly publicPage?: boolean;
  /** Канонический nested route плагина, извлечённый из shell location. */
  readonly routePath?: string;
}

export interface NotConnectedPluginInfo {
  readonly connected: false;
  readonly granted: false;
}

export interface ConnectedPluginInfo {
  readonly connected: true;
  readonly granted: boolean;
  /** Единственный источник версии для src и handshake. */
  readonly manifest_version: string;
  /** Обязательный OCI digest, не сокращённое legacy-поле digest. */
  readonly artifact_digest: string;
  /** Effective capabilities, уже отфильтрованные trusted API. */
  readonly capabilities: readonly string[];
  readonly scope?: PluginRequestContext;
  /** Опциональный backend deadline; канал дополнительно ограничивает его lease. */
  readonly grant_expires_at?: string;
  /** Signed bootstrap fields; legacy admin info does not have them. */
  readonly static_origin?: string;
  /** Exact shell origin supplied by the server bootstrap, never window.location. */
  readonly plugin_shell_origin?: string;
  readonly frontend_entry?: string;
  readonly frontend_base_path?: string;
  readonly route_path?: string;
  readonly rpc_operations?: readonly RpcOperationResponse[];
  readonly public_pages?: readonly string[];
}

export type PluginInfo = NotConnectedPluginInfo | ConnectedPluginInfo;

export interface PluginFrameConfig {
  readonly staticOrigin?: string;
  readonly allowedStaticOrigins?: readonly string[];
  readonly shellOrigin?: string;
  readonly env?: Readonly<Record<string, unknown>>;
}

export interface UsePluginFrameOptions {
  readonly pluginId: string;
  readonly companyId: string;
  readonly publicPage?: boolean;
  readonly routePath?: string;
  readonly infoPort?: PluginInfoPort;
  readonly config?: PluginFrameConfig;
  readonly timeoutMs?: number;
  readonly grantTtlMs?: number;
  readonly onRequest?: PluginRequestHandler;
  readonly onError?: (error: PluginFrameError) => void;
}

export type PluginFrameStatus = "loading" | "ready" | "error" | "notGranted" | "notConnected";
export type { PluginChannelFactory, PluginChannelOptions };

interface ResolvedPluginIdentity {
  readonly version: string;
  readonly artifactDigest: string;
  readonly capabilities: readonly string[];
  readonly scope: PluginRequestContext;
  readonly src: string;
  readonly staticOrigin: string;
  readonly shellOrigin: string;
  readonly rpcOperations: readonly RpcOperationResponse[];
  readonly grantExpiresAt?: string;
}

export interface UsePluginFrameResult {
  readonly status: PluginFrameStatus;
  readonly error: PluginFrameError | undefined;
  readonly src: string;
  readonly staticOrigin: string;
  readonly shellOrigin: string;
  readonly expectedOrigin: string;
  readonly version: string;
  readonly artifactDigest: string;
  readonly capabilities: readonly string[];
  readonly scope: PluginRequestContext;
  readonly rpcOperations: readonly RpcOperationResponse[];
  readonly grantExpiresAt?: string;
  readonly requiresSession: boolean;
  readonly retry: () => void;
  readonly channelFactory: PluginChannelFactory;
  readonly dispose: () => void;
}

const SAFE_SEGMENT = /^[A-Za-z0-9._-]{1,128}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const SEMVER = /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)(?:[.](?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:[+]([0-9a-zA-Z-]+(?:[.][0-9a-zA-Z-]+)*))?$/;
const CAPABILITY = /^[a-z][a-z0-9._-]{0,127}(?:@[0-9A-Za-z][0-9A-Za-z._+-]{0,63})?$/;

function readEnv(): Readonly<Record<string, unknown>> | undefined {
  try {
    return import.meta.env as Readonly<Record<string, unknown>> | undefined;
  } catch {
    return undefined;
  }
}

function readConfiguredOrigin(config: PluginFrameConfig): string | undefined {
  const source: Readonly<Record<string, unknown>> | undefined = config.env ?? readEnv();
  const fromEnv = source?.VITE_PLUGIN_STATIC_ORIGIN;
  const raw = config.staticOrigin ?? (typeof fromEnv === "string" ? fromEnv : undefined);
  return typeof raw === "string" && raw.trim() ? raw.trim() : undefined;
}

/** Точный отдельный HTTPS static-origin; никаких wildcard или неявных default. */
export function resolveStaticOrigin(config: PluginFrameConfig = {}): string {
  const raw = readConfiguredOrigin(config);
  if (!raw) throw new PluginFrameError("loadError", "plugin_static_origin_unavailable");
  if (raw === "*" || raw.includes("*")) throw new PluginFrameError("originMismatch", "plugin_wildcard_origin_denied");

  let parsed: URL;
  try { parsed = new URL(raw); } catch {
    throw new PluginFrameError("originMismatch", "plugin_static_origin_invalid");
  }
  if (parsed.protocol !== "https:") throw new PluginFrameError("originMismatch", "plugin_static_origin_insecure");
  if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new PluginFrameError("originMismatch", "plugin_static_origin_not_exact");
  }
  const origin = parsed.origin;
  if (config.allowedStaticOrigins && !config.allowedStaticOrigins.includes(origin)) {
    throw new PluginFrameError("originMismatch", "plugin_static_origin_not_allowlisted");
  }
  if (config.shellOrigin) {
    const shell = resolvePluginShellOrigin(config.shellOrigin);
    if (shell === origin) throw new PluginFrameError("originMismatch", "plugin_static_origin_is_shell");
  }
  return origin;
}

/** Only the private legacy admin fallback uses this storage convention. */
function buildLegacyPluginSrc(
  origin: string,
  input: { readonly companyId: string; readonly pluginId: string; readonly version: string },
): string {
  for (const value of [input.companyId, input.pluginId, input.version]) {
    if (!SAFE_SEGMENT.test(value)) throw new PluginFrameError("unsafeSrc", "plugin_path_segment_invalid");
  }
  const src = `${origin.replace(/[/]$/, "")}/tenant-${input.companyId}/${input.pluginId}/${input.version}/index.html`;
  assertSafeSrc(src);
  return src;
}

function resolveIdentity(
  info: ConnectedPluginInfo,
  pluginId: string,
  companyId: string,
  config: PluginFrameConfig | undefined,
  routePath: string | undefined,
  publicPage: boolean,
): ResolvedPluginIdentity {
  if (!SEMVER.test(info.manifest_version) || !SAFE_SEGMENT.test(info.manifest_version)
    || !DIGEST.test(info.artifact_digest)) {
    throw new PluginFrameError("identityMismatch", "plugin_registry_identity_invalid");
  }
  if (!Array.isArray(info.capabilities) || info.capabilities.length > 64
    || new Set(info.capabilities).size !== info.capabilities.length
    || info.capabilities.some((item) => typeof item !== "string" || !CAPABILITY.test(item))) {
    throw new PluginFrameError("notGranted", "plugin_capabilities_invalid");
  }
  if (info.scope?.company_id !== undefined && info.scope.company_id !== companyId) {
    throw new PluginFrameError("notGranted", "plugin_scope_company_mismatch");
  }
  if (info.grant_expires_at !== undefined && !Number.isFinite(Date.parse(info.grant_expires_at))) {
    throw new PluginFrameError("notGranted", "plugin_grant_expiry_invalid");
  }
  const requestedRoute = routePath ?? "/";
  if (!isCanonicalPluginRoutePath(requestedRoute)) {
    throw new PluginFrameError("unsafeSrc", "plugin_route_path_invalid");
  }
  const bootstrapFields = [
    info.static_origin, info.plugin_shell_origin, info.frontend_entry, info.frontend_base_path,
    info.route_path, info.rpc_operations, info.public_pages,
  ];
  const hasBootstrap = bootstrapFields.some((value) => value !== undefined);
  let staticOrigin: string;
  let shellOrigin = "";
  let src: string;
  let rpcOperations: readonly RpcOperationResponse[] = [];
  if (hasBootstrap) {
    if (typeof info.static_origin !== "string" || typeof info.plugin_shell_origin !== "string"
      || typeof info.frontend_entry !== "string"
      || typeof info.frontend_base_path !== "string" || typeof info.route_path !== "string"
      || info.route_path !== requestedRoute || !Array.isArray(info.rpc_operations)
      || !Array.isArray(info.public_pages)) {
      throw new PluginFrameError("identityMismatch", "plugin_bootstrap_binding_invalid");
    }
    const publicPages = normalizePluginRoutePaths(info.public_pages);
    if (!publicPages || (publicPage && !publicPages.includes(requestedRoute))) {
      throw new PluginFrameError("notGranted", "plugin_public_page_not_declared");
    }
    shellOrigin = resolvePluginShellOrigin(info.plugin_shell_origin);
    staticOrigin = resolveStaticOrigin({
      staticOrigin: info.static_origin,
      ...(config?.allowedStaticOrigins ? { allowedStaticOrigins: config.allowedStaticOrigins } : {}),
      shellOrigin,
    });
    src = buildPluginSrc(staticOrigin, {
      entry: info.frontend_entry,
      basePath: info.frontend_base_path,
      shellOrigin,
    });
    const normalizedOperations = normalizeRpcOperations(info.rpc_operations, info.capabilities, publicPage);
    if (!normalizedOperations) throw new PluginFrameError("notGranted", "plugin_rpc_operations_invalid");
    rpcOperations = normalizedOperations;
  } else {
    if (publicPage) throw new PluginFrameError("identityMismatch", "plugin_public_bootstrap_required");
    staticOrigin = resolveStaticOrigin(config);
    src = buildLegacyPluginSrc(staticOrigin, {
      companyId, pluginId, version: info.manifest_version,
    });
  }
  return {
    version: info.manifest_version,
    artifactDigest: info.artifact_digest,
    capabilities: [...info.capabilities],
    scope: { ...(info.scope ?? {}), company_id: companyId },
    src,
    staticOrigin,
    shellOrigin,
    rpcOperations,
    ...(info.grant_expires_at ? { grantExpiresAt: info.grant_expires_at } : {}),
  };
}

/**
 * Registry/grant → identity → src → first-load handshake. Даже publicPage требует
 * registry identity/digest; исключение касается только session/grant, не доверия
 * к версии артефакта.
 */
export function usePluginFrame(options: UsePluginFrameOptions): UsePluginFrameResult {
  const {
    pluginId, companyId, publicPage = false, routePath, infoPort, config, timeoutMs,
    grantTtlMs, onRequest, onError,
  } = options;
  const [status, setStatus] = useState<PluginFrameStatus>("loading");
  const [error, setError] = useState<PluginFrameError | undefined>();
  const [src, setSrc] = useState("");
  const [identity, setIdentity] = useState<ResolvedPluginIdentity | undefined>();
  const [attempt, setAttempt] = useState(0);
  const channelRef = useRef<PluginChannel | null>(null);
  const onErrorRef = useRef(onError);
  const onRequestRef = useRef(onRequest);
  const identityRef = useRef(identity);
  onErrorRef.current = onError;
  onRequestRef.current = onRequest;
  identityRef.current = identity;

  const configRef = useRef(config);
  configRef.current = config;
  const infoPortRef = useRef(infoPort);
  infoPortRef.current = infoPort;
  const envOrigin = config?.env?.VITE_PLUGIN_STATIC_ORIGIN;
  const configKey = [
    config?.staticOrigin ?? "",
    (config?.allowedStaticOrigins ?? []).join(","),
    config?.shellOrigin ?? "",
    typeof envOrigin === "string" ? envOrigin : "",
  ].join("|");

  const fail = useCallback((next: PluginFrameError, nextStatus: PluginFrameStatus = "error") => {
    channelRef.current?.dispose();
    channelRef.current = null;
    setSrc("");
    setIdentity(undefined);
    setError(next);
    setStatus(nextStatus);
    try { onErrorRef.current?.(next); } catch { /* External observers cannot alter failure. */ }
  }, []);

  useEffect(() => {
    let current = true;
    setStatus("loading");
    setError(undefined);
    setSrc("");
    setIdentity(undefined);

    const requiresSession = pluginSessionRequired(publicPage);
    const port = infoPortRef.current;
    if (!port) {
      fail(new PluginFrameError("loadError", "plugin_info_port_unavailable"));
      return () => { current = false; };
    }

    if (!isCanonicalPluginRoutePath(routePath ?? "/")) {
      fail(new PluginFrameError("unsafeSrc", "plugin_route_path_invalid"));
      return () => { current = false; };
    }

    void port.getPluginInfo({ pluginId, companyId, publicPage, routePath: routePath ?? "/" })
      .then((info) => {
        if (!current) return;
        if (!info || info.connected !== true) {
          fail(new PluginFrameError("notConnected", "plugin_not_connected"), "notConnected");
          return;
        }
        if (requiresSession && info.granted !== true) {
          fail(new PluginFrameError("notGranted", "plugin_not_granted"), "notGranted");
          return;
        }
        try {
          const resolved = resolveIdentity(info, pluginId, companyId, configRef.current, routePath, publicPage);
          if (!current) return;
          setIdentity(resolved);
          setSrc(resolved.src);
        } catch (cause) {
          if (current) fail(toPluginFrameError(cause));
        }
      })
      .catch((cause: unknown) => {
        if (current) fail(toPluginFrameError(cause));
      });

    return () => { current = false; };
  }, [pluginId, companyId, publicPage, routePath, configKey, attempt, fail]);

  useEffect(() => () => {
    channelRef.current?.dispose();
    channelRef.current = null;
  }, []);

  const channelFactory = useCallback<PluginChannelFactory>((frame, origin, channelOptions) => {
    channelRef.current?.dispose();
    channelRef.current = null;
    const resolved = identityRef.current;
    const channel = createPluginChannel(frame, origin, {
      ...channelOptions,
      artifactDigest: channelOptions.artifactDigest ?? resolved?.artifactDigest,
      capabilities: channelOptions.capabilities ?? resolved?.capabilities,
      scope: channelOptions.scope ?? resolved?.scope,
      grantExpiresAt: channelOptions.grantExpiresAt ?? resolved?.grantExpiresAt,
      grantTtlMs: channelOptions.grantTtlMs ?? grantTtlMs,
      onRequest: channelOptions.onRequest ?? onRequestRef.current,
      timeoutMs: timeoutMs ?? channelOptions.timeoutMs,
      onReady: (ready) => {
        setStatus("ready");
        try { channelOptions.onReady?.(ready); } catch { /* ignore */ }
      },
      onError: (cause) => {
        setSrc("");
        setError(cause);
        setStatus("error");
        try { onErrorRef.current?.(cause); } catch { /* ignore */ }
        try { channelOptions.onError?.(cause); } catch { /* ignore */ }
      },
    });
    channelRef.current = channel;
    return channel;
  }, [timeoutMs, grantTtlMs]);

  const dispose = useCallback(() => {
    channelRef.current?.dispose();
    channelRef.current = null;
    setSrc("");
  }, []);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  return {
    status,
    error,
    src,
    staticOrigin: identity?.staticOrigin ?? "",
    shellOrigin: identity?.shellOrigin ?? "",
    expectedOrigin: OPAQUE_ORIGIN,
    version: identity?.version ?? "",
    artifactDigest: identity?.artifactDigest ?? "",
    capabilities: identity?.capabilities ?? [],
    scope: identity?.scope ?? { company_id: companyId },
    rpcOperations: identity?.rpcOperations ?? [],
    ...(identity?.grantExpiresAt ? { grantExpiresAt: identity.grantExpiresAt } : {}),
    requiresSession: pluginSessionRequired(publicPage),
    retry,
    channelFactory,
    dispose,
  };
}

export function pluginFrameStatusKey(status: PluginFrameStatus, code: PluginErrorCode): string {
  if (status === "notGranted") return "plugins.notGranted";
  if (status === "notConnected") return "plugins.notConnected";
  if (status === "error") {
    switch (code) {
      case "timeout": return "plugins.timeout";
      case "originMismatch": return "plugins.originMismatch";
      case "unsupportedProtocol": return "plugins.unsupportedProtocol";
      default: return "plugins.loadError";
    }
  }
  return "plugins.loading";
}
