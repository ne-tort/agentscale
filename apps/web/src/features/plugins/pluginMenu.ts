/**
 * Меню установленных plugin-модулей.
 *
 * Источник — уже существующий `GET /admin/plugins`, а не новое поле манифеста:
 * меню это навигация по тому, что РЕАЛЬНО установлено, поэтому ему нужны
 * `plugin_id`, `enabled` и `grant_status`, а не декларация автора. Схема
 * манифеста v1 заморожена, и расширять её ради навигации не требуется.
 */
import type { AdminPluginRow } from "../../api/types";

export interface PluginMenuItem {
  readonly pluginId: string;
  /** Путь внутри приложения; company_id обязателен — меню всегда tenant-scoped. */
  readonly href: string;
  readonly enabled: boolean;
  readonly grantStatus: string;
}

/**
 * Пункт меню появляется только для установленного модуля с grant. Модуль без
 * grant'а открыл бы страницу, которая честно ответит `notGranted`: предлагать
 * такой переход значит вести пользователя в тупик.
 */
export function pluginMenuItems(
  rows: readonly AdminPluginRow[],
  companyId: string,
): readonly PluginMenuItem[] {
  if (!companyId) return [];
  const items: PluginMenuItem[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const pluginId = typeof row.plugin_id === "string" ? row.plugin_id : "";
    if (!pluginId || seen.has(pluginId)) continue;
    const grantStatus = typeof row.grant_status === "string" ? row.grant_status : "";
    // Без активного grant'а модуль не открывается: в меню ему не место.
    if (grantStatus !== "active") continue;
    seen.add(pluginId);
    items.push({
      pluginId,
      href: `/plugins/${encodeURIComponent(pluginId)}/${encodeURIComponent(companyId)}`,
      enabled: row.enabled === true,
      grantStatus,
    });
  }
  return items.sort((left, right) => left.pluginId.localeCompare(right.pluginId));
}
