import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import type { Translate } from "../../../i18n/catalogue";
import type { Locale } from "../../../i18n/core";
import { formatDateTime, formatNumber } from "../../../i18n/format";
import { useI18n } from "../../../i18n/useT";
import {
  catalogColumnMap,
  catalogFreshness,
  catalogMappingReady,
  catalogSourceColumns,
  hasFileRef,
  isRecord,
  sanitizeCatalogMessage,
  secretRef,
  type CatalogFreshnessReason,
  type CatalogRowBody,
  type EquipmentCatalogRow,
  type EquipmentCatalogWorkspace,
} from "./contracts";
import { EquipmentSourcesRequestError, type EquipmentSourcesErrorCode, type EquipmentSourcesService } from "./service";
import "./equipment-sources.css";

export interface EquipmentSourcesPageProps {
  readonly service: EquipmentSourcesService;
  readonly initialWorkspace?: EquipmentCatalogWorkspace;
}

type Notice = {
  readonly kind: "error" | "success" | "info";
  /** A sentence in the reader's language. */
  readonly message: string;
  /** The server's own text, shown as a second line and never as the sentence. */
  readonly detail?: string;
};

/** Каждый отказ сервиса — своей фразой; технические подробности идут отдельной строкой. */
const SOURCE_ERROR_KEYS: Record<EquipmentSourcesErrorCode, string> = {
  network: "equipment.srcErrNetwork",
  http: "equipment.srcErrHttp",
  invalidList: "equipment.srcErrInvalidList",
  invalidRow: "equipment.srcErrInvalidRow",
  invalidJson: "equipment.srcErrInvalidJson",
  moduleMissing: "equipment.srcErrModuleMissing",
  moduleUnconfirmed: "equipment.srcErrModuleUnconfirmed",
  actionUndefined: "equipment.srcErrActionUndefined",
  actionDisabled: "equipment.srcErrActionDisabled",
  uploadRejected: "equipment.srcErrUploadRejected",
  uploadIncomplete: "equipment.srcErrUploadIncomplete",
  secretIncomplete: "equipment.srcErrSecretIncomplete",
  serviceUnavailable: "equipment.srcErrServiceUnavailable",
};

/**
 * Отказ сервиса превращается в две части: переведённую фразу по коду и, если
 * сервер что-то пояснил, его собственный текст отдельной строкой.
 */
function sourceErrorNotice(t: Translate, error: unknown, sensitiveValues: readonly string[] = []): Notice {
  if (error instanceof EquipmentSourcesRequestError) {
    const detail = error.detail ? sanitizeCatalogMessage(error.detail, sensitiveValues, t) : "";
    return {
      kind: "error",
      message: t(SOURCE_ERROR_KEYS[error.code] ?? "equipment.actionFailed"),
      ...(detail ? { detail } : {}),
    };
  }
  const message = sanitizeCatalogMessage(error instanceof Error ? error.message : error, sensitiveValues, t);
  return { kind: "error", message: message || t("equipment.actionFailed") };
}

function currentSecrets(draft: CatalogRowBody, dsnInput: string, passwordInput: string): string[] {
  const values = [dsnInput, passwordInput];
  for (const value of [draft.remote_dsn, draft.remote_password]) {
    if (isRecord(value) && typeof value.value === "string") values.push(value.value);
  }
  return values.filter(Boolean);
}

function fileName(t: Translate, value: unknown): string {
  return hasFileRef(value) ? value.filename : t("equipment.srcFileMissing");
}

function sourceKind(body: CatalogRowBody): "local" | "remote" {
  return body.source_kind === "remote" ? "remote" : "local";
}

/** Значения статуса приходят кодами; человеку показывается слово. */
function statusLabel(t: Translate, status: unknown): string {
  if (status === "queued") return t("equipment.catalogStatusQueued");
  if (status === "indexing") return t("equipment.catalogStatusIndexing");
  if (status === "ready") return t("equipment.catalogStatusReady");
  if (status === "error") return t("equipment.catalogStatusError");
  if (status === "draft") return t("equipment.catalogStatusDraft");
  return t("equipment.catalogStatusUnknown");
}

