import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../../i18n/useT";
import { useAdminService } from "./AdminArea";
import { PickListModal } from "./PickListModal";
import { ConfirmButton, DialogForm, Field, ListToolbar, Modal, Notice, useCollection } from "./ui";

/**
 * Platform modules — what a company gets access to.
 *
 * The editor for a module's internals lives with the company that uses it, not
 * here: an administrator grants a module, they do not build one.
 */
export function ModulesPage() {
  const service = useAdminService();
  const { t, plural } = useI18n();
  const modules = useCollection(() => service.listModules(), [service]);
  const companies = useCollection(() => service.listCompanies(), [service]);

  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [name, setName] = useState("");
  const [granting, setGranting] = useState<string>();
  const [switching, setSwitching] = useState<string>();

  const companiesById = useMemo(
    () => new Map(companies.rows.map((company) => [company.id, company])),
    [companies.rows],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return modules.rows;
    return modules.rows.filter((module) => module.name.toLowerCase().includes(needle));
  }, [modules.rows, query]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await service.createModule(name.trim());
      await modules.reload();
      setCreating(false);
      setNotice(t("admin.moduleCreated", { name: name.trim() }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  const grantingModule = modules.rows.find((module) => module.id === granting);

  return (
    <section className="admin-section">
      <Notice success={notice} />

      {modules.loading ? (
        <p className="admin-muted" role="status">{t("common.loading")}</p>
      ) : modules.error ? (
        <Notice error={modules.error} />
      ) : modules.rows.length === 0 ? (
        <div className="admin-empty">
          <h3>{t("admin.modulesEmpty")}</h3>
          <p className="admin-muted">{t("admin.modulesEmptyHint")}</p>
        </div>
      ) : (
        <>
          <ListToolbar
            action={
              <button
                className="admin-button admin-button--primary"
                onClick={() => { setName(""); setError(undefined); setCreating(true); }}
                type="button"
              >
                {t("admin.newModule")}
              </button>
            }
            count={visible.length}
            label={plural("admin.modulesRecord", visible.length)}
            onQuery={setQuery}
            query={query}
          />
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">{t("admin.moduleName")}</th>
                <th scope="col">{t("admin.moduleCompanies")}</th>
                <th scope="col">{t("admin.moduleBindings")}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {visible.map((module) => (
                <tr key={module.id}>
                  <td>{module.name}</td>
                  <td>
                    {/* The scope is a setting, not a verdict: any module can be
                        granted to every company or to a chosen few, and the
                        choice is changed here. */}
                    <select
                      aria-label={t("admin.moduleScopeAria", { name: module.name })}
                      className="admin-input admin-input--inline"
                      onChange={(event) => {
                        const scope = event.target.value as "all" | "selected";
                        setSwitching(module.id);
                        void service
                          .setModuleScope(module.id, scope)
                          .then(async (updated) => {
                            await modules.reload();
                            setNotice(updated
                              ? t("admin.moduleScopeAll", { name: module.name })
                              : t("admin.moduleScopeSelected", { name: module.name }));
                          })
                          .catch((caught: unknown) => {
                            setNotice(undefined);
                            setError(caught instanceof Error ? caught.message : t("common.error"));
                            void modules.reload();
                          })
                          .finally(() => setSwitching(undefined));
                      }}
                      disabled={switching === module.id}
                      value={module.allCompanies ? "all" : "selected"}
                    >
                      <option value="all">{t("admin.moduleCompaniesAll")}</option>
                      <option value="selected">{t("admin.moduleCompaniesSelected")}</option>
                    </select>
                    {!module.allCompanies && module.companyIds.length > 0 && (
                      <span className="admin-chips admin-chips--below">
                        {module.companyIds.map((id) => (
                          <Link className="admin-chip" key={id} to={`/admin/companies/${encodeURIComponent(id)}`}>
                            {companiesById.get(id)?.name ?? id}
                          </Link>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="admin-muted">{module.bindings}</td>
                  <td className="admin-row-actions">
                    <button
                      className="admin-button"
                      disabled={module.allCompanies}
                      onClick={() => setGranting(module.id)}
                      title={module.allCompanies ? t("admin.moduleGrantDisabledAll") : undefined}
                      type="button"
                    >
                      {t("admin.moduleGrant")}
                    </button>
                    <ConfirmButton
                      confirmLabel={t("admin.moduleDeleteConfirm")}
                      label={t("admin.moduleDelete")}
                      onConfirm={() => {
                        void service.deleteModule(module.id).then(async () => {
                          await modules.reload();
                          setNotice(t("admin.moduleDeleted"));
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
        <Modal onClose={() => setCreating(false)} title={t("admin.newModuleTitle")}>
          <DialogForm busy={busy} onSubmit={(event) => void submit(event)} submitLabel={t("admin.moduleCreate")}>
            <Field label={t("admin.moduleName")}>
              <input
                autoFocus
                className="admin-input"
                maxLength={200}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("admin.moduleNamePlaceholder")}
                required
                value={name}
              />
            </Field>
            <Notice error={error} />
          </DialogForm>
        </Modal>
      )}

      {grantingModule && (
        <PickListModal
          initial={grantingModule.companyIds}
          items={companies.rows.map((company) => ({
            id: company.id,
            label: company.name,
            secondary: company.login,
          }))}
          onApply={async (next) => {
            await service.setModuleCompanies(grantingModule.id, next);
            await modules.reload();
            setGranting(undefined);
            setNotice(t("admin.moduleGranted"));
          }}
          onClose={() => setGranting(undefined)}
          title={t("admin.moduleGrantTitle", { name: grantingModule.name })}
        />
      )}
    </section>
  );
}
