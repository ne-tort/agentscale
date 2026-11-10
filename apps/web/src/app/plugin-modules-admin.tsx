import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi, useAuth } from "./context";
import type { AdminPluginRow } from "../api/types";
import { ModuleAdminPage } from "../features/contours/module-admin/ModuleAdminPage";
import { createModuleAdminService } from "../features/contours/module-admin/service";
import { PluginLifecycle } from "../features/plugins/PluginLifecycle";
import { PluginPublicationApprove } from "../features/plugins/PluginPublicationApprove";

/**
 * Платформенное управление plugin-модулями.
 *
 * Три части в одном экране: каталог модулей платформы (штатная фабрика),
 * создание plugin-модуля (источник + точный scope) и жизненный цикл уже
 * установленного модуля. Версия и digest для grant берутся из
 * `GET /admin/plugins`, а не вводятся руками: иначе grant привязался бы к
 * артефакту, которого нет.
 */
export function PluginModulesAdminPage(): ReactElement {
  const api = useApi();
  const auth = useAuth();
  const [params] = useSearchParams();
  const cabinetId = params.get("cabinetId") ?? "";
  const projectId = params.get("projectId") ?? "";
  const companyId = params.get("companyId") ?? "";
  const pluginId = params.get("pluginId") ?? "";
  const [installed, setInstalled] = useState<readonly AdminPluginRow[]>([]);

  const service = useMemo(() => createModuleAdminService({
    getAccessToken: () => auth.tokens?.access_token,
  }), [auth.tokens?.access_token]);

  const refreshInstalled = useCallback(async () => {
    if (!companyId) {
      setInstalled([]);
      return;
    }
    try {
      const response = await api.listAdminPlugins(companyId);
      setInstalled(response.items);
    } catch {
      // Каталог недоступен: жизненный цикл просто не рендерится, без ложного
      // «пусто» и без текста исключения на экране.
      setInstalled([]);
    }
  }, [api, companyId]);

  useEffect(() => { void refreshInstalled(); }, [refreshInstalled]);

  const current = installed.find((row) => row.plugin_id === pluginId);
  const pluginScope = cabinetId && projectId ? { cabinetId, projectId } : undefined;
  return <>
    <ModuleAdminPage
      mode="admin"
      service={service}
      {...(companyId ? { companyId } : {})}
      pluginApi={api}
      {...(pluginScope ? { pluginScope } : {})}
    />
    {/* Согласование публикации — ДО жизненного цикла: approve разрешает
        конкретный digest, grant уже опирается на разрешённый артефакт. */}
    {companyId && pluginId && current ? <PluginPublicationApprove
      api={api}
      companyId={companyId}
      pluginId={pluginId}
      manifestVersion={current.manifest_version}
      artifactDigest={current.artifact_digest}
      capabilities={Array.isArray(current.capabilities) ? (current.capabilities as string[]) : []}
    /> : null}
    {companyId && pluginId && current ? <PluginLifecycle
      api={api}
      companyId={companyId}
      pluginId={pluginId}
      manifestVersion={current.manifest_version}
      artifactDigest={current.artifact_digest}
      capabilities={Array.isArray(current.capabilities) ? (current.capabilities as string[]) : []}
      onGranted={() => void refreshInstalled()}
    /> : null}
  </>;
}