function freshnessLabel(t: Translate, reason: CatalogFreshnessReason): string {
  if (reason === "fresh") return t("equipment.freshnessFresh");
  if (reason === "stale") return t("equipment.freshnessStale");
  if (reason === "error") return t("equipment.freshnessError");
  if (reason === "local") return t("equipment.freshnessLocal");
  return t("equipment.freshnessNeverIndexed");
}

/**
 * Значение каталога как его читает человек: числа — в конвенциях языка,
 * отсутствующее — словом, остальное — как пришло от сервера.
 */
function displayValue(t: Translate, locale: Locale, value: unknown): string {
  if (value === null || value === undefined || value === "") return t("equipment.srcValueMissing");
  if (typeof value === "number") return formatNumber(value, locale) || t("equipment.srcValueMissing");
  if (typeof value === "object") return t("equipment.srcValueConfigured");
  return String(value);
}

/** Дата каталога в конвенциях языка; нечитаемое значение показывается как есть. */
function dateValue(t: Translate, locale: Locale, value: unknown): string {
  const formatted = formatDateTime(typeof value === "string" || typeof value === "number" || value instanceof Date ? value : undefined, locale);
  return formatted || displayValue(t, locale, value);
}

function normalizeActionResult(
  t: Translate,
  value: unknown,
  operation: "probe" | "databases" | "tables" | "index",
): { readonly ok: boolean; readonly detail: string } {
  if (!isRecord(value)) return { ok: false, detail: t("equipment.srcActionEmpty") };
  if (value.status === "queued" && value.enqueued === false) return { ok: false, detail: t("equipment.srcActionQueuedFailed") };
  if (value.status === "error" || value.ok === false) {
    return { ok: false, detail: t("equipment.srcActionError", { error: String(value.error ?? t("equipment.srcUnknownError")) }) };
  }
  if (operation === "databases" && Array.isArray(value.databases)) return { ok: true, detail: t("equipment.srcActionDatabases", { count: value.databases.length }) };
  if (operation === "tables" && Array.isArray(value.tables)) return { ok: true, detail: t("equipment.srcActionTables", { count: value.tables.length }) };
  if (operation === "probe" && value.status === "draft") {
    if (value.needs_database) return { ok: true, detail: t("equipment.srcActionProbeDatabase") };
    if (value.needs_table) return { ok: true, detail: t("equipment.srcActionProbeTable") };
    if (Array.isArray(value.columns)) return { ok: true, detail: t("equipment.srcActionProbeColumns", { count: value.columns.length }) };
  }
  if (value.status === "queued" || value.status === "indexing") {
    return { ok: true, detail: t("equipment.srcActionIndexAccepted") };
  }
  if (value.status === "ready") {
    return { ok: true, detail: operation === "probe" ? t("equipment.srcActionProbeDone") : t("equipment.srcActionIndexDone") };
  }
  if (value.status === "skipped") {
    return { ok: false, detail: t("equipment.srcActionSkipped", { reason: String(value.reason ?? t("equipment.srcUnknownReason")) }) };
  }
  return { ok: false, detail: t("equipment.srcActionUnconfirmed") };
}

function CatalogStatus({ row }: { readonly row: EquipmentCatalogRow }) {
  const { t, locale } = useI18n();
  const body = row.body;
  const freshness = catalogFreshness(body, locale);
  const tone = body.status === "error" || freshness.freshness === "stale" ? "warning" : body.status === "ready" ? "good" : "muted";
  return <div className="equipment-source-status">
    <span className={`equipment-source-pill equipment-source-pill--${tone}`}>{statusLabel(t, body.status)}</span>
    <span className={`equipment-source-pill equipment-source-pill--${freshness.freshness === "fresh" ? "good" : freshness.freshness === "stale" ? "warning" : "muted"}`}>{freshnessLabel(t, freshness.reason)}</span>
    {body.paused === true && <span className="equipment-source-pill equipment-source-pill--warning">{t("equipment.catalogPaused")}</span>}
  </div>;
}

