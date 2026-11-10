import { useMemo, type ReactElement } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi, useAuth } from "./context";
import { ModuleAdminPage } from "../features/contours/module-admin/ModuleAdminPage";
import { createModuleAdminService } from "../features/contours/module-admin/service";

/**
 * Платформенное управление plugin-модулями.
 *
 * Сервис каталога собирается своей штатной фабрикой (она знает контракт
 * `/admin/modules`), а секция создания plugin-модуля получает отдельный
 * plugin-клиент. Точный scope приходит из query-параметров: без него установка
 * невозможна, и форма обязана отсутствовать, а не падать при отправке.
 */
export function PluginModulesAdminPage(): ReactElement {
  const api = useApi();
  const auth = useAuth();
  const [params] = useSearchParams();
  const cabinetId = params.get("cabinetId") ?? "";
  const projectId = params.get("projectId") ?? "";
  const companyId = params.get("companyId") ?? "";

  const service = useMemo(() => createModuleAdminService({
    getAccessToken: () => auth.tokens?.access_token,
  }), [auth.tokens?.access_token]);

  const pluginScope = cabinetId && projectId ? { cabinetId, projectId } : undefined;
  return <ModuleAdminPage
    mode="admin"
    service={service}
    {...(companyId ? { companyId } : {})}
    pluginApi={api}
    {...(pluginScope ? { pluginScope } : {})}
  />;
}
