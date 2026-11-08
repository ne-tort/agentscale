/**
 * Реализация `PluginInfoPort` из `usePluginFrame.ts` (зона задачи 13).
 *
 * Выбран СУЩЕСТВУЮЩИЙ endpoint задачи 12:
 *   `GET /api/v1/admin/plugins/?company_id=...`
 * (`api/v1/plugin_admin.py::list_plugins`, роутер зарегистрирован в
 * `api/v1/router.py:94`, `APIRouter(prefix="/admin/plugins")`).
 * Альтернатива `GET /api/v1/plugins/{id}/info` в репозитории ОТСУТСТВУЕТ
 * (grep по `apps/api/src/prodavan/api/v1/**`: только `/admin/plugins/*`,
 * `/pluginpage/*`, `/webhook/*`), поэтому invent'ить новый endpoint нельзя —
 * это были бы правки чужого каталога и несуществующий контракт.
 *
 * Маппинг `PluginListResponse.items[]` → `PluginInfo` подключён/выдан:
 *   - `connected` = ровно одна запись с `plugin_id === query.pluginId`;
 *   - `granted`   = `connected` && `grant_status === "active"` && `enabled === true`
 *                   && `plugin_id ∈ enabled_modules` (фича-флаг strangler §8);
 *   - `digest`    = `artifact_digest` (в URL и UI не попадает, §5.2).
 * Версия сверяется со строки: `manifest_version === query.version` — плагин
 * другой версии считается «не подключён» (fail-closed).
 *
 * Fail-closed: любая ошибка API, некорректная форма ответа, отсутствие записи,
 * несовпадение версии/компании, пустые идентификаторы, невалидный
 * `VITE_PLUGIN_STATIC_ORIGIN` → состояние «не подключён», НЕ молчаливый `src`.
 *
 * Безопасность (v4 §5.2/§7.2/§10):
 *   - capability/access token НИКОГДА не кладётся в URL: только заголовок
 *     `Authorization: Bearer ...`, который добавляет существующий typed-клиент
 *     `createApiClient` (`apps/web/src/api/client.ts:305-325`);
 *   - `company_id` — единственный query-параметр, кодируется через
 *     `encodeURIComponent`;
 *   - `sandbox` без `allow-same-origin` обеспечивает `PluginFrame.tsx`
 *     (`PLUGIN_SANDBOX = "allow-scripts allow-forms"`) — этот файл его не меняет;
 *   - `VITE_PLUGIN_STATIC_ORIGIN` читается из `import.meta.env` и проверяется
 *     тем же строгим правилом, что `resolveStaticOrigin` (точный https-origin,
 *     без path/query/fragment/userinfo/wildcard, не совпадает с shell-origin).
 *
 * Тесты написаны, НЕ запускались (verification freeze миграции).
 */

import type { ApiClient, JsonObject } from "../../api/types";
import { PluginFrameError } from "./errors";
import { buildPluginSrc, resolveStaticOrigin, type PluginInfo, type PluginInfoPort, type PluginInfoQuery } from "./usePluginFrame";

/** Подмена typed-клиента для тестов; в проде — `useApi()` из `app/context.tsx`. */
export type PluginInfoClient = Pick<ApiClient, "me">;

/**
 * Минимальный typed-метод клиента для `GET /admin/plugins`.
 *
 * `ApiClient` (общий файл, НЕ редактируем) не содержит этого метода: он
 * появится в задаче 12/родительской интеграции. Чтобы не трогать чужой
 * `apps/web/src/api/client.ts`, объявляем локальное структурное расширение
 * (ровно как `api/dynamic.ts::DynamicModulesApiClient`). Если метод не
 * привязан — fail-closed отказ ДО любого запроса.
 */
export interface AdminPluginsApi {
  listAdminPlugins(companyId: string): Promise<unknown>;
}

