import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import type {
  JsonRecord,
  ModuleActionResult,
  ModuleColumnDefinition,
  ModuleFileRef,
  ModuleManifest,
  ModulePage,
  ModuleProjectOption,
  ModuleRecord,
  ModuleRow,
  ModuleRuntimeProps,
  ModuleRuntimeRouteState,
  ModuleRuntimeScope,
  ModuleRuntimeService,
  ModuleSlotRegistry,
  ModuleViewDefinition,
} from "./contracts";
import { labelText } from "./manifest";
import { createDefaultModuleRuntimeService, ModuleRuntimeRequestError } from "./service";
import { aggregateListCount, useModuleAggregateRows, useModuleProfileRows, useModuleTable } from "./hooks";
import { actionResultNotice, collectModuleActionParams, moduleRowActionMatches, moduleRowContainsBody, moduleWriteNotice, normalizeProjectSelection, parseModuleCollectionRowAction, parseModuleColumnValue, resolvePrimaryCreateRoute, sameModuleRowBody, validateModuleActionResult, validateModuleRowBody, type ModuleActionInputField, type ModuleWriteReadbackOutcome } from "./commands";
import "./modules.css";

const defaultService = createDefaultModuleRuntimeService();
const LIST_LIMIT = 20;

function isRecord(value: unknown): value is JsonRecord { return typeof value === "object" && value !== null && !Array.isArray(value); }
function rowKey(row: ModulePage["items"][number]): string { return row.row_id; }
function displayValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function titleFrom(view: ModuleViewDefinition, locale?: string): string {
  const ui = view.ui_json;
  const scaffold = isRecord(ui.scaffold) ? ui.scaffold : undefined;
  return labelText(scaffold?.title ?? ui.title ?? view.label, locale) || view.slug;
}
function templateTitle(template: unknown, row: ModulePage["items"][number] | undefined, locale?: string): string {
  const source = labelText(template, locale);
  return source.replace(/\{([a-z][a-z0-9_]*)\}/gi, (_match, key: string) => displayValue(row?.body[key]));
}
function safeError(error: unknown): string {
  if (error instanceof ModuleRuntimeRequestError) return error.traceId ? `${error.message} (trace ${error.traceId})` : error.message;
  return error instanceof Error ? error.message : "Module operation failed.";
}

async function readScopedModuleRow(service: ModuleRuntimeService, scope: ModuleRuntimeScope, tableSlug: string, rowId: string, chatsAll: boolean): Promise<ModulePage["items"][number] | undefined> {
  for (let pageNumber = 1; pageNumber <= 100; pageNumber += 1) {
    const page = await service.listRows(scope, tableSlug, { page: pageNumber, pageSize: 200, ...(chatsAll ? { chats: "all" as const } : {}) });
    const row = page.items.find((item) => item.row_id === rowId);
    if (row) return row;
    if (pageNumber * page.page_size >= page.total || page.items.length === 0) return undefined;
  }
  throw new ModuleRuntimeRequestError("Row readback exceeds the 20,000-row client limit.", { code: "MODULE_READBACK_LIMIT", status: 413 });
}

function activeChatRequired(scope: ModuleRuntimeScope): boolean { return Boolean(scope.sessionId); }
function scopedViewVisible(view: ModuleViewDefinition, scope: ModuleRuntimeScope): boolean {
  const ui = view.ui_json;
  if (view.kind === "hub" && Array.isArray(ui.items)) return true;
  if (view.kind !== "hub" && view.kind !== "profile_hub") return true;
  const children = view.kind === "hub" ? ui.items : ui.blocks;
  return !Array.isArray(children) || children.some((child) => !isRecord(child) || !isRecord(child.scope) || child.scope.active_chat !== "required" || activeChatRequired(scope));
}

