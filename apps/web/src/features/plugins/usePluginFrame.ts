import { useCallback, useEffect, useRef, useState } from "react";
import { PluginFrameError, pluginSessionRequired, toPluginFrameError, type PluginErrorCode } from "./errors";
import { assertSafeSrc } from "./PluginFrame";
import {
  OPAQUE_ORIGIN,
  createPluginChannel,
  type PluginChannel,
  type PluginChannelFactory,
  type PluginChannelOptions,
} from "./rpc";

/**
 * Порт сведений о плагине. Реализацию предоставляет родитель (админский API /
 * реестр / gateway): этот каталог владеет только контрактом. Отсутствие порта —
 * fail-closed ошибка, а не «считаем, что всё подключено».
 */
export interface PluginInfoPort {
  getPluginInfo(query: PluginInfoQuery): Promise<PluginInfo>;
}

export interface PluginInfoQuery {
  readonly pluginId: string;
  readonly companyId: string;
  readonly version: string;
  /** Публичная страница — явное исключение (v4 §5.2): сессия не требуется. */
  readonly publicPage?: boolean;
}

export interface PluginInfo {
  /** Плагин подключён к компании (есть installation/instance). */
  readonly connected: boolean;
  /** Есть активный `CapabilityGrant` для этого сотрудника/компании. */
  readonly granted: boolean;
  /** Digest артефакта из реестра; в URL и в UI не попадает. */
  readonly digest?: string;
}

/** Конфигурация static-origin. Wildcard subdomain — цель (§5.3), v1 — один host. */
export interface PluginFrameConfig {
  /** Точный `https://host[:port]` static-origin плагинов. */
  readonly staticOrigin?: string;
  /** Список допустимых origin; `staticOrigin` должен в него входить. */
  readonly allowedStaticOrigins?: readonly string[];
  /** Origin оболочки: static-origin не должен с ним совпадать. */
  readonly shellOrigin?: string;
  /** Подмена env для тестов; по умолчанию — `import.meta.env`. */
  readonly env?: Readonly<Record<string, unknown>>;
}

export interface UsePluginFrameOptions {
  readonly pluginId: string;
  readonly companyId: string;
  readonly version: string;
  readonly publicPage?: boolean;
  readonly infoPort?: PluginInfoPort;
  readonly config?: PluginFrameConfig;
  readonly timeoutMs?: number;
  readonly onError?: (error: PluginFrameError) => void;
}

export type PluginFrameStatus =
  | "loading"
  | "ready"
  | "error"
  | "notGranted"
  | "notConnected";

export type { PluginChannelFactory, PluginChannelOptions };

export interface UsePluginFrameResult {
  readonly status: PluginFrameStatus;
  readonly error: PluginFrameError | undefined;
  /** Безопасный URL без query и fragment; пуст, пока интеграция не подтверждена. */
  readonly src: string;
  readonly expectedOrigin: string;
  /** Явное исключение: `false` только для `publicPage`. */
  readonly requiresSession: boolean;
  readonly retry: () => void;
  /** Передаётся в `<PluginFrame channelFactory=...>`; сам хук владеет cleanup. */
  readonly channelFactory: PluginChannelFactory;
  /** Принудительное закрытие канала (logout / навигация / размонтирование). */
  readonly dispose: () => void;
}

const SAFE_SEGMENT = /^[A-Za-z0-9._-]{1,128}$/;

/** Подмена env для тестов; по умолчанию — `import.meta.env` (если он есть). */
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

/**
 * Выбирает static-origin из конфигурации. Fail-closed: без явно заданного
 * точного https-origin плагин не рендерится. Wildcard, userinfo, path, query,
 * fragment и совпадение с origin оболочки запрещены.
 */
export function resolveStaticOrigin(config: PluginFrameConfig = {}): string {
  const raw = readConfiguredOrigin(config);
  if (!raw) throw new PluginFrameError("loadError", "plugin_static_origin_unavailable");
  if (raw === "*" || raw.includes("*")) throw new PluginFrameError("originMismatch", "plugin_wildcard_origin_denied");

  let parsed: URL;
  try { parsed = new URL(raw); }
  catch { throw new PluginFrameError("originMismatch", "plugin_static_origin_invalid"); }
  if (parsed.protocol !== "https:") throw new PluginFrameError("originMismatch", "plugin_static_origin_insecure");
  if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new PluginFrameError("originMismatch", "plugin_static_origin_not_exact");
  }
  const origin = parsed.origin;
  if (config.allowedStaticOrigins && !config.allowedStaticOrigins.includes(origin)) {
    throw new PluginFrameError("originMismatch", "plugin_static_origin_not_allowlisted");
  }
  if (config.shellOrigin) {
    let shell: URL;
    try { shell = new URL(config.shellOrigin); }
    catch { throw new PluginFrameError("originMismatch", "plugin_shell_origin_invalid"); }
    // §5.2: отдельный origin, НЕ `agentscale.dev` оболочки.
    if (shell.origin === origin) throw new PluginFrameError("originMismatch", "plugin_static_origin_is_shell");
  }
  return origin;
}

