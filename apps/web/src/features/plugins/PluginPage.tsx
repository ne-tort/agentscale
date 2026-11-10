import { useMemo } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useI18n } from "../../i18n/useT";
import { PluginFrame, assertSafeSrc } from "./PluginFrame";
import {
  pluginFrameStatusKey,
  usePluginFrame,
  type PluginChannelFactory,
  type PluginFrameConfig,
  type PluginFrameStatus,
  type PluginInfoPort,
} from "./usePluginFrame";
import type { PluginErrorCode } from "./errors";
import { pluginRoutePathFromLocation } from "./routePath";
import {
  createManifestOperationBroker,
  type PluginOperationTransport,
} from "./operationBroker";

/**
 * Страница плагина в основном web-приложении (A9, v4 §5.2/§5.3/§7.1).
 *
 * Границы доверия:
 * - `pluginId` / `companyId` приходят только из `useParams()`;
 * - состояние grant и `src` даёт `usePluginFrame` через `PluginInfoPort`,
 *   который реализует родитель. Страница НЕ делает прямых запросов к shared API
 *   и НЕ получает capability-токен: порт возвращает только `connected`/`granted`
 *   manifest_version/artifact_digest/capabilities, а `PluginFrame` общается с плагином по
 *   `plugin-rpc/1` без токена в URL и в сообщениях;
 * - iframe живёт на ОТДЕЛЬНОМ static-origin в песочнице без
 *   `allow-same-origin` (classic sandbox bypass, §13.1);
 * - все тексты состояний берутся из `i18n/units/plugins.ts` через `useI18n()`,
 *   заголовок/навигация — из общих юнитов каталога (hardcoded-русского нет).
 *
 * Fail-closed: при отсутствии `src`, токене в URL, небезопасном URL или
 * несовпадении origin показывается ошибка, iframe НЕ монтируется вообще.
 */

/** Версия и digest приходят только из PluginInfo/listAdminPlugins. */
export const DEFAULT_PLUGIN_BACK_PATH = "/projects";

/** Причина отказа страницы рендерить iframe. Экспортируется для тестов. */
export type PluginSrcFailure = "missing" | "token" | "unsafe" | "origin";

