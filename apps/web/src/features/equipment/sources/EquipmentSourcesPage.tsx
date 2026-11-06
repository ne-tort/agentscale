import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import {
  catalogColumnMap,
  catalogFreshness,
  catalogMappingReady,
  catalogSourceColumns,
  displayCatalogValue,
  hasFileRef,
  isRecord,
  sanitizeCatalogMessage,
  secretRef,
  type CatalogRowBody,
  type EquipmentCatalogRow,
  type EquipmentCatalogWorkspace,
} from "./contracts";
import type { EquipmentSourcesService } from "./service";
import "./equipment-sources.css";

export interface EquipmentSourcesPageProps {
  readonly service: EquipmentSourcesService;
  readonly initialWorkspace?: EquipmentCatalogWorkspace;
}

type Notice = { readonly kind: "error" | "success" | "info"; readonly message: string };

function currentSecrets(draft: CatalogRowBody, dsnInput: string, passwordInput: string): string[] {
  const values = [dsnInput, passwordInput];
  for (const value of [draft.remote_dsn, draft.remote_password]) {
    if (isRecord(value) && typeof value.value === "string") values.push(value.value);
  }
  return values.filter(Boolean);
}

function fileName(value: unknown): string {
  return hasFileRef(value) ? value.filename : "Файл не загружен";
}

function sourceKind(body: CatalogRowBody): "local" | "remote" {
  return body.source_kind === "remote" ? "remote" : "local";
}

function statusLabel(status: unknown): string {
  if (status === "queued") return "В очереди";
  if (status === "indexing") return "Индексация";
  if (status === "ready") return "Готов";
  if (status === "error") return "Ошибка";
  return "Черновик";
}

function normalizeActionResult(value: unknown, operation: "probe" | "databases" | "tables" | "index"): { readonly ok: boolean; readonly detail: string } {
  if (!isRecord(value)) return { ok: false, detail: "API вернул пустой результат действия." };
  if (value.status === "queued" && value.enqueued === false) return { ok: false, detail: "API сообщил queued, но задача не была поставлена в очередь." };
  if (value.status === "error" || value.ok === false) return { ok: false, detail: `API сообщил об ошибке: ${String(value.error ?? "неизвестная ошибка")}.` };
  if (operation === "databases" && Array.isArray(value.databases)) return { ok: true, detail: `Получено баз данных: ${value.databases.length}.` };
  if (operation === "tables" && Array.isArray(value.tables)) return { ok: true, detail: `Получено таблиц: ${value.tables.length}.` };
  if (operation === "probe" && value.status === "draft") {
    if (value.needs_database) return { ok: true, detail: "Подключение проверено; выберите базу данных." };
    if (value.needs_table) return { ok: true, detail: "Подключение проверено; выберите таблицу." };
    if (Array.isArray(value.columns)) return { ok: true, detail: `Схема проверена, колонок: ${value.columns.length}.` };
  }
  if (value.status === "queued" || value.status === "indexing") {
    return { ok: true, detail: `Индексация принята: ${String(value.status)}.` };
  }
  if (value.status === "ready") {
    return { ok: true, detail: operation === "probe" ? "Схема и подключение проверены." : "Индексация завершена." };
  }
  if (value.status === "skipped") return { ok: false, detail: `Операция пропущена API: ${String(value.reason ?? "причина не указана")}.` };
  return { ok: false, detail: "API не подтвердил успешное завершение действия." };
}

function CatalogStatus({ row }: { readonly row: EquipmentCatalogRow }) {
  const body = row.body;
  const freshness = catalogFreshness(body);
  const tone = body.status === "error" || freshness.freshness === "stale" ? "warning" : body.status === "ready" ? "good" : "muted";
  return <div className="equipment-source-status">
    <span className={`equipment-source-pill equipment-source-pill--${tone}`}>{statusLabel(body.status)}</span>
    <span className={`equipment-source-pill equipment-source-pill--${freshness.freshness === "fresh" ? "good" : freshness.freshness === "stale" ? "warning" : "muted"}`}>{freshness.label}</span>
    {body.paused === true && <span className="equipment-source-pill equipment-source-pill--warning">Пауза</span>}
  </div>;
}

