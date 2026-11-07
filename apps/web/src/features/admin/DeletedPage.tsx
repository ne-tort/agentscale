import { useMemo, useState } from "react";
import { formatDateTime } from "../../i18n/format";
import { useI18n } from "../../i18n/useT";
import { useAdminService } from "./AdminArea";
import { ConfirmButton, ListToolbar, Notice, useCollection } from "./ui";
import type { DeletedEntity } from "./service";

/**
 * The bin.
 *
 * Last tab rather than a peer: it is where things end up, not somewhere an
 * administrator works. Restoring is the common case and comes first; purging
 * stays behind a second confirmation because there is no undo behind it.
 */
export function DeletedPage() {
  const service = useAdminService();
  const { t, locale } = useI18n();
  const deleted = useCollection(() => service.listDeleted(), [service]);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState<string>();
  const [error, setError] = useState<string>();

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return deleted.rows;
    return deleted.rows.filter(
      (row) => row.name.toLowerCase().includes(needle) || row.login.toLowerCase().includes(needle),
    );
  }, [deleted.rows, query]);

  function entityLabel(entity: DeletedEntity["entity"]): string {
    if (entity === "employees") return t("admin.entityEmployee");
    if (entity === "cabinets") return t("admin.entityCabinet");
    if (entity === "projects") return t("admin.entityProject");
    return t("admin.entityCompany");
  }

  async function restore(row: DeletedEntity) {
    try {
      await service.restoreDeleted(row.entity, row.id);
      await deleted.reload();
      setError(undefined);
      setNotice(t("admin.deletedRestored"));
    } catch (caught) {
      setNotice(undefined);
      setError(caught instanceof Error ? caught.message : t("common.error"));
    }
  }

  return (
    <section className="admin-section">
      <Notice error={error} success={notice} />

      {deleted.loading ? (
        <p className="admin-muted" role="status">{t("common.loading")}</p>
      ) : deleted.error ? (
        <Notice error={deleted.error} />
      ) : deleted.rows.length === 0 ? (
        <div className="admin-empty">
          <h3>{t("admin.deletedEmpty")}</h3>
          <p className="admin-muted">{t("admin.deletedHint")}</p>
        </div>
      ) : (
        <>
          <ListToolbar count={visible.length} onQuery={setQuery} query={query} />
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">{t("admin.deletedEntity")}</th>
                <th scope="col">{t("admin.deletedName")}</th>
                <th scope="col">{t("admin.deletedLogin")}</th>
                <th scope="col">{t("admin.deletedAt")}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={`${row.entity}-${row.id}`}>
                  <td>{entityLabel(row.entity)}</td>
                  <td>{row.name}</td>
                  <td className="admin-muted">{row.login || t("common.none")}</td>
                  <td className="admin-muted">{formatDateTime(row.deletedAt, locale) || t("common.never")}</td>
                  <td className="admin-row-actions">
                    <button className="admin-button" onClick={() => void restore(row)} type="button">
                      {t("admin.deletedRestore")}
                    </button>
                    <ConfirmButton
                      confirmLabel={t("admin.deletedPurgeConfirm")}
                      label={t("admin.deletedPurge")}
                      onConfirm={async () => {
                        await service.purgeDeleted(row.entity, row.id);
                        await deleted.reload();
                        setError(undefined);
                        setNotice(t("admin.deletedPurged"));
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length === 0 && <p className="admin-muted">{t("common.noMatches")}</p>}
        </>
      )}
    </section>
  );
}
