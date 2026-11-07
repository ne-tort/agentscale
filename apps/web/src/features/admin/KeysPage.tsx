import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../../i18n/useT";
import { useAdminService } from "./AdminArea";
import { CompanyPicker } from "./picker";
import { PickListModal } from "./PickListModal";
import { ConfirmButton, DialogForm, Field, ListToolbar, Modal, Notice, useCollection } from "./ui";

/**
 * Provider keys — one row per key, showing which companies may use it.
 *
 * Creating a key asks for the company up front, because a key nobody can use is
 * a dead row. The provider is chosen from the catalog the platform already has;
 * editing that catalog is not an administrator's job and is not on this screen.
 */
export function KeysPage() {
  const service = useAdminService();
  const { t } = useI18n();
  const keys = useCollection(() => service.listKeys(), [service]);
  const companies = useCollection(() => service.listCompanies(), [service]);
  const providers = useCollection(() => service.listProviders(), [service]);

  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [secret, setSecret] = useState("");
  const [companyIds, setCompanyIds] = useState<string[]>([]);
  const [linking, setLinking] = useState<string>();

  const companiesById = useMemo(
    () => new Map(companies.rows.map((company) => [company.id, company])),
    [companies.rows],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return keys.rows;
    return keys.rows.filter((key) => key.name.toLowerCase().includes(needle));
  }, [keys.rows, query]);

  function startCreating() {
    setName("");
    setSecret("");
    setCompanyIds([]);
    setProvider(providers.rows[0]?.id ?? "");
    setError(undefined);
    setCreating(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await service.createKey({ name: name.trim(), provider, secret, companyIds });
      await keys.reload();
      setCreating(false);
      setNotice(t("admin.keyCreated", { name: name.trim() }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  const linkingKey = keys.rows.find((key) => key.id === linking);

  return (
    <section className="admin-section">
      <div className="admin-section__header">
        <div>
          <h2>{t("admin.keysTitle")}</h2>
          <p className="admin-muted">{t("admin.keysHint")}</p>
        </div>
        <button className="admin-button admin-button--primary" onClick={startCreating} type="button">
          {t("admin.newKey")}
        </button>
      </div>

      <Notice success={notice} />

      {keys.loading ? (
        <p className="admin-muted" role="status">{t("common.loading")}</p>
      ) : keys.error ? (
        <Notice error={keys.error} />
      ) : keys.rows.length === 0 ? (
        <div className="admin-empty">
          <h3>{t("admin.keysEmpty")}</h3>
          <p className="admin-muted">{t("admin.keysEmptyHint")}</p>
          <button className="admin-button admin-button--primary" onClick={startCreating} type="button">
            {t("admin.newKey")}
          </button>
        </div>
      ) : (
        <>
          <ListToolbar count={visible.length} onQuery={setQuery} query={query} />
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">{t("admin.keyName")}</th>
                <th scope="col">{t("admin.keyProvider")}</th>
                <th scope="col">{t("admin.keyCompanies")}</th>
                <th scope="col">{t("common.status")}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {visible.map((key) => (
                <tr key={key.id}>
                  <td>{key.name}</td>
                  <td className="admin-muted">{key.provider || t("admin.keyProviderUnknown")}</td>
                  <td>
                    {key.companyIds.length === 0 ? (
                      <span className="admin-muted">{t("admin.keyCompaniesEmpty")}</span>
                    ) : (
                      <span className="admin-chips">
                        {key.companyIds.map((id) => (
                          <Link className="admin-chip" key={id} to={`/admin/companies/${encodeURIComponent(id)}`}>
                            {companiesById.get(id)?.name ?? id}
                          </Link>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="admin-muted">{statusLabel(key.status, t)}</td>
                  <td className="admin-row-actions">
                    <button className="admin-button" onClick={() => setLinking(key.id)} type="button">
                      {t("admin.keyCompanies")}
                    </button>
                    <ConfirmButton
                      confirmLabel={t("admin.keyDeleteConfirm")}
                      label={t("admin.keyDelete")}
                      onConfirm={() => {
                        void service.deleteKey(key.id).then(async () => {
                          await keys.reload();
                          setNotice(t("admin.keyDeleted"));
                        });
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {creating && (
        <Modal onClose={() => setCreating(false)} title={t("admin.newKeyTitle")}>
          <DialogForm busy={busy} onSubmit={(event) => void submit(event)} submitLabel={t("admin.keyCreate")}>
            <Field label={t("admin.keyName")}>
              <input
                autoFocus
                className="admin-input"
                maxLength={200}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("admin.keyNamePlaceholder")}
                required
                value={name}
              />
            </Field>
            <Field label={t("admin.keyProvider")}>
              <select
                className="admin-input"
                onChange={(event) => setProvider(event.target.value)}
                required
                value={provider}
              >
                <option disabled value="">
                  {t("admin.keyProviderUnknown")}
                </option>
                {providers.rows.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.title}
                  </option>
                ))}
              </select>
            </Field>
            <Field hint={t("admin.keySecretHint")} label={t("admin.keySecret")}>
              <input
                autoComplete="off"
                className="admin-input"
                onChange={(event) => setSecret(event.target.value)}
                required
                type="password"
                value={secret}
              />
            </Field>
            <div className="admin-field">
              <span className="admin-field__label">{t("admin.keyCompanies")}</span>
              <CompanyPicker companies={companies.rows} onChange={setCompanyIds} selected={companyIds} />
            </div>
            <Notice error={error} />
          </DialogForm>
        </Modal>
      )}

      {linkingKey && (
        <PickListModal
          initial={linkingKey.companyIds}
          items={companies.rows.map((company) => ({
            id: company.id,
            label: company.name,
            secondary: company.login,
          }))}
          onApply={async (next) => {
            await service.setKeyCompanies(linkingKey.id, next);
            await keys.reload();
            setLinking(undefined);
            setNotice(t("admin.keyLinked"));
          }}
          onClose={() => setLinking(undefined)}
          title={t("admin.keyLinkTitle", { name: linkingKey.name })}
        />
      )}
    </section>
  );
}

function statusLabel(status: string, t: (key: string) => string): string {
  if (status === "revoked") return t("admin.keyStatusRevoked");
  if (status === "expired") return t("admin.keyStatusExpired");
  return t("admin.keyStatusActive");
}