export interface PluginInfoOptions {
  /** Typed-клиент оболочки (обычно `useApi()`). */
  readonly api?: PluginInfoClient;
  /** Origin оболочки; по умолчанию `window.location.origin`. */
  readonly shellOrigin?: string;
  /** Подмена env для тестов; по умолчанию `import.meta.env`. */
  readonly env?: Readonly<Record<string, unknown>>;
  /** Подмена `window` для тестов (jsdom предоставляет его сам). */
  readonly locationOrigin?: string;
  /**
   * Явно разрешить НЕ-platform-admin доступ.
   * По умолчанию `false`: endpoint задачи 12 защищён `PlatformAdminPort`,
   * поэтому обычный сотрудник получает «не подключён», а не попытку обойти
   * авторизацию. Родитель включает `true` только когда появится отдельный
   * employee-facing endpoint с проверкой `employee ∈ company` + grant (§7.2).
   */
  readonly allowNonPlatformAdmin?: boolean;
}

/** `GET /admin/plugins` для компании: typed-форма `PluginListResponse`. */
interface InstalledPluginWire {
  readonly plugin_id: string;
  readonly manifest_version: string;
  readonly artifact_digest: string;
  readonly grant_status: string;
  readonly enabled: boolean;
}

interface PluginListWire {
  readonly company_id: string;
  readonly items: readonly InstalledPluginWire[];
  readonly enabled_modules: readonly string[];
}

/** Состояние «не подключён»: единственный безопасный ответ при любом отказе. */
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

/**
 * Проверить `VITE_PLUGIN_STATIC_ORIGIN` из `import.meta.env` (fail-closed).
 *
 * Делегируем канонической `resolveStaticOrigin` из `usePluginFrame.ts`: она уже
 * запрещает wildcard, http, userinfo, path/query/fragment, сверяет с allow-list
 * и не даёт совпасть с origin оболочки. Здесь дополнительно явно передаём
 * `shellOrigin` (по умолчанию `window.location.origin`) — требование ТЗ.
 * Возвращаем точный origin, чтобы `buildPluginSrc` собрал версионированный
 * путь; при любой ошибке бросаем `PluginFrameError` (не «тихий успех»).
 */
export function assertPluginStaticOrigin(options: PluginInfoOptions = {}): string {
  const shell = options.shellOrigin ?? options.locationOrigin
    ?? (typeof window === "undefined" ? undefined : window.location.origin);
  const config: Parameters<typeof resolveStaticOrigin>[0] = {
    env: readEnv(options),
    ...(shell ? { shellOrigin: shell } : {}),
  };
  try {
    return resolveStaticOrigin(config);
  } catch (cause) {
    if (cause instanceof PluginFrameError) throw cause;
    throw new PluginFrameError("originMismatch", "plugin_static_origin_invalid");
  }
}

/** Безопасный `src` плагина: без query/fragment и без токена (v4 §5.2). */
export function pluginSrcFor(
  staticOrigin: string,
  query: PluginInfoQuery,
): string {
  return buildPluginSrc(staticOrigin, {
    companyId: query.companyId,
    pluginId: query.pluginId,
    version: query.version,
  });
}

function normalizeList(payload: unknown, companyId: string): PluginListWire | undefined {
  if (!isRecord(payload)) return undefined;
  if (payload.company_id !== companyId) return undefined;
  if (!Array.isArray(payload.items)) return undefined;
  const items: InstalledPluginWire[] = [];
  for (const raw of payload.items) {
    if (!isRecord(raw)) return undefined;
    if (
      typeof raw.plugin_id !== "string" ||
      typeof raw.manifest_version !== "string" ||
      typeof raw.artifact_digest !== "string" ||
      typeof raw.grant_status !== "string" ||
      typeof raw.enabled !== "boolean"
    ) return undefined;
    items.push({
      plugin_id: raw.plugin_id,
      manifest_version: raw.manifest_version,
      artifact_digest: raw.artifact_digest,
      grant_status: raw.grant_status,
      enabled: raw.enabled,
    });
  }
  const enabled_modules = Array.isArray(payload.enabled_modules)
    ? payload.enabled_modules.filter((item): item is string => typeof item === "string")
    : [];
  return { company_id: companyId, items, enabled_modules };
}

