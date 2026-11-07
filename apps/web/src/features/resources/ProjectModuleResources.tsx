import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ModuleRuntime } from "../modules/ModuleRuntime";
import { createModuleRuntimeService } from "../modules/service";
import { bindingLabel, editRightsLabel } from "../modules/binding";
import type { JsonRecord, ModuleListQuery, ModuleManifest, ModulePage, ModuleRecord, ModuleRow, ModuleRuntimeScope, ModuleRuntimeService, ModuleSlotRegistry } from "../modules/contracts";
import "./project-module-resources.css";
import "./resources.css";
import { useI18n } from "../../i18n/useT";
import type { Translate } from "../../i18n/catalogue";

const FIRST_PARTY_MODULES = [
  { id: "mod_management", labelKey: "resources.moduleManagement", detailKey: "resources.moduleManagementDetail" },
] as const;
/**
 * Tables this surface may page through. The merged Management module owns all five:
 * the prompt profile/path tables, their settings rows, MCP packages and project files.
 * A table missing here falls back to the service's own single request, so the allowlist
 * can only narrow the doorway's client-side project filtering, never widen access.
 */
const RESOURCE_MODULE_TABLES: Readonly<Record<string, ReadonlySet<string>>> = {
  mod_management: new Set(["prompt_profiles", "profile_settings", "prompt_paths", "mcp_packages", "files"]),
};
const PROMPT_FILES_TABLE = "prompt_paths";
const PROMPT_FILES_VIEW = "prompt_path_settings";
const PROMPT_FILES_SLOT = "resource_prompt_files";

type PromptFileEntry = JsonRecord;
type PromptFilesRead = { files: PromptFileEntry[]; error?: string };

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readPromptFiles(value: unknown, t: Translate): PromptFilesRead {
  if (value === undefined || value === null) return { files: [] };
  if (!Array.isArray(value)) return { files: [], error: t("resources.promptFilesBadList") };
  if (value.some((item) => !isRecord(item))) return { files: [], error: t("resources.promptFilesBadEntry") };
  return { files: value as PromptFileEntry[] };
}

function promptPriority(file: PromptFileEntry): number {
  const value = file.priority;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && Number.isFinite(Number(value))) return Number(value);
  return 100;
}

function promptFileKey(file: PromptFileEntry, index: number): string {
  return typeof file.id === "string" && file.id ? file.id : `${index}:${String(file.name ?? "")}`;
}

function promptBodyField(file: PromptFileEntry): string {
  for (const key of ["body", "body_md", "content"]) if (typeof file[key] === "string") return key;
  return "body_md";
}

function promptBody(file: PromptFileEntry): string {
  return String(file.body ?? file.body_md ?? file.content ?? "");
}

function safeError(error: unknown, t: Translate): string {
  return error instanceof Error ? error.message : t("resources.moduleOpFailed");
}

function sameJson(left: unknown, right: unknown): boolean {
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (isRecord(value)) return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    return value;
  };
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

async function findPromptPath(service: ModuleRuntimeService, scope: ModuleRuntimeScope, rowId: string, t: Translate): Promise<ModuleRow | undefined> {
  for (let page = 1; page <= 5; page += 1) {
    const result = await service.listRows(scope, "prompt_paths", { page, pageSize: 200 });
    const row = result.items.find((item) => item.row_id === rowId);
    if (row) return row;
    if (page * result.page_size >= result.total || result.items.length === 0) return undefined;
  }
  throw new Error(t("resources.moduleListLimitStale"));
}

function rowAppliesToProject(row: ModuleRow, projectId: string): boolean {
  const ids = row.body.project_ids;
  if (ids === undefined || ids === null) return true;
  if (!Array.isArray(ids) || ids.some((id) => typeof id !== "string")) return false;
  return ids.length === 0 || ids.includes(projectId);
}

/**
 * Replace the backend's undeclared `prompt_files_editor` widget with the allowlisted
 * slot that renders the nested editor.
 *
 * The decision is made from the manifest alone — the `prompt_paths.files_json` column
 * and the `prompt_path_settings` detail view that declares the widget — never from the
 * module id. Any module that ships that table/view pair gets the editor; a module that
 * does not is returned untouched, so a missing column or view never produces a slot.
 */
