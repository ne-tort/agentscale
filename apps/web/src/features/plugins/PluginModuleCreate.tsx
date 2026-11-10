import { useCallback, useState, type FormEvent } from "react";

import type { ApiClient, JsonObject, ModuleSourceResponse } from "../../api/types";
import { useI18n } from "../../i18n/useT";

/** Минимальный набор полей манифеста, который автор задаёт в этой форме. */
export interface PluginModuleDraft {
  readonly pluginId: string;
  readonly version: string;
  readonly backendPort: number;
}

/**
 * Манифест собирается на КЛИЕНТЕ только из тех полей, которые автор реально
 * ввёл; остальное приходит из OCI-бандла на этапе публикации. Поэтому форма
 * создаёт источник, а не «модуль»: репозиторий и CI-привязку выпускает сервер.
 */
export function buildSourceManifest(draft: PluginModuleDraft): JsonObject {
  return {
    plugin_id: draft.pluginId,
    version: draft.version,
    protocol: "plugin-rpc/1",
    frontend: { entry: "index.html", base_path: "/" },
    backend: { port: draft.backendPort, health: "/health" },
    capabilities: [],
    migrations: { engine: "liquibase", path: "migrations/changelog.xml" },
    egress_ref: "grant:" + draft.pluginId,
    public_pages: [],
    webhooks: [],
    min_platform_version: "1.0.0",
  };
}

export interface PluginModuleCreateProps {
  readonly api: Pick<ApiClient, "createPluginSource" | "bindPluginSourceScope">;
  readonly companyId: string;
  readonly cabinetId: string;
  readonly projectId: string;
  readonly onCreated?: (source: ModuleSourceResponse) => void;
}

/**
 * Создание plugin-модуля: источник из template-репозитория + точный scope.
 * Токены и Git-credentials в браузер не попадают: оба шага server-side.
 */
export function PluginModuleCreate({
  api, companyId, cabinetId, projectId, onCreated,
}: PluginModuleCreateProps) {
  const { t } = useI18n();
  const [pluginId, setPluginId] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ModuleSourceResponse | null>(null);

  const submit = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const id = pluginId.trim();
    if (!id || !companyId || !cabinetId || !projectId) return;
    setBusy(true);
    setError(null);
    try {
      const source = await api.createPluginSource(companyId, id, buildSourceManifest({
        pluginId: id, version, backendPort: 8080,
      }));
      // Scope привязывается сразу: без него grant для модуля невозможно выдать.
      const bound = await api.bindPluginSourceScope(companyId, id, cabinetId, projectId);
      setCreated(bound);
      setPluginId("");
      onCreated?.(bound);
    } catch {
      // Текст исключения не показываем: он может содержать внутренние id.
      setError(t("plugins.createFailed"));
    } finally {
      setBusy(false);
    }
  }, [api, cabinetId, companyId, onCreated, pluginId, projectId, t, version]);

  return <section className="plugin-create" aria-busy={busy}>
    <h2>{t("plugins.createTitle")}</h2>
    <p className="plugin-create__hint">{t("plugins.createHint")}</p>
    <form onSubmit={(event) => void submit(event)}>
      <label htmlFor="plugin-create-id">{t("plugins.createId")}
        <input
          id="plugin-create-id"
          value={pluginId}
          maxLength={160}
          pattern="[a-z][a-z0-9_]*"
          onChange={(event) => setPluginId(event.target.value)}
          disabled={busy}
        />
      </label>
      <label htmlFor="plugin-create-version">{t("plugins.createVersion")}
        <input
          id="plugin-create-version"
          value={version}
          maxLength={64}
          onChange={(event) => setVersion(event.target.value)}
          disabled={busy}
        />
      </label>
      <button type="submit" disabled={busy || !pluginId.trim() || !cabinetId || !projectId}>
        {t("plugins.createSubmit")}
      </button>
    </form>
    {!cabinetId || !projectId ? <p role="status">{t("plugins.createScopeRequired")}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {created ? <p role="status">{t("plugins.createDone")}: {created.full_name} ({created.status})</p> : null}
  </section>;
}