function SourceCard({ row, selected, onSelect }: {
  readonly row: EquipmentCatalogRow;
  readonly selected: boolean;
  readonly onSelect: () => void;
}) {
  return <button className="equipment-source-row" type="button" aria-current={selected ? "true" : undefined} onClick={onSelect}>
    <span className="equipment-source-row__heading"><strong>{displayCatalogValue(row.body.name)}</strong><code>{row.row_id}</code></span>
    <span className="equipment-source-row__meta">{sourceKind(row.body) === "remote" ? "Remote SQL" : "Local file"} · {displayCatalogValue(row.body.row_count)} строк</span>
    <CatalogStatus row={row} />
  </button>;
}

function MappingFields({ workspace, body, onChange }: {
  readonly workspace: EquipmentCatalogWorkspace;
  readonly body: CatalogRowBody;
  readonly onChange: (key: string, value: string) => void;
}) {
  const columns = catalogSourceColumns(body);
  const mapping = catalogColumnMap(body);
  if (workspace.metadata.mappingFields.length === 0) return <p className="equipment-source-notice equipment-source-notice--warning">Metadata не объявляет schema для column_map. Сопоставление и индексация недоступны, пока это не исправлено.</p>;
  if (columns.length === 0) return <p className="equipment-source-help">Сначала загрузите файл или выполните probe удалённой таблицы, чтобы получить список колонок.</p>;
  return <div className="equipment-source-mapping" aria-label="Сопоставление колонок источника">
    {workspace.metadata.mappingFields.map((field) => <label className="equipment-source-field" key={field.key}>
      <span>{field.label}{field.required ? " *" : ""}</span>
      <select value={mapping[field.key] ?? ""} onChange={(event) => onChange(field.key, event.target.value)}>
        <option value="">Не сопоставлено</option>
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
      setNotice({ kind: "error", message: sanitizeCatalogMessage(error instanceof Error ? error.message : error) });
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
        setNotice({ kind: "error", message: sanitizeCatalogMessage(error instanceof Error ? error.message : error) });
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
      setNotice({ kind: "error", message: "Укажите название каталога." });
      return;
    }
    setBusy(true);
    setNotice(undefined);
    try {
      const body: CatalogRowBody = { ...draft, name, column_map: catalogColumnMap(draft) };
      if (localMode) {
        if (file) body.source_file = await service.uploadFile(file);
        if (!body.source_file) throw new Error("Загрузите CSV или Excel файл перед сохранением локального каталога.");
        body.remote_dsn = null;
        body.remote_password = null;
        body.remote_database = null;
        body.remote_table = null;
      } else {
        if (dsnInput.trim()) body.remote_dsn = await service.uploadSecret(dsnInput.trim(), `${name} PostgreSQL DSN`);
        if (passwordInput) body.remote_password = await service.uploadSecret(passwordInput, `${name} PostgreSQL password`);
        if (!secretRef(body.remote_dsn)) throw new Error("Сохранённый secret reference для PostgreSQL DSN отсутствует.");
        body.source_file = null;
      }
      const saved = isNew ? await service.createCatalog(body) : await service.patchCatalog(selectedId, body);
      const next = await service.loadWorkspace();
      if (!next.rows.some((row) => row.row_id === saved.row_id)) throw new Error("API сохранил запрос, но строка каталога не появилась при повторном чтении.");
      setWorkspace(next);
      setSelectedId(saved.row_id);
      const confirmed = next.rows.find((row) => row.row_id === saved.row_id)!;
      setDraft({ ...confirmed.body, column_map: catalogColumnMap(confirmed.body) });
      setIsNew(false);
      setFile(undefined);
      setDsnInput("");
      setPasswordInput("");
      setNotice({ kind: "success", message: "Источник сохранён и подтверждён повторным чтением API." });
    } catch (error) {
      setNotice({ kind: "error", message: sanitizeCatalogMessage(error instanceof Error ? error.message : error, sensitiveValues) });
    } finally { setBusy(false); }
  }

  async function runOperation(operation: "probe" | "databases" | "tables" | "index") {
    if (!selected || !workspace) return;
    setBusy(true);
    setNotice(undefined);
    try {
      const result = await service.invoke(selected.row_id, operation);
      const outcome = normalizeActionResult(result, operation);
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
      setNotice({ kind: "success", message: `${sanitizeCatalogMessage(outcome.detail)} Статус перечитан из API.` });
    } catch (error) {
      setNotice({ kind: "error", message: sanitizeCatalogMessage(error instanceof Error ? error.message : error, sensitiveValues) });
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
  const unsavedRemoteCredentials = Boolean(dsnInput.trim() || passwordInput);

  return <main className="equipment-sources" aria-busy={loading}>
    <header className="equipment-sources__header">
      <div><p className="equipment-sources__eyebrow">Admin / mod_equipment / catalogs</p><h1>Каталоги источников</h1><p className="equipment-sources__lede">Конфигурация локальных файлов и подключений PostgreSQL с подтверждённым статусом индекса.</p></div>
      <div className="equipment-sources__header-actions"><button aria-label="Обновить список каталогов" className="equipment-sources__button" disabled={loading} onClick={() => void refresh(selectedId)} title="Обновить из API" type="button">↻</button><button className="equipment-sources__button equipment-sources__button--primary" type="button" onClick={newCatalog}>+ Новый каталог</button></div>
    </header>

    {notice && <div className={`equipment-source-notice equipment-source-notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.message}</div>}
    {loading && !workspace && <p className="equipment-sources__loading" role="status">Загрузка catalog rows и metadata…</p>}

    {workspace && <div className="equipment-sources__layout">
      <aside className="equipment-sources__catalog" aria-label="Каталоги источников">
        <div className="equipment-sources__section-heading"><h2>Источники</h2><span>{workspace.rows.length}</span></div>
        {workspace.rows.length === 0 && <p className="equipment-sources__empty">В API пока нет строк catalogs.</p>}
        {workspace.rows.map((row) => <SourceCard key={row.row_id} row={row} selected={selectedId === row.row_id && !isNew} onSelect={() => selectRow(row)} />)}
      </aside>

      <section className="equipment-sources__detail" aria-label="Настройка источника">
        <div className="equipment-sources__section-heading"><div><p className="equipment-sources__eyebrow">{isNew ? "Новый источник" : selected?.row_id}</p><h2>{isNew ? "Параметры каталога" : displayCatalogValue(draft.name)}</h2></div>
          {!isNew && selected && <CatalogStatus row={selected} />}
        </div>

        <form onSubmit={(event) => void save(event)}>
          <section className="equipment-sources__section">
            <div className="equipment-sources__section-heading"><h3>Источник данных</h3><span>Запись `catalogs`</span></div>
            <div className="equipment-sources__fields">
              <label className="equipment-source-field equipment-source-field--wide"><span>Название</span><input required maxLength={200} value={typeof draft.name === "string" ? draft.name : ""} onChange={(event) => patchDraft({ name: event.target.value })} /></label>
              <label className="equipment-source-field"><span>Тип источника</span><select value={sourceKind(draft)} onChange={(event) => { patchDraft({ source_kind: event.target.value, column_map: {}, columns_json: null, status: "draft", error: null }); setFile(undefined); setDsnInput(""); setPasswordInput(""); setDatabaseOptions([]); setTableOptions([]); }}><option value="local">Локальный файл</option><option value="remote">Удалённый PostgreSQL</option></select></label>
              {localMode ? <>
                <label className="equipment-source-field equipment-source-field--wide"><span>Файл каталога (.csv, .xlsx, .xls)</span><input accept=".csv,.xlsx,.xls" type="file" onChange={setFileInput} /><small>{file ? `К загрузке: ${file.name}` : fileName(draft.source_file)}</small></label>
              </> : <>
                <label className="equipment-source-field equipment-source-field--wide"><span>PostgreSQL DSN (секрет)</span><input autoComplete="new-password" type="password" placeholder={secretRef(draft.remote_dsn) ? "Сохранённый DSN не отображается; введите новый для замены" : "postgresql://user@host:5432/database"} value={dsnInput} onChange={(event) => setDsnInput(event.target.value)} /><small>{secretRef(draft.remote_dsn) ? "Секрет сохранён через secrets API; значение скрыто." : "DSN передаётся напрямую в secrets API, не в строку catalogs."}</small></label>
                <label className="equipment-source-field"><span>Логин (если не в DSN)</span><input autoComplete="off" value={typeof draft.remote_user === "string" ? draft.remote_user : ""} onChange={(event) => patchDraft({ remote_user: event.target.value })} /></label>
                <label className="equipment-source-field"><span>Пароль (секрет, если не в DSN)</span><input autoComplete="new-password" type="password" value={passwordInput} onChange={(event) => setPasswordInput(event.target.value)} placeholder={secretRef(draft.remote_password) ? "Сохранённый пароль не отображается" : "Необязательно"} /><small>{secretRef(draft.remote_password) ? "Секрет сохранён; новое значение заменит его через secrets API." : "Пустое значение не сохраняется."}</small></label>
                <label className="equipment-source-field"><span>Интервал обновления, ч</span><input min={1} max={8760} type="number" value={typeof draft.reindex_interval_hours === "number" ? draft.reindex_interval_hours : 24} onChange={(event) => patchDraft({ reindex_interval_hours: Number(event.target.value) })} /></label>
                <div className="equipment-source-field equipment-source-field--wide equipment-source-actions">
                  <button className="equipment-sources__button" disabled={busy || unavailable.probe || !selected || sourceConfigurationDirty} onClick={() => void runOperation("probe")} title={sourceConfigurationDirty ? "Сохраните конфигурацию перед probe" : unavailable.probe ? "Action отсутствует в metadata" : undefined} type="button">Проверить подключение</button>
                  <button className="equipment-sources__button" disabled={busy || unavailable.databases || !selected || sourceConfigurationDirty} onClick={() => void runOperation("databases")} title={sourceConfigurationDirty ? "Сохраните конфигурацию перед списком баз" : unavailable.databases ? "Action отсутствует в metadata" : undefined} type="button">Получить базы</button>
                  <button className="equipment-sources__button" disabled={busy || unavailable.tables || !selected || (!draft.remote_database && draft.remote_dsn_has_database !== true) || sourceConfigurationDirty} onClick={() => void runOperation("tables")} title={sourceConfigurationDirty ? "Сохраните конфигурацию перед списком таблиц" : unavailable.tables ? "Action отсутствует в metadata" : undefined} type="button">Получить таблицы</button>
                  {(unavailable.probe || unavailable.databases || unavailable.tables) && <small>Действие отключено или не объявлено в actions metadata.</small>}
                </div>
                <label className="equipment-source-field"><span>База данных</span><select value={typeof draft.remote_database === "string" ? draft.remote_database : ""} onChange={(event) => patchDraft({ remote_database: event.target.value, remote_table: "" })}><option value="">{draft.remote_dsn_url_has_database === true ? "Задана в DSN" : "Выберите базу"}</option>{typeof draft.remote_database === "string" && draft.remote_database && !databaseOptions.includes(draft.remote_database) && <option value={draft.remote_database}>{draft.remote_database}</option>}{databaseOptions.map((database) => <option key={database} value={database}>{database}</option>)}</select><small>Список баз появляется после вызова metadata action.</small></label>
                <label className="equipment-source-field"><span>Таблица</span><select value={typeof draft.remote_table === "string" ? draft.remote_table : ""} onChange={(event) => patchDraft({ remote_table: event.target.value })}><option value="">Выберите таблицу</option>{typeof draft.remote_table === "string" && draft.remote_table && !tableOptions.includes(draft.remote_table) && <option value={draft.remote_table}>{draft.remote_table}</option>}{tableOptions.map((table) => <option key={table} value={table}>{table}</option>)}</select><small>Поле базы и таблицы сохраняются в catalogs row после «Сохранить».</small></label>
              </>}
            </div>
          </section>

          <section className="equipment-sources__section">
            <div className="equipment-sources__section-heading"><h3>Сопоставление данных</h3><span>{catalogSourceColumns(draft).length} колонок источника</span></div>
            <MappingFields workspace={workspace} body={draft} onChange={setMapping} />
            {!mappingReady && <p className="equipment-source-notice equipment-source-notice--warning">Переиндексация заблокирована: обязательные поля `title` и `price` должны быть сопоставлены по metadata.</p>}
          </section>

          <div className="equipment-sources__footer-actions">
            <button className="equipment-sources__button equipment-sources__button--primary" disabled={busy || (paused && !isNew)} title={paused && !isNew ? "Configuration writes run index actions; pause/resume is blocked by the API contract gap" : undefined} type="submit">{busy ? "Сохранение…" : isNew ? "Создать источник" : "Сохранить настройки"}</button>
            {!isNew && selected && <button className="equipment-sources__button" disabled={busy || unavailable.index || paused || !mappingReady || sourceConfigurationDirty} onClick={() => void runOperation("index")} type="button" title={paused ? "Источник приостановлен" : unavailable.index ? "Action отсутствует в metadata" : !mappingReady ? "Column mapping не заполнен" : sourceConfigurationDirty ? "Сначала сохраните конфигурацию источника" : undefined}>{busy ? "Обработка…" : "Переиндексировать"}</button>}
            {!isNew && selected && <button className="equipment-sources__button" disabled title="API gap: отдельного pause/resume endpoint нет; catalogs PATCH запускает auto-index action">{paused ? "Снять паузу недоступно" : "Пауза недоступна"}</button>}
          </div>
          {!isNew && selected && <p className="equipment-source-notice equipment-source-notice--warning">Pause/resume заблокированы: отдельного API действия нет, а PATCH строки каталога запускает auto-index action. В metadata объявлен pause toggle, однако безопасный lifecycle endpoint отсутствует.</p>}
          {!workspace.metadata.indexAction && <p className="equipment-source-notice equipment-source-notice--warning">Действие индексации отсутствует в actions metadata. Reindex заблокирован.</p>}
          {workspace.metadata.indexAction && !workspace.metadata.indexAction.enabled && <p className="equipment-source-notice equipment-source-notice--warning">Действие индексации отключено в metadata: {workspace.metadata.indexAction.id}.</p>}
        </form>

        {selected && <section className="equipment-sources__section equipment-sources__lifecycle">
          <div className="equipment-sources__section-heading"><h3>Статус и актуальность</h3><span>{statusLabel(selected.body.status)}</span></div>
          <dl><div><dt>Последняя успешная индексация</dt><dd>{displayCatalogValue(selected.body.last_indexed_at)}</dd></div><div><dt>Строк</dt><dd>{displayCatalogValue(selected.body.row_count)}</dd></div><div><dt>Прогресс</dt><dd>{selected.body.status === "indexing" || selected.body.status === "queued" ? `${displayCatalogValue(selected.body.indexed_count)} / ${displayCatalogValue(selected.body.total_rows)}` : "Не выполняется"}</dd></div></dl>
          {selected.body.status === "error" && <p className="equipment-source-notice equipment-source-notice--error">Ошибка индексации: {sanitizeCatalogMessage(selected.body.error)}</p>}
          {(() => { const freshness = catalogFreshness(selected.body); return freshness.freshness === "stale" || freshness.freshness === "unknown" ? <p className="equipment-source-notice equipment-source-notice--warning">{freshness.label}{freshness.lastGoodLabel ? ` · последняя успешная: ${freshness.lastGoodLabel}` : ". Успешная индексация не подтверждена."}</p> : null; })()}
          {selected.body.remote_auth_failed === true && <p className="equipment-source-notice equipment-source-notice--warning">PostgreSQL сообщает об ошибке аутентификации. Проверьте DSN/учётные данные и запустите probe.</p>}
          {Boolean(selected.body.error) && selected.body.status !== "error" && <p className="equipment-source-notice equipment-source-notice--warning">{sanitizeCatalogMessage(selected.body.error)}</p>}
        </section>}
      </section>
    </div>}

  </main>;
}