export function withResourceModuleViews(loaded: { scope: ModuleRuntimeScope; manifest: ModuleManifest }): { scope: ModuleRuntimeScope; manifest: ModuleManifest } {
  const hasFilesJson = loaded.manifest.columns.some((column) => column.table_slug === PROMPT_FILES_TABLE && column.name === "files_json" && column.type === "json");
  const view = loaded.manifest.views.find((candidate) => candidate.slug === PROMPT_FILES_VIEW && candidate.kind === "detail" && candidate.table_slug === PROMPT_FILES_TABLE);
  const fields = Array.isArray(view?.ui_json.fields) ? view.ui_json.fields : [];
  const nestedEditor = fields.find((field) => isRecord(field) && field.column === "files_json" && field.widget === "prompt_files_editor");
  if (!hasFilesJson || !view || !nestedEditor) return loaded;

  const reasons = loaded.manifest.invalid_views?.[PROMPT_FILES_VIEW]?.split("; ") ?? [];
  const nestedEditorOnlyIssue = reasons.length > 0 && reasons.every((item) => {
    const match = item.match(/^view prompt_path_settings fields\[(\d+)\] uses unsupported widget 'prompt_files_editor'$/);
    return Boolean(match && Number(match[1]) === fields.indexOf(nestedEditor));
  });
  if (reasons.length > 0 && !nestedEditorOnlyIssue) return loaded;
  const views = loaded.manifest.views.map((candidate) => candidate.slug !== PROMPT_FILES_VIEW ? candidate : {
    ...candidate,
    ui_json: {
      ...candidate.ui_json,
      fields: fields.filter((field) => !(isRecord(field) && field.column === "files_json" && field.widget === "prompt_files_editor")),
      slots: [
        ...(Array.isArray(candidate.ui_json.slots) ? candidate.ui_json.slots.filter((slot) => !isRecord(slot) || slot.name !== PROMPT_FILES_SLOT) : []),
        { name: PROMPT_FILES_SLOT, placement: "after_content" },
      ],
    },
  });
  const invalidViews = { ...(loaded.manifest.invalid_views ?? {}) };
  delete invalidViews[PROMPT_FILES_VIEW];
  const visibleColumns = loaded.manifest.columns.filter((column) => !(column.table_slug === PROMPT_FILES_TABLE && column.name === "files_json"));
  const manifest: ModuleManifest = { ...loaded.manifest, columns: visibleColumns, views, invalid_views: invalidViews };
  return { ...loaded, manifest };
}

/** The doorway's own read path: adapt the manifest, then page the merged tables client-side. */
export function resourceModuleService(service: ModuleRuntimeService, t: Translate): ModuleRuntimeService {
  return {
    ...service,
    async loadModule(projectId, moduleId, sessionId, options) {
      return withResourceModuleViews(await service.loadModule(projectId, moduleId, sessionId, options));
    },
    async listRows(scope: ModuleRuntimeScope, tableSlug: string, query: ModuleListQuery = {}, requestOptions) {
      if (!RESOURCE_MODULE_TABLES[scope.moduleId]?.has(tableSlug)) return service.listRows(scope, tableSlug, query, requestOptions);
      const pageSize = 200;
      const collected: ModuleRow[] = [];
      let total = 0;
      for (let page = 1; page <= 5; page += 1) {
        const result = await service.listRows(scope, tableSlug, { page, pageSize, ...(query.chats ? { chats: query.chats } : {}) }, requestOptions);
        collected.push(...result.items);
        total = result.total;
        if (collected.length >= total || result.items.length === 0) break;
      }
      if (collected.length < total) throw new Error(t("resources.moduleListLimit"));
      const visible = collected.filter((row) => rowAppliesToProject(row, scope.projectId)
        && Object.entries(query.filters ?? {}).every(([field, expected]) => row.body[field] === expected));
      const search = (query.search ?? "").trim().toLocaleLowerCase();
      const matching = search ? visible.filter((row) => JSON.stringify(row.body).toLocaleLowerCase().includes(search)) : visible;
      const page = Math.max(1, query.page ?? 1);
      const size = Math.max(1, Math.min(query.pageSize ?? 50, 200));
      return { items: matching.slice((page - 1) * size, page * size), total: matching.length, page, page_size: size } satisfies ModulePage;
    },
  };
}