function toPluginInfo(
  list: PluginListWire | undefined,
  query: PluginInfoQuery,
): PluginInfo {
  if (!list) return notConnectedPluginInfo();
  const matches = list.items.filter((item) => item.plugin_id === query.pluginId);
  // Ровно одна запись: дубликат = противоречивый реестр → отказ, а не «первая».
  if (matches.length !== 1) return notConnectedPluginInfo();
  const item = matches[0];
  if (item.manifest_version !== query.version) return notConnectedPluginInfo();
  if (!/^sha256:[0-9a-f]{64}$/.test(item.artifact_digest)) return notConnectedPluginInfo();

  const featureEnabled = list.enabled_modules.includes(query.pluginId);
  const granted = item.grant_status === "active" && item.enabled && featureEnabled;
  return {
    connected: true,
    granted,
    digest: item.artifact_digest,
  };
}

/**
 * Реализация `PluginInfoPort` через существующий typed `useApi()`-клиент.
 *
 * Возвращает объект с единственным методом `getPluginInfo`. Все проверки
 * (auth-контур, static-origin, форма ответа) выполняются ДО формирования
 * состояния «подключён»; при любом сомнении — `connected: false`.
 */
export function createPluginInfoPort(options: PluginInfoOptions = {}): PluginInfoPort {
  const api = options.api;

  async function getPluginInfo(query: PluginInfoQuery): Promise<PluginInfo> {
    const fail = notConnectedPluginInfo();
    try {
      if (!api) return fail;
      if (!query.pluginId.trim() || !query.companyId.trim() || !query.version.trim()) return fail;

      // 1. Канонический Principal из `GET /me` (без разбора JWT на клиенте).
      //    Endpoint задачи 12 требует platform.admin; без явного разрешения
      //    родителя обычный пользователь получает «не подключён».
      const me = await api.me();
      const isPlatformAdmin = me.active === true
        && (me.contours?.includes("platform_admin") === true
          || me.roles?.includes("platform.admin") === true);
      if (!isPlatformAdmin && !options.allowNonPlatformAdmin) return fail;

      // 2. static-origin из `import.meta.env` (точный https, не shell-origin).
      //    `src` не возвращается портом (контракт `PluginInfo` его не содержит),
      //    но проверка обязательна: невалидная конфигурация = ошибка, а не
      //    молчаливое продолжение с потенциально небезопасным origin.
      assertPluginStaticOrigin(options);

      // 3. Состояние grant'а — СУЩЕСТВУЮЩИЙ `GET /admin/plugins/?company_id=...`.
      //    Токен уходит только в заголовке `Authorization` (client.ts),
      //    в URL — лишь закодированный `company_id`.
      const client = api as PluginInfoClient & Partial<AdminPluginsApi>;
      if (typeof client.listAdminPlugins !== "function") return fail;
      const payload: unknown = await client.listAdminPlugins(encodeURIComponent(query.companyId));
      return toPluginInfo(normalizeList(payload, query.companyId), query);
    } catch {
      // Ошибка API / сети / невалидный origin / недоступный /me → «не подключён».
      // Сообщение исключения намеренно не логируется: оно может содержать токен.
      return fail;
    }
  }

  return { getPluginInfo };
}

/**
 * Удобная фабрика для `usePluginFrame({ infoPort })`.
 *
 * Клиент обязателен: без `useApi()` порт всегда вернёт «не подключён»
 * (fail-closed), а не попытается угадать конфигурацию. Родитель вызывает
 * `createPluginInfoPort({ api: useApi() })` внутри компонента.
 */
export function createPluginInfoPortFor(api: PluginInfoClient | undefined): PluginInfoPort {
  return createPluginInfoPort({ api });
}
