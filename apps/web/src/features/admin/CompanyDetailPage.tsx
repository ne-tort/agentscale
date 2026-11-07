import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useI18n } from "../../i18n/useT";
import { useAdminService } from "./AdminArea";
import { keyEndpointLabel } from "./keyLabel";
import { PickListModal } from "./PickListModal";
import { ConfirmButton, Notice, useCollection } from "./ui";
import type { AdminCompany } from "./service";

/**
 * One company, and the three things you might do to it: give it a key, give it a
 * module, change its limits.
 *
 * Keys and modules are shown by name. The previous screen made you edit the
 * relationship from the *other* side, as a comma-separated list of identifiers,
 * which meant an administrator had to hold both halves of the relationship in
 * their head at once.
 */
export function CompanyDetailPage() {
  const { companyId = "" } = useParams();
  const service = useAdminService();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [company, setCompany] = useState<AdminCompany>();
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [linkingKeys, setLinkingKeys] = useState(false);
  const [granting, setGranting] = useState(false);

  const keys = useCollection(() => service.listKeys(), [service]);
  const modules = useCollection(() => service.listModules(), [service]);

  useEffect(() => {
    let current = true;
    setError(undefined);
    void service
      .getCompany(companyId)
      .then((next) => { if (current) setCompany(next); })
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : t("admin.companyNotFound"));
      });
    return () => { current = false; };
  }, [companyId, service, t]);

  const linkedKeys = useMemo(
    () => keys.rows.filter((key) => key.companyIds.includes(companyId)),
    [keys.rows, companyId],
  );
  const grantedModules = useMemo(
    () => modules.rows.filter((module) => module.allCompanies || module.companyIds.includes(companyId)),
    [modules.rows, companyId],
  );

  if (error) {
    return (
      <section className="admin-section">
        <Link className="admin-back" to="/admin">{t("admin.companyBack")}</Link>
        <Notice error={error} />
      </section>
    );
  }
  if (!company) {
    return <p className="admin-muted" role="status">{t("common.loading")}</p>;
  }

  async function reloadCompany() {
    setCompany(await service.getCompany(companyId));
  }

  async function deleteCompany() {
    // Throws on purpose: the confirm prompt is where a 409 ("company still has
    // cabinets") has to be read, not nowhere.
    await service.deleteCompany(companyId);
    navigate("/admin", { replace: true });
  }

  return (
    <section className="admin-section">
      <Link className="admin-back" to="/admin">← {t("admin.companyBack")}</Link>

      <header className="admin-company-header">
        <div>
          <h2>{company.name}</h2>
          <p className="admin-company-header__meta">
            <span>{t("admin.companyLogin")}: <code>{company.login}</code></span>
            {company.contactEmail && <span>{company.contactEmail}</span>}
          </p>
        </div>
        <ConfirmButton
          confirmLabel={t("admin.companyDeleteConfirm")}
          label={t("admin.companyDelete")}
          onConfirm={deleteCompany}
        />
      </header>

      <Notice success={notice} />

      <div className="admin-cards">
        <article className="admin-card">
          <header className="admin-card__header">
            <h3>{t("admin.companyKeys")}</h3>
            <button className="admin-button" onClick={() => setLinkingKeys(true)} type="button">
              {t("admin.keyLinkCompany")}
            </button>
          </header>
          {linkedKeys.length === 0 ? (
            <div className="admin-card__empty">
              <p>{t("admin.companyKeysEmpty")}</p>
              <p className="admin-muted">{t("admin.companyKeysEmptyHint")}</p>
            </div>
          ) : (
            <ul className="admin-list">
              {linkedKeys.map((key) => (
                <li key={key.id}>
                  <span className="admin-list__name">{key.name}</span>
                  <span className="admin-list__meta">{keyEndpointLabel(key, t)}</span>
                </li>
              ))}
            </ul>
          )}
        </article>

        <article className="admin-card">
          <header className="admin-card__header">
            <h3>{t("admin.companyModules")}</h3>
            <button className="admin-button" onClick={() => setGranting(true)} type="button">
              {t("admin.moduleGrant")}
            </button>
          </header>
          {grantedModules.length === 0 ? (
            <div className="admin-card__empty">
              <p>{t("admin.companyModulesEmpty")}</p>
              <p className="admin-muted">{t("admin.companyModulesEmptyHint")}</p>
            </div>
          ) : (
            <ul className="admin-list">
              {grantedModules.map((module) => (
                <li key={module.id}>
                  <span className="admin-list__name">{module.name}</span>
                  <span className="admin-list__meta">
                    {module.allCompanies ? t("admin.moduleCompaniesAll") : t("admin.moduleCompaniesSelected")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </article>
      </div>

      <QuotaCard company={company} onSaved={() => void reloadCompany()} service={service} />

      {linkingKeys && (
        <PickListModal
          initial={linkedKeys.map((key) => key.id)}
          items={keys.rows.map((key) => ({ id: key.id, label: key.name, secondary: keyEndpointLabel(key, t) }))}
          onApply={async (next) => {
            // A key's company list is the relationship, so each side that changed
            // is written where it lives: no endpoint accepts "give company X key Y".
            const before = new Set(linkedKeys.map((key) => key.id));
            const after = new Set(next);
            const writes: Promise<void>[] = [];
            for (const key of keys.rows) {
              const wasLinked = before.has(key.id);
              const shouldLink = after.has(key.id);
              if (wasLinked === shouldLink) continue;
              const companyIds = shouldLink
                ? [...key.companyIds, companyId]
                : key.companyIds.filter((id) => id !== companyId);
              writes.push(service.setKeyCompanies(key.id, companyIds));
            }
            await Promise.all(writes);
            await keys.reload();
            setLinkingKeys(false);
            setNotice(t("admin.keyLinked"));
          }}
          onClose={() => setLinkingKeys(false)}
          title={t("admin.keyLinkCompany")}
        />
      )}

      {granting && (
        <PickListModal
          initial={grantedModules.map((module) => module.id)}
          items={modules.rows.map((module) => ({
            id: module.id,
            label: module.name,
            locked: module.allCompanies,
            lockedNote: module.allCompanies ? t("admin.moduleCompaniesAll") : undefined,
          }))}
          onApply={async (next) => {
            const before = new Set(grantedModules.map((module) => module.id));
            const after = new Set(next);
            const writes: Promise<void>[] = [];
            for (const module of modules.rows) {
              const wasGranted = before.has(module.id);
              const shouldGrant = after.has(module.id);
              if (wasGranted === shouldGrant || module.allCompanies) continue;
              const companyIds = shouldGrant
                ? [...module.companyIds, companyId]
                : module.companyIds.filter((id) => id !== companyId);
              writes.push(service.setModuleCompanies(module.id, companyIds));
            }
            await Promise.all(writes);
            await modules.reload();
            setGranting(false);
            setNotice(t("admin.moduleGranted"));
          }}
          onClose={() => setGranting(false)}
          title={t("admin.moduleGrant")}
        />
      )}
    </section>
  );
}

function QuotaCard({
  company,
  onSaved,
  service,
}: {
  company: AdminCompany;
  onSaved: () => void;
  service: ReturnType<typeof useAdminService>;
}) {
  const { t } = useI18n();
  const [quota, setQuota] = useState(company.quota);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);

  useEffect(() => setQuota(company.quota), [company.quota]);

  /** An empty field is "not typed yet", not zero: `Number("")` is 0 and would
   *  save a quota nobody asked for. The minimum is the field's own floor. */
  function number(value: string, fallback: number, minimum: number): number {
    const parsed = Number(value);
    if (value.trim() === "" || !Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.trunc(parsed));
  }

  async function save() {
    setBusy(true);
    setError(undefined);
    setSaved(false);
    try {
      await service.setCompanyQuota(company.id, quota);
      onSaved();
      setSaved(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="admin-card">
      <header className="admin-card__header">
        <h3>{t("admin.companyQuota")}</h3>
      </header>
      <div className="admin-quota">
        <label className="admin-field">
          <span className="admin-field__label">{t("admin.companyQuotaCabinets")}</span>
          <input
            className="admin-input"
            min={1}
            onChange={(event) => setQuota({ ...quota, cabinets: number(event.target.value, quota.cabinets, 1) })}
            type="number"
            value={quota.cabinets}
          />
        </label>
        <label className="admin-field">
          <span className="admin-field__label">{t("admin.companyQuotaPackages")}</span>
          <input
            className="admin-input"
            min={0}
            onChange={(event) =>
              setQuota({ ...quota, packagesPerCabinet: number(event.target.value, quota.packagesPerCabinet, 0) })
            }
            type="number"
            value={quota.packagesPerCabinet}
          />
        </label>
        <label className="admin-field">
          <span className="admin-field__label">{t("admin.companyQuotaImport")}</span>
          <input
            className="admin-input"
            min={1}
            onChange={(event) => setQuota({ ...quota, importMb: number(event.target.value, quota.importMb, 1) })}
            type="number"
            value={quota.importMb}
          />
        </label>
        <button className="admin-button" disabled={busy} onClick={() => void save()} type="button">
          {busy ? t("common.saving") : t("common.save")}
        </button>
      </div>
      <Notice error={error} success={saved ? t("admin.companyQuotaSaved") : undefined} />
    </article>
  );
}
