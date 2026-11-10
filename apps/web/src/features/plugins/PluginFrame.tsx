import { useCallback, useEffect, useRef, useState } from "react";
import type { IframeHTMLAttributes } from "react";
import { useI18n } from "../../i18n/useT";
import {
  PluginFrameError,
  pluginErrorKey,
  toPluginFrameError,
  type PluginErrorCode,
} from "./errors";
import { BLOCKED_FRAME_SRCDOC } from "./frameBlock";
import { buildPluginShellHandoff, resolvePluginShellOrigin } from "./shellHandoff";
import {
  createPluginChannel,
  OPAQUE_ORIGIN,
  type PluginChannel,
  type PluginChannelFactory,
  type PluginRequestContext,
  type PluginRequestHandler,
} from "./rpc";
export type { PluginChannelFactory } from "./rpc";

/**
 * `sandbox` плагина: `allow-scripts allow-forms`, БЕЗ `allow-same-origin`.
 * Сочетание `allow-scripts allow-same-origin` на том же origin позволяет
 * скрипту снять собственную песочницу (classic sandbox bypass, v4 §5.2/§13.1).
 * Константа одна: и атрибут iframe, и тест читают её отсюда.
 */
export const PLUGIN_SANDBOX = "allow-scripts allow-forms";

/** Регистр плагинов приходит из конфигурации; capability token в URL запрещён. */
const FORBIDDEN_QUERY_KEYS = [
  "token", "access_token", "refresh_token", "id_token", "auth", "authorization",
  "bearer", "api_key", "apikey", "secret", "capability_token", "plugin_claims",
  "jwt", "session", "session_id", "code",
] as const;
const SENSITIVE_QUERY_PATTERN = /(?:^|[?&#])[^=&#]*(?:token|secret|auth|bearer|credential|claims|password|api[_-]?key)[^=&#]*=/i;

export type SafeSrcFailure = "malformed" | "insecure" | "query" | "fragment" | "credentials";

export class UnsafePluginSrcError extends PluginFrameError {
  readonly reason: SafeSrcFailure;

  constructor(reason: SafeSrcFailure) {
    super("unsafeSrc", `plugin_src_${reason}`);
    this.name = "UnsafePluginSrcError";
    this.reason = reason;
  }
}

/** Токен в query — утечка через Referer, историю и access-логи (v4 §5.2/§10). */
function containsTokenQuery(raw: string): boolean {
  const lower = raw.toLowerCase();
  if (SENSITIVE_QUERY_PATTERN.test(lower)) return true;
  return FORBIDDEN_QUERY_KEYS.some((key) => lower.includes(`${key}=`));
}

/**
 * Проверяет `src` ДО того, как он окажется в DOM. Fail-closed:
 * query и `#fragment` запрещены целиком, отдельно — известные имена параметров,
 * userinfo и закодированные разделители ( `%3F` / `%23` / `%26` ).
 */
export function assertSafeSrc(src: string, base?: string): void {
  let parsed: URL;
  const baseHref = base ?? (typeof window === "undefined" ? "https://shell.invalid" : window.location.href);
  try { parsed = new URL(src, baseHref); }
  catch { throw new UnsafePluginSrcError("malformed"); }

  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") throw new UnsafePluginSrcError("insecure");
  if (parsed.username || parsed.password) throw new UnsafePluginSrcError("credentials");
  if (parsed.hash) throw new UnsafePluginSrcError("fragment");
  if (parsed.search) throw new UnsafePluginSrcError("query");
  if (containsTokenQuery(src)) throw new UnsafePluginSrcError("query");
  const raw = src.toLowerCase();
  if (raw.includes("#")) throw new UnsafePluginSrcError("fragment");
  if (raw.includes("%23") || raw.includes("%3f") || raw.includes("%26")) throw new UnsafePluginSrcError("query");
}

/** Песочница без `allow-same-origin` всегда даёт opaque `event.origin` (v4 §5.3). */
export function expectedPluginOrigin(_src: string): string {
  return OPAQUE_ORIGIN;
}

/** `data-*` атрибуты для identity плагина; React 19 пропускает их в DOM. */
type PluginIdentityAttributes = {
  "data-plugin-id"?: string;
  "data-plugin-version"?: string;
};

/**
 * `credentialless` не описан в @types/react@19; это boolean HTML-атрибут iframe.
 * Значение обязано быть `true`: React 19 считает ПУСТУЮ СТРОКУ у boolean-атрибута
 * ложью (`Received an empty string for a boolean attribute... treat as false`)
 * и вообще не выводит атрибут в DOM. Для изоляции плагина потеря атрибута
 * означает, что фрейм сможет использовать учётные данные оболочки.
 */
type CredentiallessAttribute = { credentialless?: boolean };

export interface PluginFrameProps extends Pick<IframeHTMLAttributes<HTMLIFrameElement>, "height" | "width" | "loading"> {
  readonly pluginId: string;
  readonly companyId: string;
  readonly version: string;
  /** Registry identity обязательна: без digest канал не создаётся. */
  readonly artifactDigest: string;
  readonly capabilities?: readonly string[];
  readonly scope?: PluginRequestContext;
  readonly grantExpiresAt?: string;
  readonly grantTtlMs?: number;
  readonly onRequest?: PluginRequestHandler;
  readonly src: string;
  /** Exact origin from the server bootstrap DTO; never Host/window.location/query. */
  readonly shellOrigin?: string;
  /** Явное исключение v4: публичная страница не требует сессии в UI-логике. */
  readonly publicPage?: boolean;
  readonly expectedOrigin?: string;
  readonly timeoutMs?: number;
  /** Если задана, lifecycle канала ведёт родительский `usePluginFrame`. */
  readonly channelFactory?: PluginChannelFactory;
  readonly onReady?: () => void;
  readonly onError?: (error: PluginFrameError) => void;
  readonly title?: string;
  readonly className?: string;
}

type FramePhase = "loading" | "ready" | "error" | "notConnected";

/**
 * Хост iframe плагина. Владеет каналом `plugin-rpc/1` и показывает состояние
 * загрузки/ошибки/«не подключён». Никакого capability token: ни в `src`, ни в
 * postMessage, ни в состоянии компонента.
 *
 * iframe монтируется один раз и остаётся в DOM: канал привязан к его
 * `contentWindow`. `src` задаётся императивно только после `assertSafeSrc`,
 * поэтому опасный URL никогда не становится адресом фрейма.
 */
export function PluginFrame(props: PluginFrameProps) {
  const { t } = useI18n();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const channelRef = useRef<PluginChannel | null>(null);
  const [phase, setPhase] = useState<FramePhase>("loading");
  const [errorCode, setErrorCode] = useState<PluginErrorCode>("loadError");
  const [attempt, setAttempt] = useState(0);

  const onReadyRef = useRef(props.onReady);
  const onErrorRef = useRef(props.onError);
  onReadyRef.current = props.onReady;
  onErrorRef.current = props.onError;

  const channelFactoryRef = useRef(props.channelFactory);
  channelFactoryRef.current = props.channelFactory;
  const onRequestRef = useRef(props.onRequest);
  onRequestRef.current = props.onRequest;

  const {
    pluginId, companyId, version, artifactDigest, src, shellOrigin, publicPage = false,
    timeoutMs, grantTtlMs, grantExpiresAt, expectedOrigin,
  } = props;
  const capabilities = props.capabilities ?? [];
  const scope = props.scope ?? { company_id: companyId };
  const capabilitiesKey = capabilities.join("|");
  const scopeKey = JSON.stringify(scope);

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    setErrorCode("loadError");
    channelRef.current?.dispose();
    channelRef.current = null;

    const frame = frameRef.current;
    if (!frame) return undefined;

    // Пока интеграция не подтвердила grant, канал не создаётся: пустой `src`
    // означает «данных нет» (fail-closed), а не «грузим что попало».
    if (!src) {
      frame.removeAttribute("name");
      return undefined;
    }

    let handoffName: string | undefined;
    let srcError: PluginFrameError | undefined;
    try {
      assertSafeSrc(src);
      if (shellOrigin !== undefined) {
        const canonicalShell = resolvePluginShellOrigin(shellOrigin);
        if (new URL(src).origin === canonicalShell) {
          throw new PluginFrameError("originMismatch", "plugin_static_origin_is_shell");
        }
        handoffName = buildPluginShellHandoff({
          shellOrigin: canonicalShell,
          pluginId,
          manifestVersion: version,
          artifactDigest,
        });
      }
    } catch (error) { srcError = toPluginFrameError(error, "unsafeSrc"); }
    if (srcError) {
      frame.removeAttribute("src");
      frame.removeAttribute("name");
      // A hidden frame still runs; block the document so nothing executes.
      frame.setAttribute("srcdoc", BLOCKED_FRAME_SRCDOC);
      frame.dataset.pluginBlocked = "true";
      setErrorCode(srcError.code);
      setPhase(srcError.code === "notConnected" ? "notConnected" : "error");
      try { onErrorRef.current?.(srcError); } catch { /* Внешний обработчик не влияет на отказ. */ }
      return undefined;
    }

    // A previous dispose/fatal error blocks via srcdoc. Explicit retry is the
    // only path that clears it and creates a fresh nonce/instance channel.
    frame.removeAttribute("srcdoc");
    delete frame.dataset.pluginBlocked;
    const targetOrigin = expectedOrigin ?? expectedPluginOrigin(src);
    let channel: PluginChannel;
    let failureReported = false;
    try {
      const channelOptions = {
        pluginId,
        version,
        artifactDigest,
        capabilities,
        scope,
        ...(grantExpiresAt ? { grantExpiresAt } : {}),
        ...(grantTtlMs !== undefined ? { grantTtlMs } : {}),
        timeoutMs,
        ...(onRequestRef.current ? {
          onRequest: (request: Parameters<PluginRequestHandler>[0]) => onRequestRef.current!(request),
        } : {}),
        onReady: () => {
          if (cancelled) return;
          setPhase("ready");
          try { onReadyRef.current?.(); } catch { /* ignore */ }
        },
        onError: (cause: PluginFrameError) => {
          failureReported = true;
          if (cancelled) return;
          setErrorCode(cause.code);
          setPhase("error");
          try { onErrorRef.current?.(cause); } catch { /* ignore */ }
        },
      };
      // Без фабрики компонент сам владеет каналом и своим cleanup.
      const factory = channelFactoryRef.current;
      channel = factory
        ? factory(frame, targetOrigin, channelOptions)
        : createPluginChannel(frame, targetOrigin, channelOptions);
    } catch (error) {
      const frameError = toPluginFrameError(error);
      frame.removeAttribute("src");
      // A hidden iframe still runs: hide is not stop. Neutralise it the same
      // way a disposed channel does, so a failed start cannot keep executing.
      frame.setAttribute("srcdoc", BLOCKED_FRAME_SRCDOC);
      frame.dataset.pluginBlocked = "true";
      setErrorCode(frameError.code);
      setPhase("error");
      try { onErrorRef.current?.(frameError); } catch { /* ignore */ }
      return undefined;
    }
    // Channel/load listener exists BEFORE navigation starts. The first real
    // plugin load is therefore the only event that can trigger host.ready.
    // The bounded server-derived DTO is installed before src; it is not a token,
    // a client header, signed-HTML mutation, or a replacement for RPC validation.
    if (handoffName) frame.name = handoffName;
    else frame.removeAttribute("name");
    frame.setAttribute("src", src);
    channelRef.current = channel;
    void channel.handshake().catch((error: unknown) => {
      // `dispose()` при cleanup тоже реджектит handshake — это не ошибка UI.
      if (cancelled || failureReported) return;
      const frameError = toPluginFrameError(error);
      setErrorCode(frameError.code);
      setPhase("error");
      try { onErrorRef.current?.(frameError); } catch { /* ignore */ }
    });

    return () => {
      cancelled = true;
      channel.dispose();
      if (channelRef.current === channel) channelRef.current = null;
    };
  }, [
    pluginId, companyId, version, artifactDigest, src, shellOrigin, timeoutMs, grantTtlMs,
    grantExpiresAt, expectedOrigin, capabilitiesKey, scopeKey, attempt,
  ]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  const identity: PluginIdentityAttributes = {
    "data-plugin-id": pluginId,
    "data-plugin-version": version,
  };
  // `true`, не `""`: React 19 отбрасывает boolean-атрибут с пустой строкой.
  const credentialless: CredentiallessAttribute = { credentialless: true };

  const ariaLabel = props.title ?? t("plugins.open");

  return <section
    className={props.className ?? "plugin-frame"}
    data-plugin-phase={phase}
    data-plugin-public={publicPage ? "true" : "false"}
    data-plugin-company={props.companyId}
    aria-busy={phase === "loading"}
  >
    {publicPage && <p className="plugin-frame__notice" role="status">{t("plugins.publicPage")}</p>}

    {phase === "loading" && <p className="plugin-frame__status" role="status">{t("plugins.loading")}</p>}

    {(phase === "error" || phase === "notConnected") && <div className="plugin-frame__error" role="alert">
      <p>{phase === "notConnected" ? t("plugins.notConnected") : t(pluginErrorKey(errorCode))}</p>
      <button type="button" onClick={retry}>{t("plugins.retry")}</button>
    </div>}

    <iframe
      ref={frameRef}
      data-plugin-public={publicPage ? "true" : "false"}
      sandbox={PLUGIN_SANDBOX}
      referrerPolicy="no-referrer"
      {...credentialless}
      allow=""
      title={ariaLabel}
      aria-label={ariaLabel}
      aria-busy={phase === "loading"}
      aria-hidden={phase === "error" || phase === "notConnected" ? true : undefined}
      hidden={phase === "error" || phase === "notConnected" ? true : undefined}
      loading={props.loading}
      height={props.height}
      width={props.width}
      {...identity}
    />

    {phase === "ready" && <button type="button" className="plugin-frame__close" onClick={() => {
      channelRef.current?.dispose();
      channelRef.current = null;
      setPhase("loading");
      setAttempt((value) => value + 1);
    }}>{t("plugins.close")}</button>}
  </section>;
}
