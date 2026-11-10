import { useCallback, useState } from "react";

import type { ApiClient, PluginGrantResponse } from "../../api/types";
import { useI18n } from "../../i18n/useT";

type LifecycleAction = "revoke" | "uninstall" | "wake";

export interface PluginLifecycleProps {
  readonly api: Pick<ApiClient, "grantPlugin" | "revokePlugin" | "uninstallPlugin" | "wakePlugin">;
  readonly companyId: string;
  readonly pluginId: string;
  /** Версия и digest обязательны: без них grant не привязан к артефакту. */
  readonly manifestVersion: string;
  readonly artifactDigest: string;
  readonly capabilities: readonly string[];
  readonly onGranted?: (grant: PluginGrantResponse) => void;
}

/**
 * Выдача и снятие grant'а для уже созданного plugin-модуля.
 *
 * До этого экрана все четыре операции существовали только в API. Форма
 * отправляет минимум, который реально нужен серверу: (company, plugin) плюс
 * версия и digest артефакта. Digest приходит из публикации, а не вводится
 * руками, — иначе grant привязался бы к несуществующему артефакту.
 */
export function PluginLifecycle({
  api, companyId, pluginId, manifestVersion, artifactDigest, capabilities, onGranted,
}: PluginLifecycleProps) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grant, setGrant] = useState<PluginGrantResponse | null>(null);

  const run = useCallback(async (action: "grant" | LifecycleAction) => {
    if (!companyId || !pluginId) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "grant") {
        const created = await api.grantPlugin({
          company_id: companyId,
          plugin_id: pluginId,
          manifest_version: manifestVersion,
          artifact_digest: artifactDigest,
          capabilities: { capabilities: [...capabilities] },
        });
        setGrant(created);
        onGranted?.(created);
      } else if (action === "revoke") {
        await api.revokePlugin(companyId, pluginId);
        setGrant(null);
      } else if (action === "uninstall") {
        await api.uninstallPlugin(companyId, pluginId);
        setGrant(null);
      } else {
        await api.wakePlugin(companyId, pluginId);
      }
    } catch {
      // Текст исключения не показываем: он может нести внутренние id/DSN.
      setError(t("plugins.lifecycleFailed"));
    } finally {
      setBusy(false);
    }
  }, [api, artifactDigest, capabilities, companyId, manifestVersion, onGranted, pluginId, t]);

  const ready = Boolean(companyId && pluginId && manifestVersion && artifactDigest);
  const granted = grant !== null;
  return <section className="plugin-lifecycle" aria-busy={busy}>
    <h2>{t("plugins.lifecycleTitle")}</h2>
    <p className="plugin-lifecycle__hint">{t("plugins.lifecycleHint")}</p>
    <div className="plugin-lifecycle__actions">
      <button type="button" disabled={busy || !ready || granted} onClick={() => void run("grant")}>
        {t("plugins.lifecycleGrant")}
      </button>
      <button type="button" disabled={busy || !ready || !granted} onClick={() => void run("wake")}>
        {t("plugins.lifecycleWake")}
      </button>
      <button type="button" disabled={busy || !ready || !granted} onClick={() => void run("revoke")}>
        {t("plugins.lifecycleRevoke")}
      </button>
      <button type="button" disabled={busy || !ready} onClick={() => void run("uninstall")}>
        {t("plugins.lifecycleUninstall")}
      </button>
    </div>
    {!ready ? <p role="status">{t("plugins.lifecycleNotReady")}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {grant ? <dl className="plugin-lifecycle__grant">
      <div><dt>{t("plugins.lifecycleGrantId")}</dt><dd>{grant.grant_id}</dd></div>
      <div><dt>{t("plugins.lifecycleStatus")}</dt><dd>{grant.grant_status}</dd></div>
      <div><dt>{t("plugins.lifecycleDatabase")}</dt><dd>{grant.database_name}</dd></div>
      <div><dt>{t("plugins.lifecycleBucket")}</dt><dd>{grant.bucket}/{grant.prefix}</dd></div>
    </dl> : null}
  </section>;
}
