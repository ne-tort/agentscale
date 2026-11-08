/** Все отказы плагинного контура: один закрытый набор кодов, один ключ UI на код. */
export const PLUGIN_ERROR_CODES = [
  "unsupportedProtocol",
  "originMismatch",
  "timeout",
  "unsafeSrc",
  "notGranted",
  "notConnected",
  "loadError",
  "identityMismatch",
  "disposed",
  "notReady",
] as const;

export type PluginErrorCode = (typeof PLUGIN_ERROR_CODES)[number];

/**
 * Текст ошибки намеренно не несёт ничего, кроме безопасного диагностического
 * маркера: сообщения плагина, URL и тексты исключений могут содержать токен,
 * а capability token никогда не должен попадать во frontend — даже в лог.
 */
function safeDetail(detail: string): string {
  return detail.replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, 120) || "plugin_error";
}

export class PluginFrameError extends Error {
  readonly code: PluginErrorCode;
  readonly detail: string;

  constructor(code: PluginErrorCode, detail: string = code) {
    const clean = safeDetail(detail);
    super(clean);
    this.name = "PluginFrameError";
    this.code = code;
    this.detail = clean;
  }
}

export function isPluginFrameError(error: unknown): error is PluginFrameError {
  return error instanceof PluginFrameError;
}

/** Никогда не перекладываем чужое сообщение в UI: оно может содержать секрет. */
export function toPluginFrameError(error: unknown, fallback: PluginErrorCode = "loadError"): PluginFrameError {
  if (isPluginFrameError(error)) return error;
  return new PluginFrameError(fallback, "integration_failure");
}

/** Код из `plugin.error` проходит через белый список, остальное — в общий отказ. */
export function pluginErrorFromCode(rawCode: unknown, detail = "plugin_declared_error"): PluginFrameError {
  const code = typeof rawCode === "string" ? rawCode : "";
  const known = (PLUGIN_ERROR_CODES as readonly string[]).includes(code)
    ? (code as PluginErrorCode)
    : "loadError";
  // Код плагина не становится текстом ошибки: только безопасный маркер.
  return new PluginFrameError(known, detail);
}

/** Ключ из `i18n/units/plugins.ts`; русский текст берётся через `useI18n()`. */
export function pluginErrorKey(code: PluginErrorCode): string {
  switch (code) {
    case "unsupportedProtocol": return "plugins.unsupportedProtocol";
    case "originMismatch": return "plugins.originMismatch";
    case "timeout": return "plugins.timeout";
    case "notGranted": return "plugins.notGranted";
    case "notConnected": return "plugins.notConnected";
    default: return "plugins.loadError";
  }
}

/** Явное исключение v4: публичная страница не требует сессии и grant. */
export function pluginSessionRequired(publicPage: boolean): boolean {
  return !publicPage;
}
