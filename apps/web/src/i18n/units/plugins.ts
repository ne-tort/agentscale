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
    createTitle: "Новый plugin-модуль",
    createHint: "Источник модуля и репозиторий создаются на сервере: токены и Git-credentials в браузер не передаются.",
    createId: "Идентификатор плагина",
    createVersion: "Версия",
    createSubmit: "Создать и привязать",
    createScopeRequired: "Выберите кабинет и проект: без точного scope установка невозможна.",
    createDone: "Модуль создан",
    createFailed: "Не удалось создать модуль. Проверьте права и источник.",
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
    createTitle: "New plugin module",
    createHint: "The module source and repository are provisioned server-side: no tokens or Git credentials reach the browser.",
    createId: "Plugin id",
    createVersion: "Version",
    createSubmit: "Create and bind",
    createScopeRequired: "Pick a cabinet and project: installation needs an exact scope.",
    createDone: "Module created",
    createFailed: "The module could not be created. Check permissions and the source.",
  },
});