export function ModuleNavigation({ modules, manifest, scope, onOpen }: { modules: readonly ModuleRecord[]; manifest: ModuleManifest; scope: ModuleRuntimeScope; onOpen: (module: ModuleRecord, view: ModuleViewDefinition) => void }) {
  const tabs = useMemo(() => modules.flatMap((module) => manifest.tabs
    .filter((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company")
    .filter((tab) => tab.nav?.placement === "management" || tab.nav?.placement === "data")
    .map((tab) => ({ module, tab, view: manifest.views.find((view) => view.slug === tab.view_slug) }))
    .filter((entry): entry is { module: ModuleRecord; tab: ModuleManifest["tabs"][number]; view: ModuleViewDefinition } => entry.view !== undefined && entry.view.enabled !== false && scopedViewVisible(entry.view, scope))), [manifest, modules, scope]);
  if (!tabs.length) return <p className="module-runtime-empty-inline">В этом проекте нет доступных разделов модуля.</p>;
  return <nav className="module-runtime-nav" aria-label="Module sections">{tabs.sort((a, b) => (a.tab.order ?? 999) - (b.tab.order ?? 999)).map(({ module, tab, view }) => <button key={`${module.id}:${tab.id}`} type="button" onClick={() => onOpen(module, view)}><span>{labelText(tab.title) || module.name}</span><small>{tab.nav?.placement === "data" ? "Данные" : "Управление"}</small></button>)}</nav>;
}

function SectionHeader({ eyebrow, title, detail, action }: { eyebrow: string; title: string; detail?: string; action?: ReactNode }) {
  return <header className="module-runtime-section-heading"><div><p className="module-runtime-eyebrow">{eyebrow}</p><h2>{title}</h2>{detail && <p>{detail}</p>}</div>{action}</header>;
}

function InvalidMeta({ message, repair = "Попросите администратора модуля исправить meta и проверить разрешённые view/widget/action типы." }: { message: string; repair?: string }) {
  return <aside className="module-runtime-invalid" role="alert"><strong>Некорректная конфигурация модуля</strong><p>{message}</p><small>{repair}</small></aside>;
}

function renderSlot(registry: ModuleSlotRegistry | undefined, slot: string, scope: ModuleRuntimeScope, manifest: ModuleManifest, view: ModuleViewDefinition, row: ModulePage["items"][number] | undefined, onRefresh: () => void): ReactNode {
  const render = registry?.resolve(scope.moduleId, view.slug, slot);
  return render ? <div key={`${view.slug}:${slot}`} className="module-runtime-slot">{render({ scope, manifest, view, ...(row ? { row } : {}), onRefresh })}</div> : null;
}

function HubContents({ view, manifest, scope, service, contextRowId, onOpen, slots, onRefresh }: { view: ModuleViewDefinition; manifest: ModuleManifest; scope: ModuleRuntimeScope; service: ModuleRuntimeService; contextRowId?: string; onOpen: (viewSlug: string, contextRowId?: string) => void; slots?: ModuleSlotRegistry; onRefresh: () => void }) {
  const ui = view.ui_json;
  const items = view.kind === "hub" ? ui.items : ui.blocks;
  if (!Array.isArray(items)) return <InvalidMeta message={`View ${view.slug} must declare navigation items.`} />;
  const profileMode = view.kind === "profile_hub";
  const [profileEpoch, setProfileEpoch] = useState(0);
  const profileRows = useModuleProfileRows(service, scope, profileMode ? (typeof ui.profile_table === "string" ? ui.profile_table : "prompt_profiles") : undefined, profileEpoch);
  const settingsTable = typeof ui.settings_table === "string" ? ui.settings_table : "profile_settings";
  const profileField = typeof ui.profile_id_field === "string" ? ui.profile_id_field : "profile_id";
  const activeField = typeof ui.active_field === "string" ? ui.active_field : "active";
  const settings = useModuleProfileRows(service, scope, profileMode ? settingsTable : undefined, profileEpoch);
  const defaultId = profileRows.rows.find((row) => row.body.is_default === true)?.row_id;
  const activeId = settings.rows.find((row) => row.body[activeField] === true)?.body[profileField];
  const [selected, setSelected] = useState<string>();
  const profileId = contextRowId ?? selected ?? (typeof defaultId === "string" ? defaultId : typeof activeId === "string" ? activeId : profileRows.rows[0]?.row_id);
  const [error, setError] = useState<string>();
  const rows = profileRows.rows;
  const readonly = scope.readOnly;

  async function chooseProfile(id: string) {
    setError(undefined); setSelected(id);
    try { await service.setProfile(scope, id); onRefresh(); }
    catch (err) { setSelected(undefined); setError(safeError(err)); }
  }

  return <section className="module-runtime-surface">
    <SectionHeader eyebrow={profileMode ? "Profile hub" : "Module navigation"} title={titleFrom(view)} />
    {profileMode && profileRows.loading && <p role="status">Профили загружаются…</p>}
    {profileMode && (profileRows.error || settings.error) && <div className="module-runtime-error" role="alert">{profileRows.error ?? settings.error}<button type="button" className="module-runtime-button module-runtime-button--small" disabled={profileRows.loading || settings.loading} onClick={() => setProfileEpoch((value) => value + 1)}>Повторить загрузку</button></div>}
    {profileMode && !profileRows.loading && !rows.length && <div className="module-runtime-empty"><strong>Нет профилей</strong><span>Добавьте профиль в разрешённой коллекции.</span></div>}
    {profileMode && !profileId && rows.length > 0 && !readonly && <p role="status">Выберите профиль, чтобы продолжить.</p>}
    {profileMode && !contextRowId && <fieldset className="module-runtime-profile-list"><legend>Профиль</legend>{rows.map((row) => <label key={row.row_id}><input checked={profileId === row.row_id} disabled={readonly} name={`module-profile-${scope.moduleId}`} onChange={() => void chooseProfile(row.row_id)} type="radio" /><span>{displayValue(row.body.name ?? row.row_id)}</span></label>)}</fieldset>}
    {error && <p className="module-runtime-error" role="alert">{error}</p>}
    <div className="module-runtime-hub-grid">
      {items.map((item, index) => {
        if (!isRecord(item) || typeof item.title !== "string") return <InvalidMeta key={index} message={`${view.slug} item ${index + 1} is malformed.`} />;
        if (isRecord(item.scope) && item.scope.active_chat === "required" && !scope.sessionId) return null;
        const target = isRecord(item.target) ? item.target : { kind: "view", view: item.view };
        if (target.kind !== "view" || typeof target.view !== "string") return <InvalidMeta key={index} message={`${view.slug} item '${item.title}' has an unsupported target.`} />;
        const destination = manifest.views.find((candidate) => candidate.slug === target.view);
        if (!destination || destination.enabled === false) return <InvalidMeta key={index} message={`${view.slug} item '${item.title}' targets missing view '${target.view}'.`} />;
        if (!scopedViewVisible(destination, scope)) return null;
        const slotNodes = Array.isArray(destination.ui_json.slots) ? destination.ui_json.slots : [];
        return <article className="module-runtime-hub-item" key={`${view.slug}:${index}`}>
          <div><p className="module-runtime-eyebrow">{profileMode ? "Профиль" : "Раздел"}</p><h3>{item.title}</h3><p>{titleFrom(destination)}</p></div>
          {slotNodes.filter((slot): slot is JsonRecord => isRecord(slot) && slot.placement === "before_content").map((slot) => renderSlot(slots, String(slot.name), scope, manifest, destination, undefined, onRefresh))}
          <button type="button" className="module-runtime-button module-runtime-button--primary" disabled={profileMode && !profileId} onClick={() => onOpen(destination.slug, profileMode ? profileId : undefined)}>Открыть</button>
        </article>;
      })}
    </div>
  </section>;
}

function DataView({ view, manifest, scope, service, route, setRoute, slots, onRefresh }: { view: ModuleViewDefinition; manifest: ModuleManifest; scope: ModuleRuntimeScope; service: ModuleRuntimeService; route: ModuleRuntimeRouteState; setRoute: (route: ModuleRuntimeRouteState) => void; slots?: ModuleSlotRegistry; onRefresh: () => void }) {
  const tableSlug = view.table_slug ?? "";
  const table = manifest.tables.find((item) => item.slug === tableSlug);
  const ui = view.ui_json;
  const tableAvailable = Boolean(table && table.enabled !== false);
  const chatsAll = table?.scope?.chats === "all";
  const chatScopeAvailable = table?.scope?.chats !== "current" || Boolean(scope.sessionId);
  const contextFilters = useMemo(() => route.contextRowId && isRecord(ui.context_bind)
    ? Object.fromEntries(Object.entries(ui.context_bind).filter(([, source]) => source === "contextRowId").map(([field]) => [field, route.contextRowId]))
    : undefined, [route.contextRowId, ui.context_bind]);
  const tableFilters = useMemo(() => ({ ...(isRecord(ui.row_filter) ? ui.row_filter : {}), ...(contextFilters ?? {}) }), [contextFilters, ui.row_filter]);
  const activeFilters = Object.keys(tableFilters).length ? tableFilters : undefined;
  const state = useModuleTable(service, scope, tableSlug || "__invalid_table__", LIST_LIMIT, chatsAll, activeFilters, tableAvailable && chatScopeAvailable);
  const columns = Array.isArray(ui.columns) ? ui.columns.filter(isRecord) : [];
  const [aggregateEpoch, setAggregateEpoch] = useState(0);
  const aggregates = useModuleAggregateRows(view, manifest, service, scope, tableAvailable && chatScopeAvailable, aggregateEpoch);
  const filteredRows = state.page?.items ?? [];
  const [mutationError, setMutationError] = useState<string>();
  const [mutationNotice, setMutationNotice] = useState<string | undefined>(route.notice);
  const [mutationUnknown, setMutationUnknown] = useState(Boolean(route.noticeUnknown));
  const mutationUnknownRef = useRef(Boolean(route.noticeUnknown));
  useEffect(() => {
    setMutationNotice(route.notice);
    setMutationUnknown(Boolean(route.noticeUnknown));
    mutationUnknownRef.current = Boolean(route.noticeUnknown);
  }, [route.notice, route.noticeUnknown]);

  const [busy, setBusy] = useState(false);
  const mutationLock = useRef(false);

  const [contextHeader, setContextHeader] = useState<ModulePage["items"]>([]);
  const [contextHeaderError, setContextHeaderError] = useState<string>();

  useEffect(() => {
    if (route.contextRowId && isRecord(ui.context_header) && typeof ui.context_header.table_slug === "string") {
      const controller = new AbortController();
      void service.listRows(scope, ui.context_header.table_slug, { page: 1, pageSize: 200 }, { signal: controller.signal }).then((page) => {
        if (!controller.signal.aborted) setContextHeader(page.items.filter((row) => row.row_id === route.contextRowId));
      }).catch((error: unknown) => { if (!controller.signal.aborted) setContextHeaderError(safeError(error)); });
      return () => controller.abort();
    }
    setContextHeader([]); setContextHeaderError(undefined);
    return undefined;
  }, [route.contextRowId, scope, service, ui.context_header]);

  const selectedRow = route.mode === "view" ? route.row : route.row;
  const readonly = scope.readOnly || table?.storage_kind === "opensearch_virtual";
  const canCreate = !readonly && isRecord(ui.inline_add) && typeof ui.inline_add.field === "string";
  const definitionFor = (actionId: string) => manifest.actions.find((candidate) => candidate.id === actionId && candidate.enabled !== false);
  const total = state.page?.total ?? 0;
  const pages = state.page ? Math.max(1, Math.ceil(total / state.page.page_size)) : 1;

  const refreshData = useCallback(() => {
    state.refresh();
    setAggregateEpoch((value) => value + 1);
  }, [state.refresh]);

  function openRow(row: ModulePage["items"][number]) {
    const tap = isRecord(ui.row_tap) ? ui.row_tap : undefined;
    if (!tap || typeof tap.view !== "string") return;
    const destination = manifest.views.find((candidate) => candidate.slug === tap.view);
    if (!destination || destination.enabled === false) { setMutationError(`Экран ${tap.view} недоступен. Исправьте manifest.`); return; }
      const contextField = typeof tap.context_field === "string" ? tap.context_field : undefined;
    const context = contextField && typeof row.body[contextField] === "string"
      ? row.body[contextField] as string
      : isRecord(destination.ui_json.context_bind) ? row.row_id : route.contextRowId;
    const parentContext = { viewSlug: route.viewSlug, ...(route.contextRowId ? { contextRowId: route.contextRowId } : {}) };
    setRoute({ viewSlug: destination.slug, mode: destination.kind === "form" ? "edit" : "view", ...(context ? { contextRowId: context } : {}), row, returnTo: parentContext });
  }

  async function createInline(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!canCreate) return;
    const field = ui.inline_add as JsonRecord;
    const column = manifest.columns.find((candidate) => candidate.table_slug === tableSlug && candidate.name === field.field);
    if (!column) { setMutationError("The inline add field is not declared in the table schema."); return; }
    const body: JsonRecord = { ...tableFilters, [column.name]: String(new FormData(event.currentTarget).get("value") ?? "").trim() };
    for (const definition of manifest.columns.filter((candidate) => candidate.table_slug === tableSlug && candidate.default !== undefined)) body[definition.name] ??= definition.default;
    if (column.name === "project_ids" && !Array.isArray(body.project_ids)) body.project_ids = [];
    const validation = validateModuleRowBody(body, manifest.columns.filter((candidate) => candidate.table_slug === tableSlug));
    if (validation) { setMutationError(validation); return; }
    await mutate(async () => {
      mutationUnknownRef.current = true;
      setMutationUnknown(true);
      const created = await service.createRow(scope, tableSlug, body);
      let persisted: ModuleRow | undefined;
      try { persisted = await readScopedModuleRow(service, scope, tableSlug, created.row_id, chatsAll); }
      catch {
        setMutationUnknown(true);
        setMutationNotice(moduleWriteNotice("create", "unavailable"));
        return created;
      }
    if (!persisted) {
      setMutationUnknown(true);
      setMutationNotice(moduleWriteNotice("create", "missing"));
    } else if (persisted.row_id !== created.row_id || !moduleRowContainsBody(body, persisted.body)) {
      setMutationUnknown(true);
      setMutationNotice(moduleWriteNotice("create", "mismatch"));
    } else {
        setMutationUnknown(false);
        mutationUnknownRef.current = false;
        setMutationNotice(moduleWriteNotice("create", "confirmed"));
      }
      return created;
    }, event.currentTarget);
  }

  async function mutate<T>(operation: () => Promise<T>, form?: HTMLFormElement, refresh = refreshData): Promise<T | undefined> {
    if (mutationLock.current) return undefined;
    mutationLock.current = true;
    setBusy(true); setMutationError(undefined); setMutationNotice(undefined);
    try { const result = await operation(); form?.reset(); refresh(); return result; }
    catch (error) {
      if (mutationUnknownRef.current) setMutationNotice("Неизвестно, зафиксирована ли запись: POST завершился ошибкой или потерял ответ. Проверьте список перед повторной отправкой.");
      setMutationError(safeError(error));
      return undefined;
    }
    finally { mutationLock.current = false; setBusy(false); }
  }

  const title = templateTitle(ui.title_template ?? (isRecord(ui.scaffold) ? ui.scaffold.title : undefined) ?? ui.title, selectedRow, undefined) || titleFrom(view);
  if ((view.kind === "form" || view.kind === "detail") && route.mode !== "view") {
    return <ModuleFormView key={`${route.mode}:${route.row?.row_id ?? "new"}`} view={view} manifest={manifest} scope={scope} service={service} route={route} setRoute={setRoute} slots={slots} onRefresh={refreshData} />;
  }
  if (!tableAvailable) return <InvalidMeta message={`Экран ${view.slug} связан с отсутствующей или отключённой таблицей.`} />;
  if (!chatScopeAvailable) return <InvalidMeta message={`Таблица ${tableSlug} требует активный chat session.`} repair="Откройте модуль из чата проекта. Значение main не подставляется неявно интерфейсом." />;
  if (view.kind === "detail" && !selectedRow) return <InvalidMeta message="Для detail view требуется выбранная строка." repair="Вернитесь к списку и откройте запись, связанную с этим экраном." />;

  const actions = view.kind === "collection" && isRecord(ui.scaffold) && Array.isArray(ui.scaffold.actions) ? ui.scaffold.actions : [];
  const rowActions = view.kind === "collection" && Array.isArray(ui.row_actions) ? ui.row_actions : [];
  const parsedRowActions = rowActions.map(parseModuleCollectionRowAction);
  const rowActionErrors = parsedRowActions.flatMap((parsed, index) => {
    if (!parsed.ok) return [`row_actions[${index}]: ${parsed.error}`];
    const action = parsed.action;
    if (action.kind === "invoke_action" && !definitionFor(action.action)) return [`Action '${action.action}' is missing or disabled in actions meta.`];
    if (action.kind === "open_view" && !manifest.views.some((candidate) => candidate.slug === action.view && candidate.enabled !== false)) return [`row_actions[${index}] targets missing or disabled view '${action.view}'.`];
    return [];
  });
  const rowActionsMalformed = view.kind === "collection" && ui.row_actions !== undefined && !Array.isArray(ui.row_actions);
  const toolbarMalformed = view.kind === "collection" && ui.toolbar !== undefined && !Array.isArray(ui.toolbar);
  const primaryCreate = view.kind === "collection" && ui.primary_action !== undefined
    ? resolvePrimaryCreateRoute(manifest, view, route.contextRowId)
    : undefined;
  const aggregateColumns = columns.filter((column) => typeof column.source === "string");
  const unavailableAggregateTables = [...new Set(aggregateColumns.flatMap((column) => {
    const source = String(column.source);
    const tableName = source.match(/^aggregate\.([a-z][a-z0-9_]{0,63})\./)?.[1];
    return tableName && !aggregates.loading && !aggregates.error && !aggregates.rowsByTable[tableName] ? [tableName] : [];
  }))];
  const contextHeaderMeta = isRecord(ui.context_header) ? ui.context_header : undefined;
  const contextHeaderTableSlug = typeof contextHeaderMeta?.table_slug === "string" ? contextHeaderMeta.table_slug : undefined;
  const contextHeaderFields = Array.isArray(contextHeaderMeta?.fields) ? contextHeaderMeta.fields.filter(isRecord) : [];
  return <section className="module-runtime-surface" aria-busy={state.loading || busy}>
    <SectionHeader eyebrow={`${scope.moduleName} / ${tableSlug}`} title={title} detail={scope.bindKind === "global" ? "Общие данные модуля" : "Локальные данные проекта"} action={<div className="module-runtime-actions"><button type="button" className="module-runtime-button" onClick={refreshData} disabled={state.loading || aggregates.loading}>Обновить</button>{view.kind === "detail" && !readonly && selectedRow && <button type="button" className="module-runtime-button" onClick={() => setRoute({ viewSlug: view.slug, mode: "edit", row: selectedRow, ...(route.contextRowId ? { contextRowId: route.contextRowId } : {}), ...(route.returnTo ? { returnTo: route.returnTo } : {}) })}>Изменить</button>}</div>} />
    {view.kind === "detail" && selectedRow && <dl className="module-runtime-detail-facts">{manifest.columns.filter((column) => column.table_slug === tableSlug).map((column) => <div key={column.name}><dt>{labelText(column.label) || column.name}</dt><dd><CellValue value={selectedRow.body[column.name]} definition={{}} scope={scope} service={service} /></dd></div>)}</dl>}
    {contextHeaderError && <p className="module-runtime-error" role="alert">{contextHeaderError}</p>}
    {route.contextRowId && contextHeaderMeta && contextHeader[0] && <div className="module-runtime-context-header"><strong>{displayValue(contextHeader[0].body.name ?? contextHeader[0].body.title ?? route.contextRowId)}</strong>{contextHeaderFields.map((field) => <span key={String(field.column)}>{labelText(manifest.columns.find((column) => column.table_slug === contextHeaderTableSlug && column.name === field.column)?.label) || String(field.column)}: {displayValue(contextHeader[0].body[String(field.column)])}</span>)}</div>}
    {view.kind === "collection" && <div className="module-runtime-search"><label htmlFor={`search-${scope.moduleId}-${view.slug}`}>Поиск по данным</label><input id={`search-${scope.moduleId}-${view.slug}`} type="search" value={state.search} onChange={(event) => state.setSearch(event.currentTarget.value)} placeholder="Введите запрос"/><span>{total} записей</span></div>}
    {canCreate && <form className="module-runtime-inline-add" onSubmit={(event) => void createInline(event)}><label htmlFor={`add-${scope.moduleId}-${view.slug}`}>{labelText(isRecord(ui.inline_add) ? ui.inline_add.title : undefined) || "Добавить запись"}</label><input id={`add-${scope.moduleId}-${view.slug}`} name="value" placeholder={labelText(isRecord(ui.inline_add) ? ui.inline_add.hintText : undefined) || labelText(isRecord(ui.inline_add) ? ui.inline_add.title : undefined)} required/><button type="submit" disabled={busy}>{busy ? "Сохраняем…" : "Добавить"}</button></form>}
    {view.kind === "collection" && isRecord(ui.primary_action) && ui.primary_action.kind === "create_row" && primaryCreate?.route && !readonly && <button type="button" className="module-runtime-button module-runtime-button--primary" disabled={busy} onClick={() => setRoute(primaryCreate.route!)}>{labelText(ui.primary_action.label) || "Добавить"}</button>}
    {primaryCreate?.error && <InvalidMeta message={primaryCreate.error} repair="Укажите create_row и объявите enabled form/detail в primary_action.view или row_tap.view." />}
    {rowActionsMalformed && <InvalidMeta message="row_actions must be an array of typed row commands." />}
    {rowActionErrors.map((message, index) => <InvalidMeta key={`row-action-error-${index}`} message={message} />)}
    {toolbarMalformed && <InvalidMeta message="toolbar must be an array of typed commands." />}
    {mutationError && <p className="module-runtime-error" role="alert">{mutationError}</p>}
    {mutationNotice && <p className={mutationUnknown ? "module-runtime-error" : "module-runtime-success"} role={mutationUnknown ? "alert" : "status"}>{mutationNotice}</p>}
    {state.error && <div className="module-runtime-error" role="alert">{state.error}<button type="button" className="module-runtime-button module-runtime-button--small" onClick={refreshData} disabled={state.loading}>Повторить загрузку</button></div>}
    {aggregates.error && <div className="module-runtime-error" role="alert">Связанные данные не загружены: {aggregates.error}<button type="button" className="module-runtime-button module-runtime-button--small" onClick={() => setAggregateEpoch((value) => value + 1)} disabled={aggregates.loading}>Повторить связанные данные</button></div>}
    {unavailableAggregateTables.map((tableName) => <InvalidMeta key={tableName} message={`Aggregate source table '${tableName}' is unavailable.`} repair="Проверьте, что source table объявлена и разрешена текущему модулю." />)}
    {state.loading && <p className="module-runtime-loading" role="status">Загружаем страницу данных…</p>}
    {aggregates.loading && <p className="module-runtime-loading" role="status">Загружаем связанные данные…</p>}
    {!state.loading && !state.error && filteredRows.length === 0 && <div className="module-runtime-empty"><strong>{labelText(isRecord(ui.empty) ? ui.empty.title : undefined) || "Нет записей"}</strong><span>{state.search ? "Поиск не дал результатов на этой странице." : "Backend подтвердил отсутствие записей."}</span></div>}
    {view.kind === "collection" && filteredRows.length > 0 && <div className="module-runtime-table-scroll"><table className="module-runtime-table"><caption>{title}</caption><thead><tr>{columns.map((column) => <th key={String(column.field)} scope="col">{labelText(column.label) || String(column.field)}</th>)}{(!readonly || rowActions.length > 0) && <th scope="col">Действия</th>}</tr></thead><tbody>{filteredRows.map((row) => <tr key={rowKey(row)} onDoubleClick={() => openRow(row)}>{columns.map((column) => <td key={String(column.field)}>{typeof column.source === "string" ? displayValue(aggregateListCount(manifest, aggregates.rowsByTable, view, row, column)) : <CellValue value={row.body[String(column.field)]} definition={column} scope={scope} service={service} />}</td>)}{(!readonly || rowActions.length > 0) && <td className="module-runtime-row-actions">{!readonly && <><button type="button" title="Открыть запись" onClick={() => openRow(row)}>Открыть</button><button type="button" className="module-runtime-danger-link" disabled={busy} onClick={() => { if (window.confirm("Удалить эту запись?")) void mutate(() => service.deleteRow(scope, tableSlug, row.row_id), undefined, refreshData); }}>Удалить</button></>}{parsedRowActions.map((parsed, index) => {
      if (!parsed.ok) return null;
      const action = parsed.action;
      if (!moduleRowActionMatches(action, row.body)) return null;
      const label = labelText(action.label) || labelText(action.tooltip) || (action.kind === "invoke_action" ? action.action : action.view);
      if (action.kind === "open_view") {
        const destination = manifest.views.find((candidate) => candidate.slug === action.view && candidate.enabled !== false);
        if (!destination) return null;
        const rowTarget = (destination.kind === "form" || destination.kind === "detail") && destination.table_slug === tableSlug;
        const contextRowId = isRecord(destination.ui_json.context_bind) ? row.row_id : route.contextRowId;
        const returnTo = { viewSlug: route.viewSlug, ...(route.contextRowId ? { contextRowId: route.contextRowId } : {}) };
        const context = contextRowId ? { contextRowId } : {};
        // `edit` carries a required row, `create` never does, so the three modes are
        // built as explicit literals instead of a widened `mode` string.
        const target: ModuleRuntimeRouteState = rowTarget && destination.kind === "form"
          ? { viewSlug: destination.slug, mode: "edit", row, ...context, returnTo }
          : !rowTarget && (destination.kind === "form" || destination.kind === "detail")
            ? { viewSlug: destination.slug, mode: "create", ...context, returnTo }
            : { viewSlug: destination.slug, mode: "view", ...(rowTarget ? { row } : {}), ...context, returnTo };
        return <button type="button" key={`row-view-${index}`} title={labelText(action.tooltip) || label} onClick={() => setRoute(target)}>{label}</button>;
      }
      const definition = definitionFor(action.action);
      if (!definition) return null;
      return <ActionButton key={`row-action-${index}`} action={action.action} expectedKind={definition.kind} label={labelText(action.label) || labelText(definition.label) || action.action} scope={scope} service={service} rowId={row.row_id} tableSlug={tableSlug} params={isRecord(definition.params) ? definition.params : undefined} paramsForm={action.kind === "invoke_action" ? action.params_form : undefined} chatsAll={chatsAll} refresh={refreshData} />;
    })}</td>}</tr>)}</tbody></table></div>}
    {view.kind === "detail" && selectedRow && <div className="module-runtime-slot-list">{Array.isArray(ui.slots) && ui.slots.filter((slot): slot is JsonRecord => isRecord(slot) && slot.placement !== "before_content").map((slot) => renderSlot(slots, String(slot.name), scope, manifest, view, selectedRow, refreshData))}</div>}
    {view.kind === "collection" && <Pagination page={state.pageNumber} pages={pages} total={total} onPage={state.setPage} />}
    {actions.length > 0 && <div className="module-runtime-toolbar-actions">{actions.map((rawAction, index) => {
      if (!isRecord(rawAction) || typeof rawAction.action !== "string") return <InvalidMeta key={`scaffold-invalid-${index}`} message={`scaffold.actions[${index}] is malformed.`} />;
      const actionId = rawAction.action;
      const definition = definitionFor(actionId);
      return !definition ? <InvalidMeta key={actionId || "unknown"} message={`Действие '${actionId || "(empty)"}' отсутствует или отключено в actions meta.`} /> : <ActionButton action={actionId} expectedKind={definition.kind} label={labelText(rawAction.label) || labelText(definition.label) || actionId} key={actionId} scope={scope} service={service} rowId={selectedRow?.row_id} tableSlug={tableSlug} chatsAll={chatsAll} params={isRecord(definition.params) ? definition.params : undefined} refresh={refreshData}/>;
    })}</div>}
    {view.kind === "collection" && Array.isArray(ui.toolbar) && ui.toolbar.map((rawItem, index) => {
      if (!isRecord(rawItem)) return <InvalidMeta key={`toolbar-invalid-${index}`} message={`toolbar[${index}] is malformed.`} />;
      if (rawItem.kind === "open_view") {
        const destination = typeof rawItem.view === "string" ? manifest.views.find((candidate) => candidate.slug === rawItem.view && candidate.enabled !== false) : undefined;
        return destination ? <button className="module-runtime-button" key={index} type="button" onClick={() => setRoute({ viewSlug: destination.slug, mode: "view", ...(route.contextRowId ? { contextRowId: route.contextRowId } : {}) })}>{labelText(rawItem.label) || destination.slug}</button> : <InvalidMeta key={`toolbar-view-${index}`} message={`toolbar[${index}] targets a missing or disabled view.`} />;
      }
      if (rawItem.kind !== "invoke_action" || typeof rawItem.action !== "string") return <InvalidMeta key={`toolbar-action-${index}`} message={`toolbar[${index}] uses an unsupported command.`} />;
      const definition = definitionFor(rawItem.action);
      return !definition ? <InvalidMeta key={index} message={`Действие '${rawItem.action}' отсутствует или отключено в actions meta.`} /> : <ActionButton action={rawItem.action} expectedKind={definition.kind} label={labelText(rawItem.label) || labelText(definition.label) || rawItem.action} key={index} scope={scope} service={service} rowId={selectedRow?.row_id} tableSlug={tableSlug} chatsAll={chatsAll} params={isRecord(definition.params) ? definition.params : undefined} refresh={refreshData}/>;
    })}
  </section>;
}

function CellValue({ value, definition, scope, service }: { value: unknown; definition: JsonRecord; scope: ModuleRuntimeScope; service: ModuleRuntimeService }): ReactNode {
  if (definition.format === "list_count" && Array.isArray(value)) return String(value.length);
  if (definition.format === "boolean") return value === true ? "Да" : value === false ? "Нет" : "—";
  if (isRecord(value) && typeof value.asset_id === "string") return <FileRefDownload refValue={value as ModuleFileRef} scope={scope} service={service} />;
  return displayValue(value);
}

function FileRefDownload({ refValue, scope, service }: { refValue: ModuleFileRef; scope: ModuleRuntimeScope; service: ModuleRuntimeService }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  return <span className="module-runtime-file-ref"><button type="button" className="module-runtime-row-link" disabled={busy} onClick={async () => {
    setBusy(true); setError(undefined);
    try { await downloadFileToBrowser(service, scope, refValue); }
    catch (err) { setError(safeError(err)); }
    finally { setBusy(false); }
  }}>{busy ? "Скачиваем…" : typeof refValue.filename === "string" ? refValue.filename : "Скачать файл"}</button>{error && <small role="alert">{error}</small>}</span>;
}

async function downloadFileToBrowser(service: ModuleRuntimeService, scope: ModuleRuntimeScope, ref: ModuleFileRef): Promise<void> {
  const { blob, filename } = await service.downloadFile(scope, ref);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Pagination({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (page: number) => void }) {
  return <nav className="module-runtime-pagination" aria-label="Пагинация"><span>Страница {page} из {pages} · {total} записей</span><div><button type="button" onClick={() => onPage(page - 1)} disabled={page <= 1}>Назад</button><button type="button" onClick={() => onPage(page + 1)} disabled={page >= pages}>Далее</button></div></nav>;
}

function ModuleFormView({ view, manifest, scope, service, route, setRoute, slots, onRefresh }: { view: ModuleViewDefinition; manifest: ModuleManifest; scope: ModuleRuntimeScope; service: ModuleRuntimeService; route: ModuleRuntimeRouteState; setRoute: (route: ModuleRuntimeRouteState) => void; slots?: ModuleSlotRegistry; onRefresh: () => void }) {
  const tableSlug = view.table_slug!;
  const columns = useMemo(() => manifest.columns.filter((column) => column.table_slug === tableSlug), [manifest.columns, tableSlug]);
  const row = route.mode === "view" ? undefined : route.row;
  const initial = row?.body ?? (route.mode === "create" ? Object.fromEntries(columns.filter((column) => column.default !== undefined).map((column) => [column.name, column.default])) : undefined) ?? {};
  const readonly = scope.readOnly;
  const [saving, setSaving] = useState(false);
  const submitLock = useRef(false);
  const [error, setError] = useState<string>();
  const [projects, setProjects] = useState<ModuleProjectOption[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(columns.some((column) => column.name === "project_ids"));
  const [projectsError, setProjectsError] = useState<string>();
  const [projectsEpoch, setProjectsEpoch] = useState(0);
  const initialProjects = Array.isArray(initial.project_ids) ? initial.project_ids.filter((id): id is string => typeof id === "string") : [];
  const [allProjects, setAllProjects] = useState(initialProjects.length === 0);
  const [selectedProjects, setSelectedProjects] = useState<readonly string[]>(initialProjects);
  const [uploadingField, setUploadingField] = useState<string>();
  const [secretInput, setSecretInput] = useState<Record<string, string>>({});
  const [secretRefs, setSecretRefs] = useState<Record<string, JsonRecord>>({});
  const [busyAction, setBusyAction] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [noticeUnknown, setNoticeUnknown] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const isEdit = route.mode === "edit";
  const canEdit = !readonly && view.ui_json.mode !== "read";
  const viewTitle = titleFrom(view);

  useEffect(() => {
    const ids = Array.isArray(initial.project_ids) ? initial.project_ids.filter((id): id is string => typeof id === "string") : [];
    setSelectedProjects(ids);
    setAllProjects(ids.length === 0);
  }, [row?.row_id]);

  useEffect(() => {
    if (columns.some((column) => column.name === "project_ids")) {
      const controller = new AbortController();
      setProjectsLoading(true); setProjects([]); setProjectsError(undefined);
      void service.listBoundProjects(scope, { signal: controller.signal }).then((options) => {
        if (!controller.signal.aborted) {
          setProjects(options);
          setProjectsLoading(false);
        }
      }).catch((err: unknown) => {
        if (!controller.signal.aborted) { setProjectsError(safeError(err)); setProjectsLoading(false); }
      });
      return () => controller.abort();
    }
    setProjects([]); setProjectsError(undefined); setProjectsLoading(false);
    return undefined;
  }, [columns, projectsEpoch, scope, service]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!canEdit) return;
    if (submitLock.current) return;
    const form = event.currentTarget;
    const formData = new FormData(form);
    const body: JsonRecord = { ...initial };
    if (!isEdit) for (const column of columns) if (column.default !== undefined) body[column.name] ??= column.default;
    const uiFields = Array.isArray(view.ui_json.fields) ? view.ui_json.fields.filter(isRecord) : [];
    const pendingSecrets: Record<string, JsonRecord> = {};
    for (const field of uiFields) {
      const name = String(field.column);
      if (field.read_only === true) continue;
      if (field.secret === true) {
        const rawSecret = secretInput[name] ?? "";
        if (rawSecret !== "") pendingSecrets[name] = { secret: rawSecret, label: labelText(columns.find((candidate) => candidate.name === name)?.label) || name };
        else if (secretRefs[name]) body[name] = secretRefs[name];
        continue;
      }
      if (field.widget === "project_multiselect") continue;
      const column = columns.find((candidate) => candidate.name === name);
      if (!column || column.read_only) continue;
      if (column.type === "secret_ref") {
        const rawSecret = secretInput[name] ?? "";
        if (rawSecret !== "") pendingSecrets[name] = { secret: rawSecret, label: labelText(column.label) || name };
        else if (secretRefs[name]) body[name] = secretRefs[name];
        continue;
      }
      const input = form.elements.namedItem(name);
      const fieldLabel = labelText(column.label) || name;
      if (column.type === "bool") body[name] = input instanceof HTMLInputElement ? input.checked : formData.get(name) === "true";
      else if (column.type === "file_ref" && name === "file_ref" && field.widget === "file_upload") {
        const raw = String(formData.get(name) ?? "").trim();
        if (raw) {
          try { body[name] = JSON.parse(raw); }
          catch { setError(`Поле «${fieldLabel}» содержит некорректный FileRef.`); return; }
        }
      } else if (formData.has(name)) {
        const parsed = parseModuleColumnValue(column, String(formData.get(name) ?? ""));
        if (parsed.error) { setError(parsed.error); return; }
        if (parsed.skip) delete body[name];
        else body[name] = parsed.value;
      }
    }
    if (uiFields.some((field) => field.widget === "project_multiselect")) {
      if (projectsLoading) { setError("Wait for the bound project list to load before saving."); return; }
      if (projectsError) { setError("Bound projects could not be verified. Retry before saving this selection."); return; }
      const selection = normalizeProjectSelection(allProjects ? "all" : "selected", selectedProjects);
      if (selection.error) { setError(selection.error); return; }
      const available = new Set(projects.map((project) => project.project_id));
      const unavailable = (selection.project_ids ?? []).filter((projectId) => !available.has(projectId));
      if (unavailable.length) { setError(`Remove unavailable project selection(s) before saving: ${unavailable.join(", ")}.`); return; }
      body.project_ids = selection.project_ids;
    }
    if (isRecord(view.ui_json.context_bind)) for (const [key, source] of Object.entries(view.ui_json.context_bind)) if (source === "contextRowId" && route.contextRowId) body[key] = route.contextRowId;
    if (Object.keys(pendingSecrets).length > 1) { setError("Upload one secret at a time to avoid orphaning unreferenced secrets if a later upload fails."); return; }
    const validation = validateModuleRowBody(body, columns.filter((column) => !pendingSecrets[column.name]));
    if (validation) { setError(validation); return; }
    submitLock.current = true; setSaving(true); setError(undefined); setNotice(undefined); setNoticeUnknown(false);
    let submitted = false;
    try {
      for (const [name, secret] of Object.entries(pendingSecrets)) {
        const ref = await service.uploadSecret(scope, String(secret.secret), String(secret.label));
        body[name] = ref;
        setSecretRefs((current) => ({ ...current, [name]: ref }));
        setSecretInput((current) => ({ ...current, [name]: "" }));
      }
      const finalValidation = validateModuleRowBody(body, columns);
      if (finalValidation) throw new Error(finalValidation);
      const saved = isEdit && row ? await service.patchRow(scope, tableSlug, row.row_id, body) : await (async () => { submitted = true; return service.createRow(scope, tableSlug, body); })();
      if (isEdit && row && saved.row_id !== row.row_id) throw new Error("Backend saved a different module row.");
      let confirmed: ModuleRow | undefined;
      let readbackUnavailable = false;
      try { confirmed = await readScopedModuleRow(service, scope, tableSlug, saved.row_id, manifest.tables.find((candidate) => candidate.slug === tableSlug)?.scope?.chats === "all"); }
      catch { readbackUnavailable = true; /* The write response is retained, but durability cannot be confirmed. */ }
      const isConfirmed = Boolean(confirmed && confirmed.row_id === saved.row_id && sameModuleRowBody(saved.body, confirmed.body) && sameModuleRowBody(body, confirmed.body));
      // Distinguish "backend answered with a different row" from "backend could not be asked":
      // both leave the write in doubt, but only the first means a foreign body came back.
      const outcome: ModuleWriteReadbackOutcome = isConfirmed
        ? "confirmed"
        : readbackUnavailable ? "unavailable" : confirmed ? "mismatch" : "missing";
      const notice = moduleWriteNotice(isEdit ? "update" : "create", outcome);
      setNotice(notice);
      setNoticeUnknown(!isConfirmed);
      if (route.mode === "create" && route.returnTo) setRoute({ ...route.returnTo, mode: "view", notice, ...(!isConfirmed ? { noticeUnknown: true } : {}) });
      else setRoute({ viewSlug: view.slug, mode: "edit", row: isConfirmed ? confirmed! : saved, ...(route.contextRowId ? { contextRowId: route.contextRowId } : {}), ...(route.returnTo ? { returnTo: route.returnTo } : {}), notice, ...(!isConfirmed ? { noticeUnknown: true } : {}) });
      setSecretInput({});
      setSecretRefs({});
    } catch (err) {
      setError(safeError(err));
      if (submitted) {
        setNoticeUnknown(true);
        setNotice("Неизвестно, зафиксирована ли запись: POST завершился ошибкой или потерял ответ. Проверьте список перед повторной отправкой.");
      }
    } finally { submitLock.current = false; setSaving(false); }
  }

  async function runFieldAction(actionId: string) {
    if (!row) return;
    const definition = manifest.actions.find((item) => item.id === actionId && item.enabled !== false);
    if (!definition) { setError(`Действие ${actionId} не объявлено или отключено.`); return; }
    setBusyAction(actionId); setError(undefined); setNotice(undefined);
    try { const result = validateModuleActionResult(await service.invokeAction(scope, actionId, { rowId: row.row_id, ...(isRecord(definition.params) ? { params: definition.params } : {}) }), definition.kind); await handleActionResult(service, scope, result); setNotice(actionResultNotice(result)); onRefresh(); }
    catch (err) { setError(safeError(err)); }
    finally { setBusyAction(undefined); }
  }

  return <section className="module-runtime-surface">
    <SectionHeader eyebrow={`${scope.moduleName} / ${tableSlug}`} title={row ? `${viewTitle}: ${displayValue(row.body.name ?? row.body.title ?? row.row_id)}` : viewTitle} detail={scope.readOnly ? "Эта запись доступна только для чтения в текущем project bind." : undefined} action={<button type="button" className="module-runtime-button" onClick={() => setRoute(route.returnTo ? { ...route.returnTo, mode: "view" } : { viewSlug: view.slug, mode: "view", ...(route.contextRowId ? { contextRowId: route.contextRowId } : {}) })}>Назад</button>} />
    {!canEdit && row && <dl className="module-runtime-detail-facts">{columns.map((column) => <div key={column.name}><dt>{labelText(column.label) || column.name}</dt><dd><CellValue value={row.body[column.name]} definition={{}} scope={scope} service={service} /></dd></div>)}</dl>}
    {canEdit && <form className="module-runtime-form" ref={formRef} onSubmit={(event) => void submit(event)}>{(Array.isArray(view.ui_json.fields) ? view.ui_json.fields.filter(isRecord) : []).map((field) => {
      const name = String(field.column);
      const column = columns.find((candidate) => candidate.name === name);
      if (!column) return <InvalidMeta key={name} message={`Поле ${name} отсутствует в columns.`} />;
      const value = initial[name] ?? column.default ?? "";
      const fieldLabel = labelText(column.label) || name;
      const disabled = readonly || column.read_only === true || field.read_only === true || Boolean(field.secret) || column.type === "secret_ref";
      const widget = String(field.widget ?? "value");
      if (column.type === "ref") return <InvalidMeta key={name} message={`Поле ${fieldLabel} использует неподдерживаемую ссылку без picker.`} />;
      if (column.type === "enum" && (widget !== "choice" || !Array.isArray(column.enum?.values))) return <InvalidMeta key={name} message={`Поле ${fieldLabel} требует enum choice с объявленными значениями.`} />;
      if (column.type === "secret_ref" || field.secret === true) return <label className="module-runtime-field" key={name}><span>{fieldLabel}</span>{isRecord(value) && <small>Секрет задан{typeof value.label === "string" && value.label ? `: ${value.label}` : "."}</small>}<input autoComplete="new-password" disabled={readonly || saving || uploadingField === name} name={`secret_${name}`} onChange={(event) => setSecretInput((current) => ({ ...current, [name]: event.currentTarget.value }))} placeholder={value ? "Оставьте пустым, чтобы сохранить текущий секрет" : "Введите секрет"} type="password" value={secretInput[name] ?? ""}/></label>;
      if (widget === "project_multiselect") return <ProjectPicker key={name} options={projects} error={projectsError} loading={projectsLoading} selected={selectedProjects} allProjects={allProjects} onAllProjectsChange={(all) => { setAllProjects(all); if (all) setSelectedProjects([]); }} onSelectionChange={setSelectedProjects} disabled={disabled} />;
      if (widget === "file_upload") return <FileUploadField key={name} name={name} label={fieldLabel} value={value} accept={typeof field.accept === "string" ? field.accept : Array.isArray(field.accept) ? field.accept.filter((item): item is string => typeof item === "string").join(",") : undefined} disabled={disabled} uploading={uploadingField === name} scope={scope} service={service} onUpload={async (file) => {
        setUploadingField(name); setError(undefined);
        try { const uploaded = await service.uploadContent(scope, file); const form = formRef.current; if (form) { const input = form.querySelector<HTMLInputElement>(`input[name="${name}"]`); if (input) input.value = JSON.stringify(uploaded); } }
        catch (err) { setError(safeError(err)); }
        finally { setUploadingField(undefined); }
      }} />;
      const actions = isRecord(field.trailing_action) && typeof field.trailing_action.action_id === "string" ? [field.trailing_action] : [];
      return <label className="module-runtime-field" key={name}><span>{fieldLabel}{column.required ? " *" : ""}</span>{column.type === "bool" || widget === "switch" ? <span className="module-runtime-switch"><input defaultChecked={value === true} disabled={disabled} name={name} type="checkbox"/><span>Активно</span></span> : widget === "choice" && Array.isArray(column.enum?.values) ? <select defaultValue={String(value)} disabled={disabled} name={name}>{column.enum.values.map((option) => <option key={option} value={option}>{labelText(column.enum?.labels?.[option]) || option}</option>)}</select> : column.type === "json" ? <textarea defaultValue={value ? JSON.stringify(value, null, 2) : ""} disabled={disabled} name={name} rows={5}/> : <input defaultValue={String(value)} disabled={disabled} name={name} required={column.required} type={column.type === "number" ? "number" : column.type === "datetime" ? "datetime-local" : "text"}/ >}{actions.map((action) => <button key={String(action.action_id)} className="module-runtime-button module-runtime-button--small" type="button" disabled={Boolean(busyAction)} onClick={() => void runFieldAction(String(action.action_id))}>{labelText(action.tooltip) || String(action.action_id)}</button>)}</label>;
    })}
      {projectsError && <div role="alert" className="module-runtime-error">{projectsError}<button type="button" className="module-runtime-button module-runtime-button--small" disabled={projectsLoading} onClick={() => setProjectsEpoch((value) => value + 1)}>Повторить загрузку проектов</button></div>}{error && <p role="alert" className="module-runtime-error">{error}</p>}{notice && <p role={noticeUnknown ? "alert" : "status"} className={noticeUnknown ? "module-runtime-error" : "module-runtime-success"}>{notice}</p>}
      <div className="module-runtime-form-actions"><button className="module-runtime-button module-runtime-button--primary" type="submit" disabled={saving || Boolean(uploadingField) || projectsLoading || Boolean(projectsError)}>{saving ? "Сохраняем…" : isEdit ? "Сохранить" : "Создать"}</button>{row && !readonly && <button type="button" className="module-runtime-button module-runtime-button--danger" disabled={saving} onClick={async () => { if (!window.confirm("Удалить запись?")) return; setSaving(true); setError(undefined); try { await service.deleteRow(scope, tableSlug, row.row_id); setRoute(route.returnTo ? { ...route.returnTo, mode: "view" } : { viewSlug: view.slug, mode: "view" }); } catch (err) { setError(safeError(err)); } finally { setSaving(false); } }}>Удалить</button>}</div>
    </form>}
    {Array.isArray(view.ui_json.slots) && view.ui_json.slots.filter((slot): slot is JsonRecord => isRecord(slot) && slot.placement === "after_content").map((slot) => renderSlot(slots, String(slot.name), scope, manifest, view, row, onRefresh))}
  </section>;
}

function ProjectPicker({ options, selected, allProjects, disabled, error, loading, onAllProjectsChange, onSelectionChange }: { options: readonly ModuleProjectOption[]; selected: readonly string[]; allProjects: boolean; disabled: boolean; error?: string; loading: boolean; onAllProjectsChange: (all: boolean) => void; onSelectionChange: (selected: readonly string[]) => void }) {
  const availableIds = new Set(options.map((option) => option.project_id));
  const unavailable = selected.filter((projectId) => !availableIds.has(projectId));
  return <fieldset className="module-runtime-project-picker"><legend>Доступные проекты</legend>{loading && <small role="status">Загружаем привязанные проекты…</small>}{error && <small role="alert">{error}</small>}<label><input checked={allProjects} disabled={disabled || loading || Boolean(error)} onChange={(event) => onAllProjectsChange(event.currentTarget.checked)} type="checkbox"/>Все проекты модуля</label>{unavailable.map((projectId) => <label key={`unavailable:${projectId}`}><input checked={!allProjects} disabled={disabled || loading || Boolean(error)} onChange={(event) => onSelectionChange(event.currentTarget.checked ? [...selected] : selected.filter((id) => id !== projectId))} type="checkbox" value={projectId}/>{projectId} (недоступен, удалите из выбора)</label>)}{options.map((option) => <label key={option.project_id}><input checked={!allProjects && selected.includes(option.project_id)} disabled={disabled || loading || Boolean(error)} onChange={(event) => { onAllProjectsChange(false); onSelectionChange(event.currentTarget.checked ? [...new Set([...selected, option.project_id])] : selected.filter((id) => id !== option.project_id)); }} type="checkbox" value={option.project_id}/>{option.name}</label>)}{options.length === 0 && !loading && !error && <small>Нет проектов с активной привязкой к модулю.</small>}</fieldset>;
}

function FileUploadField({ name, label, value, accept, disabled, uploading, onUpload, scope, service }: { name: string; label: string; value: unknown; accept?: string; disabled: boolean; uploading: boolean; onUpload: (file: File) => Promise<void>; scope: ModuleRuntimeScope; service: ModuleRuntimeService }) {
  const ref = isRecord(value) ? value as ModuleFileRef : undefined;
  return <div className="module-runtime-field"><span>{label}</span>{ref?.filename && <><small>Текущий файл: {ref.filename} ({ref.size ?? "?"} байт)</small><FileRefDownload refValue={ref} scope={scope} service={service}/></>}<input accept={accept} disabled={disabled || uploading} type="file" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void onUpload(file); }}/><input aria-label={`${label} FileRef`} className="module-runtime-hidden-ref" defaultValue={ref ? JSON.stringify(ref) : ""} name={name} readOnly/>{uploading && <small role="status">Загружаем файл…</small>}</div>;
}