function SourceCard({ row, selected, onSelect }: {
  readonly row: EquipmentCatalogRow;
  readonly selected: boolean;
  readonly onSelect: () => void;
}) {
  const { t, locale } = useI18n();
  return <button className="equipment-source-row" type="button" aria-current={selected ? "true" : undefined} title={row.row_id} onClick={onSelect}>
    <span className="equipment-source-row__heading"><strong>{displayValue(t, locale, row.body.name)}</strong></span>
    <span className="equipment-source-row__meta">{sourceKind(row.body) === "remote" ? t("equipment.catalogKindRemote") : t("equipment.catalogKindLocal")} · {t("equipment.srcRowsLabel")}: {displayValue(t, locale, row.body.row_count)}</span>
    <CatalogStatus row={row} />
  </button>;
}

function MappingFields({ workspace, body, onChange }: {
  readonly workspace: EquipmentCatalogWorkspace;
  readonly body: CatalogRowBody;
  readonly onChange: (key: string, value: string) => void;
}) {
  const { t } = useI18n();
  const columns = catalogSourceColumns(body);
  const mapping = catalogColumnMap(body);
  if (workspace.metadata.mappingFields.length === 0) return <p className="equipment-source-notice equipment-source-notice--warning">{t("equipment.srcMappingNoSchema")}</p>;
  if (columns.length === 0) return <p className="equipment-source-help">{t("equipment.srcMappingNoColumns")}</p>;
  return <div className="equipment-source-mapping" aria-label={t("equipment.srcMappingAria")}>
    {workspace.metadata.mappingFields.map((field) => <label className="equipment-source-field" key={field.key}>
      <span>{field.label}{field.required ? " *" : ""}</span>
      <select value={mapping[field.key] ?? ""} onChange={(event) => onChange(field.key, event.target.value)}>
        <option value="">{t("equipment.srcMappingUnmapped")}</option>
        {columns.map((column) => <option key={column} value={column}>{column}</option>)}
      </select>
    </label>)}
  </div>;
}

function parseArrayResult(value: unknown, key: string, itemKey: string): string[] {
  if (!isRecord(value) || !Array.isArray(value[key])) return [];
  return value[key].flatMap((item) => isRecord(item) && typeof item[itemKey] === "string" ? [item[itemKey] as string] : []);
}