/** Токен в query/fragment — утечка через Referer, историю и access-логи (§5.2). */
const TOKEN_MARKER = /[?&#][^=&#]*(?:token|secret|auth|bearer|credential|claims|password|api[_-]?key)[^=&#]*=/i;
const FORBIDDEN_MARKERS = ["?token=", "access_token", "capability_token", "refresh_token", "plugin_claims"];

/**
 * Чистая проверка `src` ДО рендера iframe. `expectedOrigin` — точный
 * static-origin из доверенной конфигурации (пустая строка = конфигурации нет,
 * тогда любой непустой `src` считается отказом). `shellOrigin` явно отделяет
 * origin оболочки от origin плагина (§5.2).
 */
export function pluginSrcFailure(
  src: string,
  expectedOrigin: string,
  shellOrigin?: string,
): PluginSrcFailure | undefined {
  if (!src || !src.trim()) return "missing";
  const lower = src.toLowerCase();
  if (FORBIDDEN_MARKERS.some((marker) => lower.includes(marker)) || TOKEN_MARKER.test(lower)) return "token";
  // assertSafeSrc отвергает ЛЮБОЙ query/fragment, userinfo, encoded-разделители
  // и схемы кроме http/https; дублирует проверку внутри PluginFrame.
  try { assertSafeSrc(src); }
  catch { return "unsafe"; }
  try {
    const parsed = new URL(src);
    if (parsed.origin !== expectedOrigin) return "origin";
    if (shellOrigin && parsed.origin === shellOrigin) return "origin";
  } catch { return "unsafe"; }
  return undefined;
}

/**
 * Ключ текста ошибки: приоритет у отказа `src` (iframe не монтируется),
 * затем у состояний хука.
 */
function errorMessageKey(
  status: PluginFrameStatus,
  code: PluginErrorCode,
  failure: PluginSrcFailure | undefined,
): string {
  if (failure === "origin") return "plugins.originMismatch";
  if (failure === "token" || failure === "unsafe") return "plugins.loadError";
  if (status === "notGranted" || status === "notConnected" || status === "error") {
    return pluginFrameStatusKey(status, code);
  }
  // `missing` в фазе `loading` до блока не доходит (показывается «Загружаем»).
  return "plugins.loadError";
}

/**
 * Чистое решение «что показать». Вынесено, чтобы fail-closed проверялся без
 * моков хуков: iframe рендерится ТОЛЬКО при `phase === "frame"`.
 */
export type PluginPagePhase = "loading" | "error" | "frame";

export function resolvePluginPagePhase(input: {
  readonly status: PluginFrameStatus;
  readonly errorCode: PluginErrorCode;
  readonly failure: PluginSrcFailure | undefined;
}): { readonly phase: PluginPagePhase; readonly messageKey: string } {
  const { status, errorCode, failure } = input;
  const hookFailure = status === "error" || status === "notGranted" || status === "notConnected";
  // Токен / чужой origin / небезопасный URL — жёсткий отказ в ЛЮБОЙ фазе хука.
  // `missing` (= src ещё не выдан) НЕ считается жёстким отказом: это ожидание.
  const hardFailure = failure === "token" || failure === "unsafe" || failure === "origin";
  if (hardFailure || hookFailure || (failure !== undefined && status !== "loading")) {
    return { phase: "error", messageKey: errorMessageKey(status, errorCode, failure) };
  }
  if (failure === "missing") {
    return { phase: "loading", messageKey: "plugins.loading" };
  }
  // ВАЖНО: `usePluginFrame` оставляет status === "loading" и после выдачи src
  // (до `plugin.ready` по `plugin-rpc/1`). Валидный src — уже достаточное
  // основание смонтировать iframe; иначе фрейм не появился бы никогда.
  return { phase: "frame", messageKey: "" };
}

const PLUGIN_PAGE_STYLES = `
.plugin-page{display:grid;gap:16px;min-height:100%}
.plugin-page__nav{display:flex}
.plugin-page__back{color:var(--accent);font-weight:700}
.plugin-page__header h1{font-size:28px;line-height:1.2;margin:0;overflow-wrap:anywhere}
.plugin-page__header>.muted{margin:8px 0 0}
.plugin-page__status{color:var(--text-muted);margin:0}
.plugin-page__error{align-items:center;background:var(--surface);border-left:3px solid var(--warning-border);display:flex;gap:16px;flex-wrap:wrap;padding:18px}
.plugin-page__frame{display:block;min-height:60vh;width:100%}
`;

export interface PluginPageProps {
  /**
   * Реализация сведений об installation/grant. Родитель обязан её предоставить:
   * без порта `usePluginFrame` отказывает fail-closed, а не «считает подключённым».
   */
  readonly infoPort?: PluginInfoPort;
  /** Static allowlist/legacy fallback; shell origin comes only from server bootstrap DTO. */
  readonly config?: PluginFrameConfig;
  /** Явное исключение §5.2: публичная страница не требует сессии. */
  readonly publicPage?: boolean;
  /** Куда ведёт «Назад»; по умолчанию — раздел проектов. */
  readonly backTo?: string;
  /**
   * Подмена фабрики каналов для детерминированных тестов страницы. В проде
   * не задаётся: `usePluginFrame` сам владеет lifecycle `plugin-rpc/1`.
   */
  readonly channelFactory?: PluginChannelFactory;
  /** Trusted adapter for signed operations; plugin-selected URLs are impossible. */
  readonly operationTransport?: PluginOperationTransport;
}

export function PluginPage(props: PluginPageProps = {}) {
  const { pluginId = "", companyId = "" } = useParams();
  const location = useLocation();
  const publicPage = props.publicPage === true;
  const routePath = pluginRoutePathFromLocation(location.pathname, publicPage);
  const { t } = useI18n();
  const backTo = props.backTo ?? DEFAULT_PLUGIN_BACK_PATH;
  const frame = usePluginFrame({
    pluginId,
    companyId,
    ...(props.infoPort ? { infoPort: props.infoPort } : {}),
    config: props.config,
    ...(props.publicPage !== undefined ? { publicPage: props.publicPage } : {}),
    routePath,
  });

  const expectedOrigin = frame.staticOrigin;
  const onRequest = useMemo(() => props.operationTransport
    ? createManifestOperationBroker({
        operations: frame.rpcOperations,
        capabilities: frame.capabilities,
        publicPage,
        transport: props.operationTransport,
      })
    : undefined, [props.operationTransport, frame.rpcOperations, frame.capabilities, publicPage]);

  const failure = pluginSrcFailure(
    frame.src,
    expectedOrigin,
    frame.shellOrigin || undefined,
  );
  const errorCode = frame.error?.code ?? "loadError";
  const { phase, messageKey } = resolvePluginPagePhase({
    status: frame.status,
    errorCode,
    failure,
  });
  const blocked = phase === "error";
  const waiting = phase === "loading";
  const headingId = "plugin-page-title";
  const frameTitle = `${t("workspace.module")} ${pluginId}`;

  return <main
    className="plugin-page"
    aria-labelledby={headingId}
    data-plugin-id={pluginId}
    data-plugin-company={companyId}
    data-plugin-status={frame.status}
    data-plugin-phase={phase}
    data-plugin-blocked={blocked ? "true" : "false"}
    data-plugin-failure={failure ?? "none"}
  >
    <style>{PLUGIN_PAGE_STYLES}</style>

    <nav className="plugin-page__nav" aria-label={t("nav.menu")}>
      <Link
        className="plugin-page__back"
        to={backTo}
        aria-label={`${t("common.previous")} — ${t("nav.projects")}`}
      >{t("common.previous")}</Link>
    </nav>

    <header className="plugin-page__header">
      <p className="eyebrow">{t("workspace.module")}</p>
      <h1 id={headingId}>{pluginId}</h1>
      <p className="muted">{t("nav.company")}: {companyId}</p>
    </header>

    {blocked && <section className="plugin-page__error" role="alert" aria-atomic="true" aria-live="assertive">
      <p>{t(messageKey)}</p>
      <button type="button" onClick={frame.retry}>{t("plugins.retry")}</button>
    </section>}

    {waiting && <p className="plugin-page__status" role="status" aria-live="polite">{t("plugins.loading")}</p>}

    {!blocked && !waiting && <PluginFrame
      pluginId={pluginId}
      companyId={companyId}
      version={frame.version}
      artifactDigest={frame.artifactDigest}
      capabilities={frame.capabilities}
      scope={frame.scope}
      {...(frame.grantExpiresAt ? { grantExpiresAt: frame.grantExpiresAt } : {})}
      {...(onRequest ? { onRequest } : {})}
      src={frame.src}
      {...(frame.shellOrigin ? { shellOrigin: frame.shellOrigin } : {})}
      publicPage={publicPage}
      expectedOrigin={frame.expectedOrigin}
      channelFactory={props.channelFactory ?? frame.channelFactory}
      title={frameTitle}
      className="plugin-frame plugin-page__frame"
    />}
  </main>;
}