function PromptFilesEditor({ service, scope, row, onRefresh }: { service: ModuleRuntimeService; scope: ModuleRuntimeScope; row: ModuleRow; onRefresh: () => void }) {
  const { t } = useI18n();
  const initial = readPromptFiles(row.body.files_json, t);
  const [files, setFiles] = useState(initial.files);
  const [baselineFiles, setBaselineFiles] = useState(initial.files);
  const [selectedKey, setSelectedKey] = useState(() => initial.files.length ? promptFileKey(initial.files[0], 0) : undefined);
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("100");
  const [body, setBody] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initial.error);
  const [notice, setNotice] = useState<string>();
  const readOnly = scope.readOnly;
  const selected = files.find((file, index) => promptFileKey(file, index) === selectedKey);
  const selectedIndex = selected ? files.indexOf(selected) : -1;
  const ordered = files.map((file, index) => ({ file, index })).sort((left, right) => promptPriority(left.file) - promptPriority(right.file) || left.index - right.index);

  useEffect(() => {
    const loaded = readPromptFiles(row.body.files_json, t);
    setFiles(loaded.files);
    setBaselineFiles(loaded.files);
    setSelectedKey(loaded.files.length ? promptFileKey(loaded.files[0], 0) : undefined);
    setError(loaded.error);
    setNotice(undefined);
  }, [row.row_id, row.body.files_json]);

  useEffect(() => {
    if (!selected) {
      setName(""); setPriority("100"); setBody("");
      return;
    }
    setName(String(selected.name ?? ""));
    setPriority(String(promptPriority(selected)));
    setBody(promptBody(selected));
  }, [selectedKey, files]);

  async function persist(nextFiles: PromptFileEntry[], nextSelectedKey?: string): Promise<void> {
    if (readOnly || error) return;
    setBusy(true); setError(undefined); setNotice(undefined);
    try {
      const current = await findPromptPath(service, scope, row.row_id, t);
      if (!current) throw new Error(t("resources.promptFilesGone"));
      const currentFiles = readPromptFiles(current.body.files_json, t);
      if (currentFiles.error || !sameJson(currentFiles.files, baselineFiles)) {
        throw new Error(t("resources.promptFilesChanged"));
      }
      const saved = await service.patchRow(scope, "prompt_paths", row.row_id, { files_json: nextFiles });
      const returned = readPromptFiles(saved.body.files_json, t);
      if (returned.error) throw new Error(t("resources.promptFilesNotConfirmed"));
      setFiles(returned.files);
      setBaselineFiles(returned.files);
      setSelectedKey(nextSelectedKey && returned.files.some((file, index) => promptFileKey(file, index) === nextSelectedKey)
        ? nextSelectedKey
        : returned.files.length ? promptFileKey(returned.files[0], 0) : undefined);
      setNotice(t("resources.promptFilesSaved"));
      onRefresh();
    } catch (cause) {
      setError(safeError(cause, t));
    } finally {
      setBusy(false);
    }
  }

  async function addPrompt(event: FormEvent) {
    event.preventDefault();
    const title = newName.trim();
    if (!title || files.some((file) => file.name === title)) return;
    const maxPriority = files.reduce((highest, file) => Math.max(highest, promptPriority(file)), 99);
    const unique = typeof globalThis.crypto?.randomUUID === "function" ? globalThis.crypto.randomUUID() : `${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const id = `prompt_${unique}`;
    const next = [...files, { id, name: title, priority: maxPriority + 1, body_md: "" }];
    await persist(next, id);
    setNewName("");
  }

  async function savePrompt(event: FormEvent) {
    event.preventDefault();
    if (!selected || selectedIndex < 0 || !name.trim()) return;
    const numericPriority = Number(priority);
    if (!Number.isSafeInteger(numericPriority) || numericPriority < 0) {
      setError(t("resources.promptFilesPriorityInvalid"));
      return;
    }
    const updated = { ...selected, name: name.trim(), priority: numericPriority, [promptBodyField(selected)]: body };
    const next = files.map((file, index) => index === selectedIndex ? updated : file);
    await persist(next, promptFileKey(updated, selectedIndex));
  }

  async function deletePrompt(file: PromptFileEntry, index: number) {
    if (readOnly || busy || error || !window.confirm(t("resources.promptFilesConfirmRemove", { name: String(file.name ?? t("resources.promptFilesUnnamed")) }))) return;
    const next = files.filter((_entry, candidateIndex) => candidateIndex !== index);
    await persist(next, next.length ? promptFileKey(next[0], 0) : undefined);
  }

  return <section className="project-prompt-files" aria-label={t("resources.promptFilesTitle")} aria-busy={busy}>
    <header><div><p>{t("resources.promptFilesTitle")}</p><h3>{String(row.body.name ?? t("resources.promptFilesUnnamed"))}</h3><span>{String(row.body.path ?? "") || t("resources.promptFilesRoot")}</span></div><span>{readOnly ? t("resources.promptFilesReadOnly") : t("resources.promptFilesBound")}</span></header>
    {readOnly && <p className="project-prompt-files__readonly" role="status">{t("resources.promptFilesReadOnlyNote")}</p>}
    {error && <p className="project-prompt-files__error" role="alert">{error}</p>}
    {error && !readOnly && <button type="button" className="project-prompt-files__reload" onClick={onRefresh}>{t("resources.promptFilesReload")}</button>}
    <form className="project-prompt-files__add" onSubmit={(event) => void addPrompt(event)}>
      <label htmlFor={`prompt-add-${row.row_id}`}>{t("resources.promptFilesAdd")}</label>
      <input id={`prompt-add-${row.row_id}`} value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="Instructions.md" disabled={readOnly || busy || Boolean(error)} required />
      <button type="submit" disabled={readOnly || busy || Boolean(error) || !newName.trim()}>{t("resources.promptFilesAddButton")}</button>
    </form>
    <div className="project-prompt-files__layout">
      <nav aria-label={t("resources.promptFilesNavLabel")}>
        {ordered.map(({ file, index }) => <div className="project-prompt-files__item" key={promptFileKey(file, index)}>
          <button type="button" aria-pressed={selectedKey === promptFileKey(file, index)} onClick={() => setSelectedKey(promptFileKey(file, index))}><strong>{String(file.name ?? t("resources.promptFilesUnnamed"))}</strong><small>{t("resources.promptFilesPriority", { value: promptPriority(file) })}</small></button>
          <button type="button" aria-label={t("resources.promptFilesRemoveAria", { name: String(file.name ?? t("resources.promptFilesTitle")) })} disabled={readOnly || busy || Boolean(error)} onClick={() => void deletePrompt(file, index)}>{t("resources.promptFilesRemove")}</button>
        </div>)}
        {files.length === 0 && !error && <p className="project-prompt-files__empty">{t("resources.promptFilesEmpty")}</p>}
      </nav>
      {selected && <form className="project-prompt-files__editor" onSubmit={(event) => void savePrompt(event)}>
        <label>{t("resources.promptFilesName")}<input value={name} onChange={(event) => setName(event.target.value)} disabled={readOnly || busy || Boolean(error)} required /></label>
        <label>{t("resources.promptFilesPriorityLabel")}<input type="number" min="0" step="1" value={priority} onChange={(event) => setPriority(event.target.value)} disabled={readOnly || busy || Boolean(error)} required /></label>
        <label>{t("resources.promptFilesMarkdown")}<textarea value={body} onChange={(event) => setBody(event.target.value)} rows={14} disabled={readOnly || busy || Boolean(error)} /></label>
        <button type="submit" disabled={readOnly || busy || Boolean(error) || !name.trim()}>{busy ? t("common.saving") : t("resources.promptFilesSave")}</button>
      </form>}
    </div>
    {notice && <p className="project-prompt-files__notice" role="status">{notice}</p>}
  </section>;
}

/**
 * The prompt-files slot is keyed by the `prompt_path_settings` detail view over the
 * `prompt_paths` table, not by a module id: the merged Management module renders it,
 * and any unrelated table that happens to reuse the slot name is refused here.
 */
export function createResourceModuleSlots(service: ModuleRuntimeService): ModuleSlotRegistry {
  return {
    resolve(_moduleId, viewSlug, slot) {
      if (viewSlug !== PROMPT_FILES_VIEW || slot !== PROMPT_FILES_SLOT) return undefined;
      return ({ view, row, scope, onRefresh }) => view.kind === "detail" && view.table_slug === PROMPT_FILES_TABLE && row
        ? <PromptFilesEditor service={service} scope={scope} row={row} onRefresh={onRefresh} />
        : null;
    },
  };
}

function moduleLabel(t: Translate, moduleId: string): string {
  const definition = FIRST_PARTY_MODULES.find((item) => item.id === moduleId);
  return definition ? t(definition.labelKey) : moduleId;
}

export function ProjectModuleResources({ projectId, accessToken }: { projectId: string; accessToken?: string }) {
  const { t } = useI18n();
  const baseService = useMemo(() => createModuleRuntimeService({ getAccessToken: () => accessToken }), [accessToken]);
  const service = useMemo(() => resourceModuleService(baseService, t), [baseService, t]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [modules, setModules] = useState<readonly ModuleRecord[]>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(undefined); setModules(undefined);
    void service.listProjectModules(projectId, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setModules(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(safeError(cause, t));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt, projectId, service]);

  const boundModules = useMemo(() => FIRST_PARTY_MODULES.flatMap((definition) => {
    const module = modules?.find((candidate) => candidate.id === definition.id);
    return module ? [{ definition, module }] : [];
  }), [modules]);
  const requestedModuleId = searchParams.get("module") ?? "";
  const active = boundModules.find(({ module }) => module.id === requestedModuleId);

  const slots: ModuleSlotRegistry = useMemo(() => createResourceModuleSlots(service), [service]);

  const openModule = useCallback((moduleId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("module", moduleId);
    setSearchParams(next);
  }, [searchParams, setSearchParams]);

  function closeModule() {
    const next = new URLSearchParams(searchParams);
    next.delete("module");
    setSearchParams(next);
  }

  return <section className="project-module-resources" aria-labelledby="project-module-resources-title">
    <header className="project-module-resources__heading" title={projectId}><div><p className="project-module-resources__eyebrow">{t("resources.modulesEyebrow")}</p><h2 id="project-module-resources-title">{t("resources.modulesTitle")}</h2><p>{t("resources.modulesDetail")}</p></div>{active && <button type="button" onClick={closeModule}>{t("resources.modulesAll")}</button>}</header>
    {loading && <p className="project-module-resources__feedback" role="status">{t("resources.modulesChecking")}</p>}
    {!loading && error && <div className="project-module-resources__feedback project-module-resources__feedback--error" role="alert"><strong>{t("resources.modulesUnavailable")}</strong><span>{error}</span><button type="button" onClick={() => setAttempt((value) => value + 1)}>{t("common.retry")}</button></div>}
    {!loading && !error && !active && <>
      {boundModules.length === 0 && <p className="project-module-resources__feedback">{t("resources.modulesEmpty")}</p>}
      <div className="project-module-resources__grid">
        {FIRST_PARTY_MODULES.map((definition) => {
          const module = modules?.find((candidate) => candidate.id === definition.id);
          return <article key={definition.id} className="project-module-resources__card" data-resource-module={definition.id}>
            <div><p className="project-module-resources__eyebrow">{module ? t("resources.moduleBound") : t("resources.moduleNotBound")}</p><h3>{t(definition.labelKey)}</h3><p>{t(definition.detailKey)}</p></div>
            {module ? <><span className="project-module-resources__binding">{t("resources.moduleBinding", { mode: bindingLabel(t, module.bind_kind, true), rights: editRightsLabel(t, module.child_may_edit) })}</span><button type="button" onClick={() => openModule(module.id)}>{t("resources.moduleOpen", { name: t(definition.labelKey) })}</button></> : <Link to="/company/modules">{t("resources.moduleOpenBindings")}</Link>}
          </article>;
        })}
      </div>
    </>}
    {!loading && !error && active && <div className="project-module-resources__runtime">
      <div className="project-module-resources__active"><span>{t("resources.moduleActive", { name: moduleLabel(t, active.module.id), mode: bindingLabel(t, active.module.bind_kind, true) })}</span><span title={projectId}>{active.module.child_may_edit ? t("resources.moduleProjectEditable") : t("resources.moduleProjectReadOnly")}</span></div>
      <ModuleRuntime key={`${projectId}:${active.module.id}`} projectId={projectId} moduleId={active.module.id} service={service} slots={slots} showNavigation onBack={closeModule} />
    </div>}
  </section>;
}