export function EquipmentSourcesPage({ service, initialWorkspace }: EquipmentSourcesPageProps) {
  const { t, locale } = useI18n();
  const [workspace, setWorkspace] = useState<EquipmentCatalogWorkspace | undefined>(initialWorkspace);
  const [selectedId, setSelectedId] = useState(initialWorkspace?.rows[0]?.row_id ?? "");
  const [draft, setDraft] = useState<CatalogRowBody>(() => initialWorkspace?.rows[0]
    ? { ...initialWorkspace.rows[0].body, column_map: catalogColumnMap(initialWorkspace.rows[0].body) }
    : { name: "", source_kind: "local", column_map: {} });
  const [isNew, setIsNew] = useState(!initialWorkspace?.rows[0]);
  const [loading, setLoading] = useState(!initialWorkspace);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File>();
  const [dsnInput, setDsnInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [databaseOptions, setDatabaseOptions] = useState<string[]>([]);
  const [tableOptions, setTableOptions] = useState<string[]>([]);
  const [notice, setNotice] = useState<Notice>();


  const selected = workspace?.rows.find((row) => row.row_id === selectedId);
  const localMode = sourceKind(draft) === "local";
  const mappingReady = workspace ? catalogMappingReady(catalogColumnMap(draft), workspace.metadata.mappingFields) : false;
  const paused = draft.paused === true;
  const savedBody = selected?.body;
  const sensitiveValues = currentSecrets(draft, dsnInput, passwordInput);
  const sourceConfigurationDirty = Boolean(selected && (
    file
    || draft.name !== savedBody?.name
    || draft.source_kind !== savedBody?.source_kind
    || draft.remote_dsn !== savedBody?.remote_dsn
    || draft.remote_password !== savedBody?.remote_password
    || dsnInput.trim()
    || passwordInput
    || draft.remote_database !== savedBody?.remote_database
    || draft.remote_table !== savedBody?.remote_table
    || draft.remote_user !== savedBody?.remote_user
    || draft.reindex_interval_hours !== savedBody?.reindex_interval_hours
    || JSON.stringify(catalogColumnMap(draft)) !== JSON.stringify(catalogColumnMap(savedBody ?? {}))
    || JSON.stringify(draft.source_file ?? null) !== JSON.stringify(savedBody?.source_file ?? null)
  ));

  async function refresh(selectId?: string) {
    setLoading(true);
    try {
      const next = await service.loadWorkspace();
      setWorkspace(next);
      const activeId = selectId ?? selectedId;
      const selectedRow = next.rows.find((row) => row.row_id === activeId) ?? next.rows[0];
      if (selectedRow) {
        setSelectedId(selectedRow.row_id);
        setDraft({ ...selectedRow.body, column_map: catalogColumnMap(selectedRow.body) });
        setIsNew(false);
      } else {
        setSelectedId("");
        setDraft({ name: "", source_kind: "local", column_map: {} });
        setIsNew(true);
      }
    } catch (error) {
      setNotice(sourceErrorNotice(t, error));
    } finally { setLoading(false); }
  }

  useEffect(() => {
    if (initialWorkspace) {
      const first = initialWorkspace.rows[0];
      setWorkspace(initialWorkspace);
      setLoading(false);
      if (first) {
        setSelectedId(first.row_id);
        setDraft({ ...first.body, column_map: catalogColumnMap(first.body) });
        setIsNew(false);
      } else setIsNew(true);
      return;
    }
    void refresh("");
  }, [initialWorkspace, service]);

  useEffect(() => {
    const active = workspace?.rows.find((row) => row.row_id === selectedId);
    if (!active || (active.body.status !== "queued" && active.body.status !== "indexing")) return undefined;
    const timer = window.setInterval(() => {
      void service.loadWorkspace().then((next) => {
        setWorkspace(next);
        const current = next.rows.find((row) => row.row_id === selectedId);
        if (current) setDraft({ ...current.body, column_map: catalogColumnMap(current.body) });
      }).catch((error: unknown) => {
        setNotice(sourceErrorNotice(t, error));
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [service, selectedId, workspace]);

  function selectRow(row: EquipmentCatalogRow) {
    setSelectedId(row.row_id);
    setDraft({ ...row.body, column_map: catalogColumnMap(row.body) });
    setIsNew(false);
    setFile(undefined);
    setDsnInput("");
    setPasswordInput("");
    setDatabaseOptions([]);
    setTableOptions([]);
    setNotice(undefined);
  }

  function newCatalog() {
    setSelectedId("");
    setDraft({ name: "", source_kind: "local", column_map: {}, status: "draft", paused: false, reindex_interval_hours: 24 });
    setIsNew(true);
    setFile(undefined);
    setDsnInput("");
    setPasswordInput("");
    setDatabaseOptions([]);
    setTableOptions([]);
    setNotice(undefined);
  }

  function patchDraft(patch: CatalogRowBody) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function setMapping(key: string, value: string) {
    const next = { ...catalogColumnMap(draft) };
    if (value) next[key] = value;
    else delete next[key];
    patchDraft({ column_map: next });
  }

  function setFileInput(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.currentTarget.files?.[0]);
  }


  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace) return;
    const name = typeof draft.name === "string" ? draft.name.trim() : "";
    if (!name) {
      setNotice({ kind: "error", message: t("equipment.srcNameRequired") });
      return;
    }
    setBusy(true);
    setNotice(undefined);
    try {
      const body: CatalogRowBody = { ...draft, name, column_map: catalogColumnMap(draft) };
      if (localMode) {
        if (file) body.source_file = await service.uploadFile(file);
        if (!body.source_file) throw new Error(t("equipment.srcFileRequired"));
        body.remote_dsn = null;
        body.remote_password = null;
        body.remote_database = null;
        body.remote_table = null;
      } else {
        if (dsnInput.trim()) body.remote_dsn = await service.uploadSecret(dsnInput.trim(), `${name} PostgreSQL DSN`);
        if (passwordInput) body.remote_password = await service.uploadSecret(passwordInput, `${name} PostgreSQL password`);
        if (!secretRef(body.remote_dsn)) throw new Error(t("equipment.srcDsnRequired"));
        body.source_file = null;
      }
      const saved = isNew ? await service.createCatalog(body) : await service.patchCatalog(selectedId, body);
      const next = await service.loadWorkspace();
      if (!next.rows.some((row) => row.row_id === saved.row_id)) throw new Error(t("equipment.srcSaveNotVisible"));
      setWorkspace(next);
      setSelectedId(saved.row_id);
      const confirmed = next.rows.find((row) => row.row_id === saved.row_id)!;
      setDraft({ ...confirmed.body, column_map: catalogColumnMap(confirmed.body) });
      setIsNew(false);
      setFile(undefined);
      setDsnInput("");
      setPasswordInput("");
      setNotice({ kind: "success", message: t("equipment.srcSaveConfirm") });
    } catch (error) {
      setNotice(sourceErrorNotice(t, error, sensitiveValues));
    } finally { setBusy(false); }
  }

  async function runOperation(operation: "probe" | "databases" | "tables" | "index") {
    if (!selected || !workspace) return;
    setBusy(true);
    setNotice(undefined);
    try {
      const result = await service.invoke(selected.row_id, operation);
      const outcome = normalizeActionResult(t, result, operation);
      const next = await service.loadWorkspace();
      setWorkspace(next);
      const current = next.rows.find((row) => row.row_id === selected.row_id);
      if (current) setDraft({ ...current.body, column_map: catalogColumnMap(current.body) });
      if (!outcome.ok) {
        setNotice({ kind: "error", message: sanitizeCatalogMessage(outcome.detail) });
        return;
      }
      if (operation === "databases") setDatabaseOptions(parseArrayResult(result, "databases", "name"));
      if (operation === "tables") setTableOptions(parseArrayResult(result, "tables", "name"));
      setNotice({ kind: "success", message: `${sanitizeCatalogMessage(outcome.detail)} ${t("equipment.srcActionRechecked")}` });
    } catch (error) {
      setNotice(sourceErrorNotice(t, error, sensitiveValues));
      try {
        const next = await service.loadWorkspace();
        setWorkspace(next);
        const current = next.rows.find((row) => row.row_id === selected.row_id);
        if (current) setDraft({ ...current.body, column_map: catalogColumnMap(current.body) });
      } catch { /* Keep the original failure visible. */ }
    } finally { setBusy(false); }
  }

  const unavailable = {
    probe: !workspace?.metadata.probeAction?.enabled,
    databases: !workspace?.metadata.databasesAction?.enabled,
    tables: !workspace?.metadata.tablesAction?.enabled,
    index: !workspace?.metadata.indexAction?.enabled,
  };

  return <main className="equipment-sources" aria-busy={loading}>
    <header className="equipment-sources__header">
      <div><p className="equipment-sources__eyebrow">{t("equipment.srcEyebrow")}</p><h1>{t("equipment.srcTitle")}</h1><p className="equipment-sources__lede">{t("equipment.srcLede")}</p></div>
      <div className="equipment-sources__header-actions"><button aria-label={t("equipment.srcRefresh")} className="equipment-sources__button" disabled={loading} onClick={() => void refresh(selectedId)} title={t("equipment.srcRefreshTitle")} type="button">↻</button><button className="equipment-sources__button equipment-sources__button--primary" type="button" onClick={newCatalog}>+ {t("equipment.srcNew")}</button></div>
    </header>

    {notice && <div className={`equipment-source-notice equipment-source-notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}><span>{notice.message}</span>{notice.detail && <span className="equipment-source-notice__detail">{notice.detail}</span>}</div>}
    {loading && !workspace && <p className="equipment-sources__loading" role="status">{t("equipment.srcLoading")}</p>}

    {workspace && <div className="equipment-sources__layout">
      <aside className="equipment-sources__catalog" aria-label={t("equipment.srcListAria")}>
        <div className="equipment-sources__section-heading"><h2>{t("equipment.srcSourcesHeading")}</h2><span>{workspace.rows.length}</span></div>
        {workspace.rows.length === 0 && <p className="equipment-sources__empty">{t("equipment.srcEmpty")}</p>}
        {workspace.rows.map((row) => <SourceCard key={row.row_id} row={row} selected={selectedId === row.row_id && !isNew} onSelect={() => selectRow(row)} />)}
      </aside>

      <section className="equipment-sources__detail" aria-label={t("equipment.srcDetailAria")}>
        <div className="equipment-sources__section-heading"><div><p className="equipment-sources__eyebrow" title={isNew ? undefined : selected?.row_id}>{isNew ? t("equipment.srcNewSource") : t("equipment.srcCardEyebrow")}</p><h2>{isNew ? t("equipment.srcCatalogParams") : displayValue(t, locale, draft.name)}</h2></div>
          {!isNew && selected && <CatalogStatus row={selected} />}
        </div>

        <form onSubmit={(event) => void save(event)}>
          <section className="equipment-sources__section">
            <div className="equipment-sources__section-heading"><h3>{t("equipment.srcSectionSource")}</h3><span>{t("equipment.srcSectionSourceHint")}</span></div>
            <div className="equipment-sources__fields">
              <label className="equipment-source-field equipment-source-field--wide"><span>{t("equipment.srcName")}</span><input required maxLength={200} value={typeof draft.name === "string" ? draft.name : ""} onChange={(event) => patchDraft({ name: event.target.value })} /></label>
              <label className="equipment-source-field"><span>{t("equipment.srcKind")}</span><select value={sourceKind(draft)} onChange={(event) => { patchDraft({ source_kind: event.target.value, column_map: {}, columns_json: null, status: "draft", error: null }); setFile(undefined); setDsnInput(""); setPasswordInput(""); setDatabaseOptions([]); setTableOptions([]); }}><option value="local">{t("equipment.srcLocalFile")}</option><option value="remote">{t("equipment.srcRemote")}</option></select></label>
              {localMode ? <>
                <label className="equipment-source-field equipment-source-field--wide"><span>{t("equipment.srcFileLabel")}</span><input accept=".csv,.xlsx,.xls" type="file" onChange={setFileInput} /><small>{file ? t("equipment.srcFilePending", { name: file.name }) : fileName(t, draft.source_file)}</small></label>
              </> : <>
                <label className="equipment-source-field equipment-source-field--wide"><span>{t("equipment.srcDsnLabel")}</span><input autoComplete="new-password" type="password" placeholder={secretRef(draft.remote_dsn) ? t("equipment.srcDsnPlaceholderSaved") : t("equipment.srcDsnPlaceholderNew")} value={dsnInput} onChange={(event) => setDsnInput(event.target.value)} /><small>{secretRef(draft.remote_dsn) ? t("equipment.srcDsnHintSaved") : t("equipment.srcDsnHintNew")}</small></label>
                <label className="equipment-source-field"><span>{t("equipment.srcUserLabel")}</span><input autoComplete="off" value={typeof draft.remote_user === "string" ? draft.remote_user : ""} onChange={(event) => patchDraft({ remote_user: event.target.value })} /></label>
                <label className="equipment-source-field"><span>{t("equipment.srcPasswordLabel")}</span><input autoComplete="new-password" type="password" value={passwordInput} onChange={(event) => setPasswordInput(event.target.value)} placeholder={secretRef(draft.remote_password) ? t("equipment.srcPasswordPlaceholderSaved") : t("equipment.srcPasswordPlaceholderOptional")} /><small>{secretRef(draft.remote_password) ? t("equipment.srcPasswordHintSaved") : t("equipment.srcPasswordHintEmpty")}</small></label>
                <label className="equipment-source-field"><span>{t("equipment.srcIntervalLabel")}</span><input min={1} max={8760} type="number" value={typeof draft.reindex_interval_hours === "number" ? draft.reindex_interval_hours : 24} onChange={(event) => patchDraft({ reindex_interval_hours: Number(event.target.value) })} /></label>
                <div className="equipment-source-field equipment-source-field--wide equipment-source-actions">
                  <button className="equipment-sources__button" disabled={busy || unavailable.probe || !selected || sourceConfigurationDirty} onClick={() => void runOperation("probe")} title={sourceConfigurationDirty ? t("equipment.srcSaveBeforeAction") : unavailable.probe ? t("equipment.srcActionMissing") : undefined} type="button">{t("equipment.srcCheckConnection")}</button>
                  <button className="equipment-sources__button" disabled={busy || unavailable.databases || !selected || sourceConfigurationDirty} onClick={() => void runOperation("databases")} title={sourceConfigurationDirty ? t("equipment.srcSaveBeforeAction") : unavailable.databases ? t("equipment.srcActionMissing") : undefined} type="button">{t("equipment.srcListDatabases")}</button>
                  <button className="equipment-sources__button" disabled={busy || unavailable.tables || !selected || (!draft.remote_database && draft.remote_dsn_has_database !== true) || sourceConfigurationDirty} onClick={() => void runOperation("tables")} title={sourceConfigurationDirty ? t("equipment.srcSaveBeforeAction") : unavailable.tables ? t("equipment.srcActionMissing") : undefined} type="button">{t("equipment.srcListTables")}</button>
                  {(unavailable.probe || unavailable.databases || unavailable.tables) && <small>{t("equipment.srcActionsDisabledNote")}</small>}
                </div>
                <label className="equipment-source-field"><span>{t("equipment.srcDatabaseLabel")}</span><select value={typeof draft.remote_database === "string" ? draft.remote_database : ""} onChange={(event) => patchDraft({ remote_database: event.target.value, remote_table: "" })}><option value="">{draft.remote_dsn_url_has_database === true ? t("equipment.srcDatabaseInDsn") : t("equipment.srcDatabaseChoose")}</option>{typeof draft.remote_database === "string" && draft.remote_database && !databaseOptions.includes(draft.remote_database) && <option value={draft.remote_database}>{draft.remote_database}</option>}{databaseOptions.map((database) => <option key={database} value={database}>{database}</option>)}</select><small>{t("equipment.srcDatabaseHint")}</small></label>
                <label className="equipment-source-field"><span>{t("equipment.srcTableLabel")}</span><select value={typeof draft.remote_table === "string" ? draft.remote_table : ""} onChange={(event) => patchDraft({ remote_table: event.target.value })}><option value="">{t("equipment.srcTableChoose")}</option>{typeof draft.remote_table === "string" && draft.remote_table && !tableOptions.includes(draft.remote_table) && <option value={draft.remote_table}>{draft.remote_table}</option>}{tableOptions.map((table) => <option key={table} value={table}>{table}</option>)}</select><small>{t("equipment.srcTableHint")}</small></label>
              </>}
            </div>
          </section>

          <section className="equipment-sources__section">
            <div className="equipment-sources__section-heading"><h3>{t("equipment.srcSectionMapping")}</h3><span>{t("equipment.srcColumnsCount", { count: catalogSourceColumns(draft).length })}</span></div>
            <MappingFields workspace={workspace} body={draft} onChange={setMapping} />
            {!mappingReady && <p className="equipment-source-notice equipment-source-notice--warning">{t("equipment.srcReindexBlocked")}</p>}
          </section>

          <div className="equipment-sources__footer-actions">
            <button className="equipment-sources__button equipment-sources__button--primary" disabled={busy || (paused && !isNew)} title={paused && !isNew ? t("equipment.srcPauseTitle") : undefined} type="submit">{busy ? t("equipment.srcSaving") : isNew ? t("equipment.srcCreate") : t("equipment.srcSave")}</button>
            {!isNew && selected && <button className="equipment-sources__button" disabled={busy || unavailable.index || paused || !mappingReady || sourceConfigurationDirty} onClick={() => void runOperation("index")} type="button" title={paused ? t("equipment.srcSourcePaused") : unavailable.index ? t("equipment.srcActionMissing") : !mappingReady ? t("equipment.srcMappingIncomplete") : sourceConfigurationDirty ? t("equipment.srcSaveFirst") : undefined}>{busy ? t("equipment.srcProcessing") : t("equipment.srcReindex")}</button>}
            {!isNew && selected && <button className="equipment-sources__button" disabled title={t("equipment.srcPauseButtonTitle")}>{paused ? t("equipment.srcResumeUnavailable") : t("equipment.srcPauseUnavailable")}</button>}
          </div>
          {!isNew && selected && <p className="equipment-source-notice equipment-source-notice--warning">{t("equipment.srcPauseNote")}</p>}
          {!workspace.metadata.indexAction && <p className="equipment-source-notice equipment-source-notice--warning">{t("equipment.srcIndexMissing")}</p>}
          {workspace.metadata.indexAction && !workspace.metadata.indexAction.enabled && <p className="equipment-source-notice equipment-source-notice--warning">{t("equipment.srcIndexDisabled")}</p>}
        </form>

        {selected && <section className="equipment-sources__section equipment-sources__lifecycle">
          <div className="equipment-sources__section-heading"><h3>{t("equipment.srcLifecycleTitle")}</h3><span>{statusLabel(t, selected.body.status)}</span></div>
          <dl><div><dt>{t("equipment.srcLastIndex")}</dt><dd>{dateValue(t, locale, selected.body.last_indexed_at)}</dd></div><div><dt>{t("equipment.srcRowsLabel")}</dt><dd>{displayValue(t, locale, selected.body.row_count)}</dd></div><div><dt>{t("equipment.srcProgress")}</dt><dd>{selected.body.status === "indexing" || selected.body.status === "queued" ? `${displayValue(t, locale, selected.body.indexed_count)} / ${displayValue(t, locale, selected.body.total_rows)}` : t("equipment.srcProgressNone")}</dd></div></dl>
          {selected.body.status === "error" && <p className="equipment-source-notice equipment-source-notice--error">{t("equipment.srcIndexError", { error: sanitizeCatalogMessage(selected.body.error, [], t) })}</p>}
          {(() => {
            const freshness = catalogFreshness(selected.body, locale);
            if (freshness.freshness !== "stale" && freshness.freshness !== "unknown") return null;
            const label = freshnessLabel(t, freshness.reason);
            return <p className="equipment-source-notice equipment-source-notice--warning">{freshness.lastGoodLabel
              ? t("equipment.srcStaleNoteLast", { label, time: freshness.lastGoodLabel })
              : t("equipment.srcStaleNoteNoIndex", { label })}</p>;
          })()}
          {selected.body.remote_auth_failed === true && <p className="equipment-source-notice equipment-source-notice--warning">{t("equipment.srcAuthFailed")}</p>}
          {Boolean(selected.body.error) && selected.body.status !== "error" && <p className="equipment-source-notice equipment-source-notice--warning">{sanitizeCatalogMessage(selected.body.error, [], t)}</p>}
        </section>}
      </section>
    </div>}

  </main>;
}
