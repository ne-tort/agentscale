import { defineStrings } from "../core";

/**
 * Слова оболочки вокруг iframe плагина.
 *
 * Плагин живёт на отдельном static-origin в песочнице без `allow-same-origin`,
 * поэтому все состояния загрузки, рукопожатия и отказов объясняет именно shell:
 * сам плагин в эти моменты ещё не может ничего показать. Тексты намеренно не
 * содержат токенов, адресов и технических деталей протокола — их место в логах,
 * а не на экране оператора. Публичная страница — явное исключение: сессия для
 * неё не требуется, о чём UI сообщает отдельной строкой.
 */
export default defineStrings({
  ru: {
    loading: "Загружаем плагин…",
    loadError: "Плагин не загрузился.",
    notGranted: "Доступ к плагину не выдан.",
    notConnected: "Плагин не подключён к компании.",
    retry: "Повторить",
    open: "Открыть",
    close: "Закрыть",
    unsupportedProtocol: "Плагин использует неподдерживаемую версию протокола.",
    originMismatch: "Плагин ответил с другого источника. Соединение отклонено.",
    timeout: "Плагин не ответил вовремя.",
    publicPage: "Публичная страница: вход в аккаунт не требуется.",
  },
  en: {
    loading: "Loading the plugin…",
    loadError: "The plugin did not load.",
    notGranted: "Access to this plugin has not been granted.",
    notConnected: "The plugin is not connected to the company.",
    retry: "Retry",
    open: "Open",
    close: "Close",
    unsupportedProtocol: "The plugin uses an unsupported protocol version.",
    originMismatch: "The plugin replied from another origin. Connection rejected.",
    timeout: "The plugin did not respond in time.",
    publicPage: "Public page: no account sign-in is required.",
  },
});
