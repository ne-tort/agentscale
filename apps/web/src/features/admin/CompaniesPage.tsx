import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../../i18n/useT";
import { useAdminService } from "./AdminArea";
import { DialogForm, Field, ListToolbar, Modal, Notice, useCollection } from "./ui";
import type { CreatedCompany } from "./service";

/**
 * The first screen an administrator opens, and the only one most days need.
 *
 * One row per company, with the three things you would otherwise go hunting for
 * — how many keys, modules and cabinets it has — right there. Opening a row is
 * where the connecting happens.
 */
export function CompaniesPage() {
  const service = useAdminService();
  const { t } = useI18n();
  const companies = useCollection(() => service.listCompanies(), [service]);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CreatedCompany>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [contactEmail, setContactEmail] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return companies.rows;
    return companies.rows.filter(
      (company) =>
        company.name.toLowerCase().includes(needle) || company.login.toLowerCase().includes(needle),
    );
  }, [companies.rows, query]);

  function startCreating() {
    setName("");
    setPassword("");
    setContactEmail("");
    setError(undefined);
    setCreated(undefined);
    setCreating(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const company = await service.createCompany({ name: name.trim(), password, contactEmail: contactEmail.trim() || undefined });
      setCreated(company);
      await companies.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-section">
      <div className="admin-section__header">
        <h2>{t("admin.companiesTitle")}</h2>
        <button className="admin-button admin-button--primary" onClick={startCreating} type="button">
          {t("admin.newCompany")}
        </button>
      </div>

      {companies.loading ? (
        <p className="admin-muted" role="status">{t("common.loading")}</p>
      ) : companies.error ? (
        <Notice error={companies.error} />
      ) : companies.rows.length === 0 ? (
        <div className="admin-empty">
          <h3>{t("admin.companiesEmpty")}</h3>
          <p className="admin-muted">{t("admin.companiesEmptyHint")}</p>
          <button className="admin-button admin-button--primary" onClick={startCreating} type="button">
            {t("admin.newCompany")}
          </button>
        </div>
      ) : (
        <>
          <ListToolbar count={visible.length} onQuery={setQuery} query={query} />
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">{t("common.name")}</th>
                <th scope="col">{t("admin.companyKeys")}</th>
                <th scope="col">{t("admin.companyModules")}</th>
                <th scope="col">{t("admin.companyCabinets")}</th>
                <th scope="col">{t("admin.companyEmployees")}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {visible.map((company) => (
                <tr key={company.id}>
                  <td>
                    <Link className="admin-entity" to={`/admin/companies/${encodeURIComponent(company.id)}`}>
                      <span className="admin-entity__name">{company.name}</span>
                      <span className="admin-entity__meta">{company.login}</span>
                    </Link>
                  </td>
                  <td>{company.keyIds.length}</td>
                  <td>{company.moduleIds.length}</td>
                  <td>{company.cabinets}</td>
                  <td>{company.employees}</td>
                  <td className="admin-row-actions">
                    <Link className="admin-button" to={`/admin/companies/${encodeURIComponent(company.id)}`}>
                      {t("admin.companyOpen")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length === 0 && <p className="admin-muted">{t("common.noMatches")}</p>}
        </>
      )}

      {creating && (
        <Modal
          detail={t("admin.companyLoginHint")}
          onClose={() => setCreating(false)}
          title={created ? t("admin.companyCreated", { name: created.name }) : t("admin.newCompanyTitle")}
        >
          {created ? (
            <div className="admin-created">
              <p className="admin-muted">{t("admin.companyCreatedHint")}</p>
              <CredentialRow label={t("admin.companyLogin")} value={created.login} />
              <CredentialRow label={t("admin.companyPassword")} value={created.password} />
              <button className="admin-button admin-button--primary" onClick={() => setCreating(false)} type="button">
                {t("common.close")}
              </button>
            </div>
          ) : (
            <DialogForm busy={busy} onSubmit={(event) => void submit(event)} submitLabel={t("admin.companyCreate")}>
              <Field label={t("admin.companyName")}>
                <input
                  autoFocus
                  className="admin-input"
                  maxLength={200}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t("admin.companyNamePlaceholder")}
                  required
                  value={name}
                />
              </Field>
              <Field hint={t("admin.companyPasswordHint")} label={t("admin.companyPassword")}>
                <input
                  autoComplete="new-password"
                  className="admin-input"
                  minLength={8}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </Field>
              <Field label={t("admin.companyContact")}>
                <input
                  className="admin-input"
                  onChange={(event) => setContactEmail(event.target.value)}
                  placeholder={t("admin.companyContactOptional")}
                  type="email"
                  value={contactEmail}
                />
              </Field>
              <Notice error={error} />
            </DialogForm>
          )}
        </Modal>
      )}
    </section>
  );
}

/** A value the administrator has to hand over, with a copy button beside it. */
function CredentialRow({ label, value }: { label: string; value: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <div className="admin-credential">
      <span className="admin-credential__label">{label}</span>
      <code className="admin-credential__value">{value}</code>
      <button
        className="admin-button"
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          });
        }}
        type="button"
      >
        {copied ? t("common.copied") : t("common.copy")}
      </button>
    </div>
  );
}