/** Версионированный path-based URL v1 (§5.2/§5.3): без query, fragment и токена. */
export function buildPluginSrc(
  origin: string,
  input: { readonly companyId: string; readonly pluginId: string; readonly version: string },
): string {
  for (const value of [input.companyId, input.pluginId, input.version]) {
    if (!SAFE_SEGMENT.test(value)) throw new PluginFrameError("unsafeSrc", "plugin_path_segment_invalid");
  }
  const src = `${origin.replace(/\/$/, "")}/tenant-${input.companyId}/${input.pluginId}/${input.version}/index.html`;
  assertSafeSrc(src);
  return src;
}

/**
 * Хост-хук: загружает grant/состояние через `PluginInfoPort`, выбирает
 * static-origin из конфигурации и управляет жизненным циклом канала.
 *
 * Cleanup на unmount обязателен: слушатель `message`, таймеры handshake и
 * ожидающие RPC иначе переживут размонтированный iframe.
 */
export function usePluginFrame(options: UsePluginFrameOptions): UsePluginFrameResult {
  const { pluginId, companyId, version, publicPage = false, infoPort, config, timeoutMs, onError } = options;
  const [status, setStatus] = useState<PluginFrameStatus>("loading");
  const [error, setError] = useState<PluginFrameError | undefined>();
  const [src, setSrc] = useState("");
  const [attempt, setAttempt] = useState(0);
  const channelRef = useRef<PluginChannel | null>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const fail = useCallback((next: PluginFrameError, nextStatus: PluginFrameStatus = "error") => {
    channelRef.current?.dispose();
    channelRef.current = null;
    setSrc("");
    setError(next);
    setStatus(nextStatus);
    try { onErrorRef.current?.(next); } catch { /* Внешний обработчик не влияет на отказ. */ }
  }, []);

  useEffect(() => {
    let current = true;
    setStatus("loading");
    setError(undefined);
    setSrc("");

    // Публичная страница не требует сессии и grant, но static-origin всё равно
    // берётся из доверенной конфигурации: исключение касается только доступа.
    const requiresSession = pluginSessionRequired(publicPage);
    if (requiresSession && !infoPort) {
      fail(new PluginFrameError("loadError", "plugin_info_port_unavailable"));
      return () => { current = false; };
    }

    let staticOrigin: string;
    let candidate: string;
    try {
      staticOrigin = resolveStaticOrigin(config);
      candidate = buildPluginSrc(staticOrigin, { companyId, pluginId, version });
    } catch (cause) {
      fail(toPluginFrameError(cause));
      return () => { current = false; };
    }

    if (!requiresSession) {
      if (current) setSrc(candidate);
      return () => { current = false; };
    }

    void infoPort!.getPluginInfo({ pluginId, companyId, version, publicPage })
      .then((info) => {
        if (!current) return;
        if (!info || info.connected !== true) {
          fail(new PluginFrameError("notConnected", "plugin_not_connected"), "notConnected");
          return;
        }
        if (info.granted !== true) {
          fail(new PluginFrameError("notGranted", "plugin_not_granted"), "notGranted");
          return;
        }
        setSrc(candidate);
      })
      .catch((cause: unknown) => {
        if (current) fail(toPluginFrameError(cause));
      });

    return () => { current = false; };
  }, [pluginId, companyId, version, publicPage, infoPort, config, attempt, fail]);

  // Cleanup на unmount: канал закрывается даже если iframe ещё жив в DOM-parent.
  useEffect(() => () => {
    channelRef.current?.dispose();
    channelRef.current = null;
  }, []);

  /**
   * Фабрика каналов для `<PluginFrame>`. Хук единственный владеет ссылкой на
   * активный канал, поэтому `retry`/`dispose`/unmount не оставляют слушателей.
   */
  const channelFactory = useCallback<PluginChannelFactory>((frame, origin, channelOptions) => {
    channelRef.current?.dispose();
    channelRef.current = null;
    const channel = createPluginChannel(frame, origin, {
      ...channelOptions,
      // Opaque sandbox адресуется `postMessage("*")`, но принимаем только
      // `"null"`. Когда цель §5.3 (wildcard subdomain) будет включена, родитель
      // задаст `acceptedOrigin` точным origin плагина.
      acceptedOrigin: channelOptions.acceptedOrigin ?? OPAQUE_ORIGIN,
      timeoutMs: timeoutMs ?? channelOptions.timeoutMs,
      onReady: (ready) => {
        setStatus("ready");
        try { channelOptions.onReady?.(ready); } catch { /* ignore */ }
      },
      onError: (cause) => {
        setError(cause);
        setStatus("error");
        try { channelOptions.onError?.(cause); } catch { /* ignore */ }
      },
    });
    channelRef.current = channel;
    return channel;
  }, [timeoutMs]);

  const dispose = useCallback(() => {
    channelRef.current?.dispose();
    channelRef.current = null;
  }, []);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  return {
    status,
    error,
    src,
    // Песочница без `allow-same-origin` даёт opaque origin; в целевом
    // wildcard-режиме родитель передаст точный origin через `expectedOrigin`.
    expectedOrigin: OPAQUE_ORIGIN,
    requiresSession: pluginSessionRequired(publicPage),
    retry,
    channelFactory,
    dispose,
  };
}

/** Удобный ключ ошибки для страницы, которая рендерит состояние хука сама. */
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
