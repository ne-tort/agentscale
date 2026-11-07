import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../../i18n/useT";
import { useAdminService } from "./AdminArea";
import { CompanyPicker } from "./picker";
import { PickListModal } from "./PickListModal";
import { ConfirmButton, DialogForm, Field, ListToolbar, Modal, Notice, useCollection } from "./ui";

/**
 * API keys — one row per key, showing where it points and which companies may use it.
 *
 * A key is an endpoint plus a secret. There is no provider to choose from: the
 * administrator knows the address their key belongs to, and picking a provider
 * from a list we maintain only ever meant "guess which entry we seeded for you".
 * Two keys on the same endpoint share one endpoint record on the server, so the
 * address is entered once per provider, not once per key.
 *
 * Everything past the address — the path models are listed on, the
 * authorisation header — sits behind «Дополнительно», because a person adding a
 * key normally only has the two things the provider gave them.
 */
export function KeysPage() {
  const service = useAdminService();
  const { t, plural } = useI18n();
  const keys = useCollection(() => service.listKeys(), [service]);
  const companies = useCollection(() => service.listCompanies(), [service]);

  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [modelsPath, setModelsPath] = useState("");
  const [chatPath, setChatPath] = useState("");
  const [authScheme, setAuthScheme] = useState<"bearer" | "x-api-key">("bearer");
  const [advanced, setAdvanced] = useState(false);
  const [companyIds, setCompanyIds] = useState<string[]>([]);
  const [linking, setLinking] = useState<string>();

  const companiesById = useMemo(
    () => new Map(companies.rows.map((company) => [company.id, company])),
    [companies.rows],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return keys.rows;
    return keys.rows.filter((key) =>
      [key.name, key.baseUrl ?? "", key.provider].join(" ").toLowerCase().includes(needle),
    );
  }, [keys.rows, query]);

  function startCreating() {
    setName("");
    setBaseUrl("");
    setSecret("");
    setModelsPath("");
    setChatPath("");
    setAuthScheme("bearer");
    setAdvanced(false);
    setCompanyIds([]);
    setError(undefined);
    setCreating(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const endpoint = baseUrl.trim();
    if (!/^https?:\/\//i.test(endpoint)) {
      setError(t("admin.keyEndpointInvalid"));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await service.createKey({
        name: name.trim(),
        baseUrl: endpoint,
        secret,
        companyIds,
        // Paths and the header live on the shared endpoint record, not on the
        // key. Sending a default would conflict with an endpoint that was
        // configured differently by whoever added the first key for it — so
        // these go only when the administrator actually opened and set them.
        ...(modelsPath.trim() ? { modelsPath: modelsPath.trim() } : {}),
        ...(chatPath.trim() ? { chatCompletionsPath: chatPath.trim() } : {}),
        ...(advanced ? { authScheme } : {}),
      });
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
  const createButton = (
    <button className="admin-button admin-button--primary" onClick={startCreating} type="button">
      {t("admin.newKey")}
    </button>
  );

  return (
    <section className="admin-section">
      <Notice success={notice} />

      {keys.loading ? (
        <p className="admin-muted" role="status">{t("common.loading")}</p>
      ) : keys.error ? (
        <Notice error={keys.error} />
      ) : keys.rows.length === 0 ? (
        <div className="admin-empty">
          <h3>{t("admin.keysEmpty")}</h3>
          <p className="admin-muted">{t("admin.keysEmptyHint")}</p>
          {createButton}
        </div>
      ) : (
        <>
          <ListToolbar
            action={createButton}
            count={visible.length}
            label={plural("admin.keysRecord", visible.length)}
            onQuery={setQuery}
            query={query}
          />
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">{t("admin.keyName")}</th>
                <th scope="col">{t("admin.keyEndpointColumn")}</th>
                <th scope="col">{t("admin.keyCompanies")}</th>
                <th scope="col">{t("common.status")}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {visible.map((key) => (
                <tr key={key.id}>
                  <td>{key.name}</td>
                  <td className="admin-muted">
                    {key.baseUrl ? (
                      <span className="admin-endpoint" title={key.baseUrl}>{hostOf(key.baseUrl)}</span>
                    ) : (
                      t("admin.keyEndpointUnknown")
                    )}
                  </td>
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
            <Field hint={t("admin.keyEndpointHint")} label={t("admin.keyEndpoint")}>
              <input
                autoComplete="off"
                className="admin-input"
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder={t("admin.keyEndpointPlaceholder")}
                required
                spellCheck={false}
                type="url"
                value={baseUrl}
              />
            </Field>
            <Field hint={t("admin.keySecretHint")} label={t("admin.keySecret")}>
              <input
                autoComplete="off"
                className="admin-input"
                onChange={(event) => setSecret(event.target.value)}
                required
                spellCheck={false}
                type="password"
                value={secret}
              />
            </Field>
            <details className="admin-advanced" onToggle={(event) => setAdvanced(event.currentTarget.open)}>
              <summary>{t("admin.keyAdvanced")}</summary>
              <Field label={t("admin.keyModelsPath")}>
                <input
                  className="admin-input"
                  onChange={(event) => setModelsPath(event.target.value)}
                  placeholder="/models"
                  spellCheck={false}
                  value={modelsPath}
                />
              </Field>
              <Field label={t("admin.keyAuthScheme")}>
                <select
                  className="admin-input"
                  onChange={(event) => setAuthScheme(event.target.value === "x-api-key" ? "x-api-key" : "bearer")}
                  value={authScheme}
                >
                  <option value="bearer">{t("admin.keyAuthBearer")}</option>
                  <option value="x-api-key">{t("admin.keyAuthApiKey")}</option>
                </select>
              </Field>
              <Field label={t("admin.keyChatPath")}>
                <input
                  className="admin-input"
                  onChange={(event) => setChatPath(event.target.value)}
                  placeholder="/chat/completions"
                  spellCheck={false}
                  value={chatPath}
                />
              </Field>
            </details>
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

/** The part of an endpoint a person recognises: `api.openai.com`, not the full path. */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function statusLabel(status: string, t: (key: string) => string): string {
  if (status === "revoked") return t("admin.keyStatusRevoked");
  if (status === "expired") return t("admin.keyStatusExpired");
  return t("admin.keyStatusActive");
}