async function handleActionResult(service: ModuleRuntimeService, scope: ModuleRuntimeScope, result: ModuleActionResult): Promise<void> {
  const ref = isRecord(result.file_ref) ? result.file_ref as ModuleFileRef : undefined;
  if (!ref?.asset_id) return;
  await downloadFileToBrowser(service, scope, ref);
}

function ActionButton({ action, expectedKind, label, scope, service, refresh, rowId, tableSlug, params, paramsForm, chatsAll = false }: { action: string; expectedKind: string; label: string; scope: ModuleRuntimeScope; service: ModuleRuntimeService; refresh: () => void; rowId?: string; tableSlug?: string; params?: JsonRecord; paramsForm?: readonly ModuleActionInputField[]; chatsAll?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [resultSummary, setResultSummary] = useState<string>();
  const [paramsOpen, setParamsOpen] = useState(false);
  const [readback, setReadback] = useState<string>();
  const [readbackRow, setReadbackRow] = useState<string>();
  const [readbackUnknown, setReadbackUnknown] = useState(false);

  async function invoke(collected: JsonRecord = {}) {
    if (busy) return;
    setBusy(true); setError(undefined); setNotice(undefined); setReadback(undefined); setReadbackRow(undefined); setReadbackUnknown(false); setResultSummary(undefined);
    try {
      const result = validateModuleActionResult(await service.invokeAction(scope, action, { ...(rowId ? { rowId } : {}), params: { ...(params ?? {}), ...collected } }), expectedKind);
      setResultSummary(JSON.stringify({ ...result }));
      setNotice(actionResultNotice(result));
      if (rowId) {
        try {
          const persisted = await readScopedModuleRow(service, scope, tableSlug ?? "", rowId, chatsAll);
          if (persisted?.row_id === rowId) {
            setReadback(`Backend GET confirmed row ${persisted.row_id}.`);
            setReadbackRow(JSON.stringify(persisted));
          } else { setReadbackUnknown(true); setReadback("Action completed, but a fresh backend GET did not confirm this row."); }
        } catch {
          setReadbackUnknown(true); setReadback("Action completed, but backend GET readback is unavailable.");
        }
      }
      try { await handleActionResult(service, scope, result); }
      catch (downloadError) { setError(safeError(downloadError)); }
    } catch (err) {
      setError(safeError(err));
      setResultSummary(JSON.stringify({ error: safeError(err) }));
      setNotice("Неизвестно, завершилось ли действие: backend не вернул подтверждённый результат. Проверьте актуальную строку перед повторной отправкой.");
      if (rowId) {
        try {
          const persisted = await readScopedModuleRow(service, scope, tableSlug ?? "", rowId, chatsAll);
          if (persisted?.row_id === rowId) {
            setReadback("Свежий GET строки выполнен; результат действия остаётся неизвестным.");
            setReadbackRow(JSON.stringify(persisted));
          } else {
            setReadbackUnknown(true);
            setReadback("Результат действия неизвестен; свежий GET не подтвердил исходную строку.");
          }
        } catch {
          setReadbackUnknown(true);
          setReadback("Результат действия неизвестен; backend GET readback недоступен.");
        }
      }
    }
    finally { setBusy(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const collected = collectModuleActionParams(paramsForm ?? [], new FormData(event.currentTarget));
    if (collected.error) { setError(collected.error); return; }
    setParamsOpen(false);
    void invoke(collected.params);
  }

  return <span className="module-runtime-action">
    <button type="button" className="module-runtime-button" disabled={busy} onClick={() => paramsForm?.length ? setParamsOpen((open) => !open) : void invoke()}>{busy ? "Выполняем…" : label}</button>
    {paramsOpen && <form className="module-runtime-action-form" onSubmit={submit}>{paramsForm?.map((field) => <label className="module-runtime-field" key={field.name}><span>{labelText(field.label) || field.name}{field.required ? " *" : ""}</span>{field.type === "bool" ? <select defaultValue="false" name={field.name}><option value="false">Нет</option><option value="true">Да</option></select> : field.type === "enum" ? <select defaultValue={field.required ? "" : field.values?.[0] ?? ""} name={field.name} required={field.required}><option value="" disabled={field.required}>Выберите значение</option>{field.values?.map((value) => <option key={value} value={value}>{value}</option>)}</select> : field.type === "json" ? <textarea name={field.name} required={field.required} rows={3} /> : <input name={field.name} required={field.required} type={field.type === "number" ? "number" : field.type === "datetime" ? "datetime-local" : "text"} />}</label>)}<div className="module-runtime-form-actions"><button type="button" className="module-runtime-button" onClick={() => { setParamsOpen(false); setError(undefined); }}>Отмена</button><button type="submit" className="module-runtime-button module-runtime-button--primary" disabled={busy}>Выполнить</button></div></form>}
    {error && <small role="alert">{error}</small>}
    {notice && <small role="status">{notice}</small>}
    {resultSummary && <details className="module-runtime-action-result"><summary>Результат backend</summary><pre>{resultSummary}</pre></details>}
    {readback && <small role={readbackUnknown ? "alert" : "status"}>{readback}</small>}
    {readbackRow && <details className="module-runtime-action-result"><summary>Прочитанная строка</summary><pre>{readbackRow}</pre></details>}
  </span>;
}

export function ModuleRuntime({ projectId, moduleId, sessionId, service = defaultService, initialRuntime, initialViewSlug, onBack, onOpenProject, slots, showNavigation = true }: ModuleRuntimeProps) {
  const [data, setData] = useState<{ scope: ModuleRuntimeScope; manifest: ModuleManifest } | undefined>(initialRuntime);
  const [loading, setLoading] = useState(initialRuntime === undefined);
  const [error, setError] = useState<string>();
  const defaultViewSlug = initialRuntime?.manifest.tabs.find((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company")?.view_slug
    ?? initialRuntime?.manifest.views.find((view) => view.enabled !== false)?.slug
    ?? "";
  const [viewSlug, setViewSlug] = useState(initialViewSlug ?? defaultViewSlug);
  const [route, setRoute] = useState<ModuleRuntimeRouteState | undefined>();
  const [epoch, setEpoch] = useState(0);
  const refresh = useCallback(() => setEpoch((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    if (initialRuntime && epoch === 0) {
      setData(initialRuntime);
      setLoading(false);
      setError(undefined);
      if (initialViewSlug) setViewSlug(initialViewSlug);
      setRoute(undefined);
      return () => controller.abort();
    }
    setLoading(true);
    setError(undefined);
    setData(undefined);
    setRoute(undefined);
    void service.loadModule(projectId, moduleId, sessionId, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      setData(result);
      const initial = initialViewSlug && result.manifest.views.some((view) => view.slug === initialViewSlug) ? initialViewSlug : undefined;
      const rootTab = result.manifest.tabs.find((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company");
      setViewSlug(initial ?? rootTab?.view_slug ?? result.manifest.views.find((view) => view.enabled !== false)?.slug ?? "");
      setLoading(false);
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) { setError(safeError(err)); setLoading(false); }
    });
    return () => controller.abort();
  }, [epoch, initialRuntime, initialViewSlug, moduleId, projectId, service, sessionId]);

  const currentViewSlug = route?.viewSlug ?? viewSlug;
  const currentView = data?.manifest.views.find((view) => view.slug === currentViewSlug);
  if (loading) return <main className="module-runtime"><div className="module-runtime-loading" role="status">Загружаем модуль и проверяем его декларативный manifest…</div></main>;
  if (error || !data) return <main className="module-runtime"><div className="module-runtime-shell">{onBack && <button className="module-runtime-back" type="button" onClick={onBack}>← Назад</button>}<InvalidMeta message={error ?? "Backend did not return module data."} repair="Проверьте, что модуль привязан к проекту и его meta соответствует декларативному контракту. Privileged fallback не используется."/><button className="module-runtime-button" type="button" onClick={refresh}>Повторить</button></div></main>;
  if (!currentView || currentView.enabled === false) return <main className="module-runtime"><div className="module-runtime-shell"><InvalidMeta message={`Экран '${currentViewSlug || "(empty)"}' отсутствует или отключён.`} repair="Исправьте tabs[].view_slug или откройте объявленный view модуля."/></div></main>;
  if (data.manifest.invalid_views?.[currentView.slug]) return <main className="module-runtime"><div className="module-runtime-shell"><InvalidMeta message={data.manifest.invalid_views[currentView.slug]} repair="Это view содержит неподдерживаемый UI DSL; добавьте явный slot renderer или приведите view к allowlisted contract."/></div></main>;
  if (!scopedViewVisible(currentView, data.scope)) return <main className="module-runtime"><div className="module-runtime-shell"><InvalidMeta message="Этот раздел требует активный chat session, но scope без чата." repair="Откройте модуль из чата, для которого предназначены данные."/></div></main>;

  return <main className="module-runtime"><div className="module-runtime-shell">
    <header className="module-runtime-header"><div className="module-runtime-heading-copy"><p className="module-runtime-eyebrow">Проект / {data.scope.projectId} / {data.scope.bindKind === "global" ? "Global bind" : "Local bind"}</p><h1>{data.scope.moduleName}</h1><p className="module-runtime-muted">{data.scope.readOnly ? "Общие данные доступны только для чтения в этом проекте." : "Данные модуля проекта"}{data.scope.sessionId ? ` · Chat ${data.scope.sessionId}` : ""}</p></div><div className="module-runtime-header-actions">{onBack && <button className="module-runtime-button" type="button" onClick={onBack}>Назад</button>}{onOpenProject && <button className="module-runtime-button" type="button" onClick={() => onOpenProject(projectId)}>Проект</button>}</div></header>
    {showNavigation && <ModuleNavigation modules={[{ id: moduleId, name: data.scope.moduleName, bind_kind: data.scope.bindKind, child_may_edit: data.scope.childMayEdit }]} manifest={data.manifest} scope={data.scope} onOpen={(module, view) => { void module; setRoute(undefined); setViewSlug(view.slug); }}/>}
    <nav className="module-runtime-breadcrumb" aria-label="Навигация модуля"><button type="button" onClick={() => { setRoute(undefined); setViewSlug(data.manifest.tabs.find((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company")?.view_slug ?? currentViewSlug); }}>{data.scope.moduleName}</button>{currentViewSlug !== viewSlug && <><span>/</span><button type="button" onClick={() => setRoute(undefined)}>{titleFrom(data.manifest.views.find((view) => view.slug === viewSlug) ?? currentView)}</button></>}{route?.contextRowId && <><span>/</span><code>{route.contextRowId}</code></>}</nav>
    {currentView.kind === "hub" || currentView.kind === "profile_hub" ? <HubContents key={currentView.slug} view={currentView} manifest={data.manifest} scope={data.scope} service={service} onOpen={(slug, contextRowId) => {
      const target = data.manifest.views.find((view) => view.slug === slug);
      if (!target) return;
      const returnTo = { viewSlug: currentView.slug, ...(route?.contextRowId ? { contextRowId: route.contextRowId } : {}) };
      setRoute({ viewSlug: slug, mode: target.kind === "form" ? "create" : "view", ...(contextRowId ? { contextRowId } : {}), ...(target.kind === "form" ? {} : { returnTo }) });
    }} slots={slots} onRefresh={refresh} /> : <DataView key={`${currentView.slug}:${route?.contextRowId ?? ""}`} view={currentView} manifest={data.manifest} scope={data.scope} service={service} route={route ?? { viewSlug: currentView.slug, mode: currentView.kind === "form" ? "create" : "view" }} setRoute={(next) => setRoute(next)} slots={slots} onRefresh={refresh} />}
  </div></main>;
}

export function isModuleActionResult(value: unknown): value is ModuleActionResult { return isRecord(value) && (typeof value.kind === "string" || typeof value.ok === "boolean" || typeof value.status === "string" || isRecord(value.file_ref)); }
