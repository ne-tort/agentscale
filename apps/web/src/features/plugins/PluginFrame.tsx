import { useCallback, useEffect, useRef, useState } from "react";
import type { IframeHTMLAttributes } from "react";
import { useI18n } from "../../i18n/useT";
import {
  PluginFrameError,
  pluginErrorKey,
  toPluginFrameError,
  type PluginErrorCode,
} from "./errors";
import { createPluginChannel, OPAQUE_ORIGIN, PLUGIN_RPC_PROTOCOL, type PluginChannel } from "./rpc";

/**
 * `sandbox` плагина: `allow-scripts allow-forms`, БЕЗ `allow-same-origin`.
 * Сочетание `allow-scripts allow-same-origin` на том же origin позволяет
 * скрипту снять собственную песочницу (classic sandbox bypass, v4 §5.2/§13.1).
 * Константа одна: и атрибут iframe, и тест читают её отсюда.
 */
export const PLUGIN_SANDBOX = "allow-scripts allow-forms";

/** Capability token НИКОГДА не должен попадать в URL (v4 §5.2, §10, §13.1). */
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

/**
 * Проверяет `src` ДО того, как он окажется в DOM.
 *
 * Fail-closed: любой query или `#fragment` запрещены целиком. Токен в URL —
 * утечка через Referer, историю браузера и access-логи; фрагмент не отправляется
 * на сервер, но остаётся в истории и виден скриптам плагина. Отдельно
 * запрещены известные имена параметров и userinfo в URL.
 */
export function assertSafeSrc(src: string): void {
  let parsed: URL;
  try { parsed = new URL(src, window.location?.href ?? "https://shell.invalid"); }
  catch { throw new UnsafePluginSrcError("malformed"); }

  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") throw new UnsafePluginSrcError("insecure");
  if (parsed.username || parsed.password) throw new UnsafePluginSrcError("credentials");
  if (parsed.hash) throw new UnsafePluginSrcError("fragment");
  if (parsed.search) throw new UnsafePluginSrcError("query");

  // Повторная проверка по «сырому» тексту: URL-парсер нормализует часть форм,
  // но нам важно отсечь и закодированные варианты (#token=, %3Ftoken=).
  const raw = src.toLowerCase();
  if (SENSITIVE_QUERY_PATTERN.test(raw)) throw new UnsafePluginSrcError("query");
  for (const key of FORBIDDEN_QUERY_KEYS) {
    if (raw.includes(`${key}=`)) throw new UnsafePluginSrcError("query");
  }
  if (raw.includes("#")) throw new UnsafePluginSrcError("fragment");
  if (raw.includes("%23") || raw.includes("%3f") || raw.includes("%26")) throw new UnsafePluginSrcError("query");
}

/** Origin для `event.origin`. Песочница без `allow-same-origin` даёт opaque origin. */
export function expectedPluginOrigin(_src: string): string {
  return OPAQUE_ORIGIN;
}

/** `data-*` атрибуты для identity плагина; React 19 пропускает их в DOM. */
type PluginIdentityAttributes = {
  "data-plugin-id"?: string;
  "data-plugin-version"?: string;
};

export type PluginFrameProps = {
  readonly pluginId: string;
  readonly companyId: string;
  readonly version: string;
  readonly src: string;
  /** Явное исключение v4: публичная страница не требует сессии в UI-логике. */
  readonly publicPage?: boolean;
  readonly expectedOrigin?: string;
  readonly timeoutMs?: number;
  readonly onReady?: () => void;
  readonly onError?: (error: PluginFrameError) => void;
  readonly title?: string;
  readonly className?: string;
} & Pick<IframeHTMLAttributes<HTMLIFrameElement>, "height" | "width" | "loading">;

type FramePhase = "loading" | "ready" | "error" | "notConnected";

