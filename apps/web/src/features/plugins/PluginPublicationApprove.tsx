import { useCallback, useState, type FormEvent } from "react";

import type { ApiClient, PluginPublicationApprovalResponse } from "../../api/types";
import { useI18n } from "../../i18n/useT";

export interface PluginPublicationApproveProps {
  readonly api: Pick<ApiClient, "approvePluginPublication">;
  readonly companyId: string;
  readonly pluginId: string;
  readonly manifestVersion: string;
  readonly artifactDigest: string;
  readonly capabilities: readonly string[];
  readonly onApproved?: (approval: PluginPublicationApprovalResponse) => void;
}

/**
 * Pre-approve публикации: разрешение РОВНО на этот digest и эту политику.
 *
 * Отдельный шаг от grant'а. Approve ничего не устанавливает и не выдаёт:
 * он лишь фиксирует, что администратор заранее согласен с конкретным
 * артефактом и набором capabilities. Поэтому digest берётся из уже
 * известного артефакта, а не вводится — approve на несуществующий digest
 * либо упадёт, либо разрешит то, чего нет.
 */
export function PluginPublicationApprove({
  api, companyId, pluginId, manifestVersion, artifactDigest, capabilities, onApproved,
}: PluginPublicationApproveProps) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<PluginPublicationApprovalResponse | null>(null);

  const ready = Boolean(companyId && pluginId && manifestVersion && artifactDigest);

  const submit = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.approvePluginPublication({
        company_id: companyId,
        plugin_id: pluginId,
        manifest_version: manifestVersion,
        artifact_digest: artifactDigest,
        capabilities: { capabilities: [...capabilities] },
      });
      setApproval(result);
      onApproved?.(result);
    } catch {
      // Текст исключения не показываем: он может нести внутренние id.
      setError(t("plugins.approveFailed"));
    } finally {
      setBusy(false);
    }
  }, [api, artifactDigest, capabilities, companyId, manifestVersion, onApproved, pluginId, ready, t]);

  return <section className="plugin-approve" aria-busy={busy}>
    <h2>{t("plugins.approveTitle")}</h2>
    <p className="plugin-approve__hint">{t("plugins.approveHint")}</p>
    <form onSubmit={(event) => void submit(event)}>
      <dl className="plugin-approve__exact">
        <div><dt>{t("plugins.approveVersion")}</dt><dd>{manifestVersion || t("plugins.approveMissing")}</dd></div>
        <div><dt>{t("plugins.approveDigest")}</dt><dd>{artifactDigest || t("plugins.approveMissing")}</dd></div>
      </dl>
      <button type="submit" disabled={busy || !ready}>{t("plugins.approveSubmit")}</button>
    </form>
    {!ready ? <p role="status">{t("plugins.approveNotReady")}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {approval ? <p role="status">
      {/* Повторный approve того же digest — не новое разрешение. */}
      {approval.replayed ? t("plugins.approveReplayed") : t("plugins.approveDone")}
      {" · "}{approval.approval_id}
    </p> : null}
  </section>;
}