/**
 * Хост iframe плагина. Владеет каналом `plugin-rpc/1` и показывает состояние
 * загрузки/ошибки/«не подключён». Никакого capability token: ни в `src`, ни в
 * postMessage, ни в состоянии компонента.
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

  const { pluginId, companyId, version, src, publicPage = false, timeoutMs } = props;

  useEffect(() => {
    setPhase("loading");
    setErrorCode("loadError");
    channelRef.current?.dispose();
    channelRef.current = null;

    let srcError: PluginFrameError | undefined;
    try { assertSafeSrc(src); }
    catch (error) { srcError = toPluginFrameError(error, "unsafeSrc"); }
    if (!companyId) srcError = new PluginFrameError("notConnected", "plugin_company_missing");

    if (srcError) {
      setErrorCode(srcError.code);
      setPhase(srcError.code === "notConnected" ? "notConnected" : "error");
      try { onErrorRef.current?.(srcError); } catch { /* Внешний обработчик не влияет на отказ. */ }
      return undefined;
    }

    const frame = frameRef.current;
    if (!frame) return undefined;

    const expectedOrigin = props.expectedOrigin ?? expectedPluginOrigin(src);
    let channel: PluginChannel;
    try {
      channel = createPluginChannel(frame, expectedOrigin, {
        pluginId,
        version,
        timeoutMs,
        onReady: () => {
          setPhase("ready");
          try { onReadyRef.current?.(); } catch { /* ignore */ }
        },
        onError: (error) => {
          setErrorCode(error.code);
          setPhase("error");
          try { onErrorRef.current?.(error); } catch { /* ignore */ }
        },
      });
    } catch (error) {
      const frameError = toPluginFrameError(error);
      setErrorCode(frameError.code);
      setPhase("error");
      try { onErrorRef.current?.(frameError); } catch { /* ignore */ }
      return undefined;
    }
    channelRef.current = channel;
    void channel.handshake().catch((error: unknown) => {
      const frameError = toPluginFrameError(error);
      setErrorCode(frameError.code);
      setPhase("error");
      try { onErrorRef.current?.(frameError); } catch { /* ignore */ }
    });

    return () => {
      channel.dispose();
      if (channelRef.current === channel) channelRef.current = null;
    };
    // `attempt` — публичный retry; `props.expectedOrigin` участвует явно.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pluginId, companyId, version, src, timeoutMs, props.expectedOrigin, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  const identity: PluginIdentityAttributes = {
    "data-plugin-id": pluginId,
    "data-plugin-version": version,
  };

  const ariaLabel = props.title ?? t("plugins.open");
  const safe = phase !== "error" && phase !== "notConnected";

  return <section
    className={props.className ?? "plugin-frame"}
    data-plugin-phase={phase}
    data-plugin-public={publicPage ? "true" : "false"}
    aria-busy={phase === "loading"}
  >
    {publicPage && <p className="plugin-frame__notice" role="status">{t("plugins.publicPage")}</p>}

    {phase === "loading" && <p className="plugin-frame__status" role="status">{t("plugins.loading")}</p>}

    {(phase === "error" || phase === "notConnected") && <div className="plugin-frame__error" role="alert">
      <p>{phase === "notConnected" ? t("plugins.notConnected") : t(pluginErrorKey(errorCode))}</p>
      <button type="button" onClick={retry}>{t("plugins.retry")}</button>
    </div>}

    {/* unsafe src / notConnected: iframe не монтируется вообще (fail-closed). */}
    {safe && <iframe
      ref={frameRef}
      src={src}
      sandbox={PLUGIN_SANDBOX}
      referrerPolicy="no-referrer"
      {...{ credentialless: true }}
      allow=""
      title={ariaLabel}
      aria-label={ariaLabel}
      aria-busy={phase === "loading"}
      loading={props.loading}
      height={props.height}
      width={props.width}
      {...identity}
    />}

    {phase === "ready" && <button type="button" className="plugin-frame__close" onClick={() => {
      channelRef.current?.dispose();
      channelRef.current = null;
      setPhase("loading");
      setAttempt((value) => value + 1);
    }}>{t("plugins.close")}</button>}
  </section>;
}

export { PLUGIN_RPC_PROTOCOL };
