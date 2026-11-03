import type {
  JsonRecord,
  ModuleActionDefinition,
  ModuleColumnDefinition,
  ModuleManifest,
  ModuleTabDefinition,
  ModuleTableDefinition,
  ModuleViewDefinition,
  ModuleViewKind,
} from "./contracts";
import { parseModuleCollectionRowAction } from "./commands";

const SLUG = /^[a-z][a-z0-9_]{0,63}$/;
const COLUMN_TYPES = new Set(["text", "number", "bool", "datetime", "json", "enum", "ref", "file_ref", "secret_ref"]);
const VIEW_KINDS = new Set<ModuleViewKind>(["hub", "profile_hub", "collection", "form", "detail"]);
const NAV_PLACEMENTS = new Set(["rail", "management", "data", "none"]);
const PROJECT_SCOPES = new Set(["all", "bound", "none"]);
const STORAGE_KINDS = new Set(["json_document", "physical", "opensearch_virtual"]);
const FORM_WIDGETS = new Set(["value", "switch", "choice", "file_upload", "project_multiselect"]);
const COLLECTION_FORMATS = new Set(["text", "list_count", "boolean", "number", "datetime", "bool_yes_no"]);
const VIEW_KEYS: Record<ModuleViewKind, ReadonlySet<string>> = {
  hub: new Set(["version", "kind", "title", "scaffold", "items", "slots"]),
  profile_hub: new Set(["version", "kind", "title", "scaffold", "profile_table", "settings_table", "profile_id_field", "active_field", "blocks", "slots"]),
  collection: new Set(["version", "kind", "title", "title_template", "scaffold", "title_field", "subtitle_fields", "columns", "row_tap", "primary_action", "inline_add", "empty", "context_bind", "context_header", "row_filter", "profile_table", "settings_table", "toolbar", "row_actions", "slots"]),
  form: new Set(["version", "kind", "title", "mode", "fields", "slots"]),
  detail: new Set(["version", "kind", "title", "mode", "title_template", "fields", "slots"]),
};
const SLOT_PLACEMENTS = new Set(["before_content", "after_content"]);
const MODULE_ACTION_KINDS = new Set([
  "data.create_row", "data.delete_row", "data.select_row",
  "content.index_tabular", "content.index_opensearch", "content.probe_remote_sql",
  "content.list_remote_sql_tables", "content.list_remote_sql_databases",
  "documents.convert", "documents.fill_template",
  "equipment.budget_sync", "equipment.match_to_line", "equipment.master_price",
  "equipment.pipeline", "equipment.offers_refresh", "equipment.ready_builds",
  "equipment.attach_ready_build", "equipment.tender_take_to_work", "equipment.tender_attach",
  "equipment.mail_send", "equipment.master_price_mail", "equipment.procurement_apply",
  "equipment.budget_export", "equipment.kp_export", "equipment.spec_export",
]);

export interface ManifestValidation {
  readonly ok: boolean;
  readonly manifest?: ModuleManifest;
  readonly error?: string;
  readonly repairHint?: string;
}

function record(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list<T>(value: unknown, label: string, errors: string[]): T[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.some((item) => !record(item))) {
    errors.push(`${label} must be an array of objects`);
    return [];
  }
  return value as T[];
}

function checkMetaLabel(value: unknown): boolean {
  if (typeof value === "string") return true;
  return record(value) && Object.values(value).every((entry) => typeof entry === "string");
}

function validateUi(view: ModuleViewDefinition, viewSlugs: Set<string>, tableSlugs: Set<string>, columns: readonly ModuleColumnDefinition[], columnsByTable: Map<string, Set<string>>, errors: string[]): void {
  const ui = view.ui_json;
  const label = `view ${view.slug}`;
  if (!record(ui)) { errors.push(`${label} ui_json must be an object`); return; }
  if (VIEW_KINDS.has(view.kind)) {
    for (const key of Object.keys(ui)) if (!VIEW_KEYS[view.kind].has(key)) errors.push(`${label} uses unsupported UI property '${key}'`);
  }
  if (ui.version !== 1) errors.push(`${label} ui_json.version must be 1`);
  if (ui.kind !== view.kind) errors.push(`${label} kind must match ui_json.kind`);
  if (ui.slots !== undefined) {
    if (!Array.isArray(ui.slots)) errors.push(`${label} slots must be an array`);
    else ui.slots.forEach((slot, index) => {
      if (!record(slot) || typeof slot.name !== "string" || !SLUG.test(slot.name) || (slot.placement !== undefined && !SLOT_PLACEMENTS.has(String(slot.placement)))) errors.push(`${label} slots[${index}] has an invalid slot name or placement`);
    });
  }

  if (view.kind === "hub") {
    if (!Array.isArray(ui.items)) { errors.push(`${label} hub requires an items array`); return; }
    ui.items.forEach((item, index) => {
      if (!record(item) || typeof item.title !== "string" || !record(item.target) || item.target.kind !== "view" || typeof item.target.view !== "string" || !viewSlugs.has(item.target.view)) {
        errors.push(`${label} items[${index}] must target a declared view`);
      } else if (record(item.scope) && item.scope.active_chat !== undefined && !["optional", "required"].includes(String(item.scope.active_chat))) {
        errors.push(`${label} items[${index}] has unsupported active_chat scope`);
      }
    });
    return;
  }

  if (view.kind === "profile_hub") {
    for (const key of ["profile_table", "settings_table"]) {
      if (ui[key] !== undefined && (typeof ui[key] !== "string" || !tableSlugs.has(ui[key] as string))) errors.push(`${label} ${key} must reference a declared table`);
    }
    if (!Array.isArray(ui.blocks)) errors.push(`${label} profile_hub requires a blocks array`);
    else ui.blocks.forEach((item, index) => {
      if (!record(item) || typeof item.title !== "string" || !record(item.target) || item.target.kind !== "view" || typeof item.target.view !== "string" || !viewSlugs.has(item.target.view)) errors.push(`${label} blocks[${index}] must target a declared view`);
    });
    return;
  }

  if (!view.table_slug || !tableSlugs.has(view.table_slug)) { errors.push(`${label} requires a declared table_slug`); return; }
  const knownColumns = columnsByTable.get(view.table_slug) ?? new Set<string>();
  if (view.kind === "collection") {
    if (ui.columns !== undefined && (!Array.isArray(ui.columns) || ui.columns.some((item) => !record(item) || typeof item.field !== "string" || (knownColumns.size > 0 && !knownColumns.has(item.field) && !(typeof item.source === "string" && /^aggregate\.[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}$/.test(item.source))) || (item.format !== undefined && !COLLECTION_FORMATS.has(String(item.format))) || Object.keys(item).some((key) => !["field", "label", "format", "source", "join_field", "foreign_key", "max_lines", "max_width", "width", "align", "variant", "label_field", "tone_field"].includes(key))))) errors.push(`${label} columns contain an unsupported field or format`);
    if (Array.isArray(ui.columns)) ui.columns.forEach((column, index) => {
      if (!record(column) || typeof column.source !== "string" || !column.source.startsWith("aggregate.")) return;
      const [, table, field] = column.source.split(".");
      if (!tableSlugs.has(table) || !columnsByTable.get(table)?.has(field)) errors.push(`${label} columns[${index}] aggregate source references an unknown table/field`);
      const candidateKeys = columns.filter((item) => item.table_slug === table && item.name.endsWith("_id")).map((item) => item.name);
      const expectedForeignKey = column.foreign_key ?? (candidateKeys.length === 1 ? candidateKeys[0] : undefined);
      if (column.join_field !== undefined && (typeof column.join_field !== "string" || (column.join_field !== "row_id" && !knownColumns.has(column.join_field)))) errors.push(`${label} columns[${index}] aggregate join_field is undeclared`);
      if (expectedForeignKey === undefined || typeof expectedForeignKey !== "string" || !columnsByTable.get(table)?.has(expectedForeignKey)) errors.push(`${label} columns[${index}] aggregate source requires an unambiguous declared foreign key`);
    });
    for (const field of [ui.title_field, ...(Array.isArray(ui.subtitle_fields) ? ui.subtitle_fields : [])]) if (field !== undefined && (typeof field !== "string" || (knownColumns.size > 0 && !knownColumns.has(field)))) errors.push(`${label} references an unknown title/subtitle field`);
    if (ui.row_tap !== undefined) {
      const tap = ui.row_tap;
      if (!record(tap) || !["open_form", "open_view"].includes(String(tap.kind)) || typeof tap.view !== "string" || !viewSlugs.has(tap.view)) errors.push(`${label} row_tap must open a declared form or view`);
    }
    if (ui.inline_add !== undefined && (!record(ui.inline_add) || typeof ui.inline_add.field !== "string" || (knownColumns.size > 0 && !knownColumns.has(ui.inline_add.field)) || Object.keys(ui.inline_add).some((key) => !["field", "title", "label", "hintText"].includes(key)))) errors.push(`${label} inline_add must reference a declared field and supported label keys`);
    if (record(ui.inline_add) && typeof ui.inline_add.field === "string" && columns.find((column) => column.table_slug === view.table_slug && column.name === ui.inline_add!.field)?.type !== "text") errors.push(`${label} inline_add requires a text column`);
    if (ui.primary_action !== undefined && (!record(ui.primary_action) || ui.primary_action.kind !== "create_row" || (ui.primary_action.view !== undefined && (typeof ui.primary_action.view !== "string" || !viewSlugs.has(ui.primary_action.view))) || Object.keys(ui.primary_action).some((key) => !["kind", "view", "label"].includes(key)))) errors.push(`${label} primary_action must be an allowlisted create_row command`);
    if (ui.primary_action !== undefined && ui.inline_add !== undefined) errors.push(`${label} must choose either primary_action or inline_add, not both`);
    if (ui.row_filter !== undefined && !record(ui.row_filter)) errors.push(`${label} row_filter must be an object`);
    if (typeof ui.title_template === "string" || record(ui.title_template)) {
      const template = typeof ui.title_template === "string" ? ui.title_template : Object.values(ui.title_template).filter((item): item is string => typeof item === "string").join(" ");
      for (const match of template.matchAll(/\{([a-z][a-z0-9_]*)\}/gi)) if (knownColumns.size > 0 && !knownColumns.has(match[1])) errors.push(`${label} title_template references unknown field '${match[1]}'`);
    }
    if (ui.context_bind !== undefined && (!record(ui.context_bind) || Object.values(ui.context_bind).some((value) => value !== "contextRowId"))) errors.push(`${label} context_bind values must be contextRowId`);
    if (ui.context_header !== undefined) {
      if (!record(ui.context_header) || typeof ui.context_header.table_slug !== "string" || !tableSlugs.has(ui.context_header.table_slug) || !Array.isArray(ui.context_header.fields)) errors.push(`${label} context_header must identify a table and fields`);
      else (ui.context_header.fields as unknown[]).forEach((item, index) => {
        if (!record(item) || typeof item.column !== "string" || (columnsByTable.get(ui.context_header!.table_slug as string)?.has(item.column) !== true)) errors.push(`${label} context_header.fields[${index}] references an unknown column`);
      });
    }
    for (const key of ["toolbar", "row_actions"]) if (ui[key] !== undefined && !Array.isArray(ui[key])) errors.push(`${label} ${key} must be an array`);
    for (const key of ["toolbar", "row_actions"]) if (Array.isArray(ui[key])) (ui[key] as unknown[]).forEach((item, index) => {
      if (!record(item)) { errors.push(`${label} ${key}[${index}] must be an object command`); return; }
      const inferredKind = item.kind ?? (typeof item.action === "string" ? "invoke_action" : undefined);
      const allowedKeys = key === "row_actions" ? ["kind", "action", "view", "label", "tooltip", "icon", "when", "params_form"] : ["kind", "action", "view", "label"];
      if (Object.keys(item).some((field) => !allowedKeys.includes(field))) errors.push(`${label} ${key}[${index}] has an unsupported property`);
      if (key === "row_actions" && item.params_form !== undefined) {
        const parsed = parseModuleCollectionRowAction(item);
        if (!parsed.ok) errors.push(`${label} row_actions[${index}]: ${parsed.error}`);
      }
      if (inferredKind === "invoke_action" && (typeof item.action !== "string" || !SLUG.test(item.action))) errors.push(`${label} ${key}[${index}] action id is invalid`);
      else if (inferredKind === "open_view" && (typeof item.view !== "string" || !viewSlugs.has(item.view))) errors.push(`${label} ${key}[${index}] targets an unknown view`);
      else if (inferredKind !== "invoke_action" && inferredKind !== "open_view") errors.push(`${label} ${key}[${index}] uses an unsupported command`);
      if (inferredKind === "invoke_action" && item.view !== undefined) errors.push(`${label} ${key}[${index}] invoke_action cannot also declare view`);
      if (inferredKind === "open_view" && item.action !== undefined) errors.push(`${label} ${key}[${index}] open_view cannot also declare action`);
      if (key === "toolbar" && item.kind === "open_view" && item.action !== undefined) errors.push(`${label} toolbar[${index}] open_view cannot declare action`);
      if (key === "row_actions" && item.when !== undefined) {
        const condition = item.when;
        if (!record(condition) || typeof condition.field !== "string" || !condition.field || Object.keys(condition).some((field) => !["field", "eq", "in"].includes(field)) || (("eq" in condition) === ("in" in condition)) || ("in" in condition && (!Array.isArray(condition.in) || condition.in.length === 0))) errors.push(`${label} row_actions[${index}] has an invalid when condition`);
      }
    });
    return;
  }

  if (view.kind === "form" || view.kind === "detail") {
    if (ui.fields !== undefined && !Array.isArray(ui.fields)) errors.push(`${label} fields must be an array`);
    if (!Array.isArray(ui.fields)) return;
    ui.fields.forEach((item, index) => {
      if (!record(item) || typeof item.column !== "string" || (knownColumns.size > 0 && !knownColumns.has(item.column))) { errors.push(`${label} fields[${index}] references an unknown column`); return; }
      const column = columnsByTable.get(view.table_slug!)?.has(item.column) ?? false;
      if (!column) errors.push(`${label} fields[${index}] references an unknown column`);
      const widget = typeof item.widget === "string" ? item.widget : "value";
      if (!FORM_WIDGETS.has(widget)) errors.push(`${label} fields[${index}] uses unsupported widget '${widget}'`);
      const fieldColumnType = columns.find((entry) => entry.table_slug === view.table_slug && entry.name === item.column)?.type;
      if (fieldColumnType === "ref") errors.push(`${label} fields[${index}] ref columns are not supported without a declared reference picker`);
      if (fieldColumnType === "enum" && widget !== "choice") errors.push(`${label} fields[${index}] enum columns require the choice widget`);
      if (fieldColumnType === "enum" && !Array.isArray(columns.find((entry) => entry.table_slug === view.table_slug && entry.name === item.column)?.enum?.values)) errors.push(`${label} fields[${index}] enum columns require declared values`);
      if (widget === "switch" && !["bool", undefined].includes(fieldColumnType)) errors.push(`${label} fields[${index}] switch requires a bool column`);
      if (widget === "choice" && !["enum", undefined].includes(fieldColumnType)) errors.push(`${label} fields[${index}] choice requires an enum column`);
      if (widget === "file_upload" && !["file_ref", undefined].includes(columns.find((entry) => entry.table_slug === view.table_slug && entry.name === item.column)?.type)) errors.push(`${label} fields[${index}] file_upload requires a file_ref column`);
      if (columns.find((entry) => entry.table_slug === view.table_slug && entry.name === item.column)?.type === "file_ref" && widget !== "file_upload") errors.push(`${label} fields[${index}] file_ref requires the file_upload widget`);
      if (widget === "project_multiselect" && item.column !== "project_ids") errors.push(`${label} fields[${index}] project_multiselect must bind project_ids`);
      if (item.read_only !== undefined && typeof item.read_only !== "boolean") errors.push(`${label} fields[${index}] read_only must be boolean`);
      if (item.secret !== undefined && typeof item.secret !== "boolean") errors.push(`${label} fields[${index}] secret must be boolean`);
      if (item.secret === true && columns.find((entry) => entry.table_slug === view.table_slug && entry.name === item.column)?.type !== "secret_ref") errors.push(`${label} fields[${index}] secret fields require a secret_ref column`);
      if (item.accept !== undefined && typeof item.accept !== "string" && !(Array.isArray(item.accept) && item.accept.every((entry) => typeof entry === "string"))) errors.push(`${label} fields[${index}] accept must be a string or string array`);
      if (item.trailing_action !== undefined && (!record(item.trailing_action) || typeof item.trailing_action.action_id !== "string" || Object.keys(item.trailing_action).some((key) => !["action_id", "icon", "tooltip"].includes(key)))) errors.push(`${label} fields[${index}] trailing_action is invalid`);
      for (const key of Object.keys(item)) if (!["column", "widget", "read_only", "secret", "accept", "trailing_action"].includes(key)) errors.push(`${label} fields[${index}] uses unsupported property '${key}'`);
    });
  }
}

export function validateModuleManifest(input: unknown): ManifestValidation {
  const errors: string[] = [];
  if (!record(input)) return invalid("manifest must be a JSON object");
  const tables = list<ModuleTableDefinition>(input.tables, "tables", errors);
  const columns = list<ModuleColumnDefinition>(input.columns, "columns", errors);
  const rawViews = list<ModuleViewDefinition>(input.views, "views", errors);
  const tabs = list<ModuleTabDefinition>(input.tabs, "tabs", errors);
  const actions = list<ModuleActionDefinition>(input.actions, "actions", errors);
  const tableSlugs = new Set<string>();
  for (const table of tables) {
    if (typeof table.slug !== "string" || !SLUG.test(table.slug)) errors.push(`invalid table slug '${String(table.slug)}'`);
    else if (tableSlugs.has(table.slug)) errors.push(`duplicate table slug '${table.slug}'`);
    else tableSlugs.add(table.slug);
    if (table.enabled !== undefined && typeof table.enabled !== "boolean") errors.push(`table ${table.slug} enabled must be boolean`);
    if (table.storage_kind !== undefined && !STORAGE_KINDS.has(String(table.storage_kind))) errors.push(`table ${table.slug} has unsupported storage_kind`);
    if (record(table.scope)) {
      if (table.scope.projects !== undefined && !PROJECT_SCOPES.has(String(table.scope.projects))) errors.push(`table ${table.slug} has unsupported project scope`);
      if (table.scope.chats !== undefined && table.scope.chats !== "all" && table.scope.chats !== "current") errors.push(`table ${table.slug} has unsupported chat scope`);
    } else if (table.scope !== undefined) errors.push(`table ${table.slug} scope must be an object`);
  }

  const columnsByTable = new Map<string, Set<string>>();
  for (const column of columns) {
    if (!tableSlugs.has(column.table_slug)) errors.push(`column references unknown table '${column.table_slug}'`);
    if (typeof column.name !== "string" || !SLUG.test(column.name)) errors.push(`invalid column name '${String(column.name)}'`);
    if (!COLUMN_TYPES.has(column.type)) errors.push(`column ${column.name} has unsupported type '${String(column.type)}'`);
    const names = columnsByTable.get(column.table_slug) ?? new Set<string>();
    if (names.has(column.name)) errors.push(`duplicate column '${column.table_slug}.${column.name}'`);
    names.add(column.name);
    columnsByTable.set(column.table_slug, names);
  }

  const views = rawViews.map((view) => {
    const body = record(view.ui_json) ? view.ui_json : undefined;
    const kind = body?.kind ?? view.kind;
    return { ...view, kind, ui_json: body } as ModuleViewDefinition;
  });
  const viewSlugs = new Set<string>();
  for (const view of views) {
    if (typeof view.slug !== "string" || !SLUG.test(view.slug)) errors.push(`invalid view slug '${String(view.slug)}'`);
    else if (viewSlugs.has(view.slug)) errors.push(`duplicate view slug '${view.slug}'`);
    else viewSlugs.add(view.slug);
    if (!VIEW_KINDS.has(view.kind)) errors.push(`view ${view.slug} has unsupported kind '${String(view.kind)}'`);
    if (view.enabled !== undefined && typeof view.enabled !== "boolean") errors.push(`view ${view.slug} enabled must be boolean`);
    if (view.label !== undefined && !checkMetaLabel(view.label)) errors.push(`view ${view.slug} label must be text or localized labels`);
  }
  const tabIds = new Set<string>();
  for (const tab of tabs) {
    if (typeof tab.id !== "string" || !SLUG.test(tab.id) || tabIds.has(tab.id)) errors.push(`invalid or duplicate tab id '${String(tab.id)}'`);
    tabIds.add(tab.id);
    if (!checkMetaLabel(tab.title)) errors.push(`tab ${tab.id} title must be text or localized labels`);
    if (typeof tab.view_slug !== "string" || !viewSlugs.has(tab.view_slug)) errors.push(`tab ${tab.id} references unknown view`);
    if (tab.table_slug !== undefined && !tableSlugs.has(tab.table_slug)) errors.push(`tab ${tab.id} references unknown table`);
    if (tab.enabled !== undefined && typeof tab.enabled !== "boolean") errors.push(`tab ${tab.id} enabled must be boolean`);
    if (tab.default_project_bind !== undefined && tab.default_project_bind !== "local" && tab.default_project_bind !== "global") errors.push(`tab ${tab.id} has invalid default_project_bind`);
    if (tab.nav !== undefined && (!record(tab.nav) || (tab.nav.placement !== undefined && !NAV_PLACEMENTS.has(String(tab.nav.placement))) || (tab.nav.contour !== undefined && !["employee", "admin", "company", "cabinet"].includes(String(tab.nav.contour))))) errors.push(`tab ${tab.id} has invalid nav placement/contour`);
  }
  const actionIds = new Set<string>();
  for (const action of actions) {
    if (typeof action.id !== "string" || !SLUG.test(action.id) || actionIds.has(action.id)) errors.push(`invalid or duplicate action id '${String(action.id)}'`);
    actionIds.add(action.id);
    if (typeof action.kind !== "string" || !action.kind) errors.push(`action ${action.id} kind must be a non-empty string`);
    if (action.enabled !== undefined && typeof action.enabled !== "boolean") errors.push(`action ${action.id} enabled must be boolean`);
  }
  const warnings: string[] = [];
  const invalidViews: Record<string, string> = {};
  for (const view of views) {
    const viewErrors: string[] = [];
    validateUi(view, viewSlugs, tableSlugs, columns, columnsByTable, viewErrors);
    const ui = view.ui_json;
    if (record(ui)) {
      const actionNodes: unknown[] = [];
      if (record(ui.scaffold) && Array.isArray(ui.scaffold.actions)) actionNodes.push(...ui.scaffold.actions);
      for (const key of ["toolbar", "row_actions"]) if (Array.isArray(ui[key])) actionNodes.push(...ui[key] as unknown[]);
      for (const field of Array.isArray(ui.fields) ? ui.fields : []) if (record(field) && record(field.trailing_action)) actionNodes.push({ action: field.trailing_action.action_id });
      for (const node of actionNodes) if (record(node) && typeof node.action === "string") {
        const action = actions.find((candidate) => candidate.id === node.action);
        if (!action) viewErrors.push(`view ${view.slug} references unknown action '${node.action}'`);
        else if (!MODULE_ACTION_KINDS.has(action.kind)) viewErrors.push(`view ${view.slug} references unsupported action kind '${action.kind}'`);
      }
    }
    if (viewErrors.length) invalidViews[view.slug] = viewErrors.join("; ");
  }
  if (errors.length) return invalid(errors[0], errors);
  return { ok: true, manifest: { syntax_version: typeof input.syntax_version === "number" ? input.syntax_version : 1, tables, columns, views, tabs, actions, ...(Object.keys(invalidViews).length ? { invalid_views: invalidViews } : {}) } };
}

function invalid(message: string, errors?: readonly string[]): ManifestValidation {
  return { ok: false, error: errors && errors.length > 1 ? `${message} (+${errors.length - 1} issues)` : message, repairHint: "Ask a module administrator to repair the module's tables, columns, views, tabs, and action IDs. This client will not execute unsupported meta." };
}

export function unwrapMetaDocument(value: unknown): unknown {
  if (!record(value)) return value;
  const body = value.body;
  if (record(body) && Array.isArray(body.items)) return body.items;
  return body;
}

export function validateManifestDocuments(documents: Readonly<Record<string, unknown>>): ManifestValidation {
  const normalized: JsonRecord = {};
  for (const slug of ["tables", "columns", "views", "tabs", "actions"]) {
    normalized[slug] = unwrapMetaDocument(documents[slug]) ?? [];
  }
  return validateModuleManifest(normalized);
}

export function labelText(value: unknown, locale?: string): string {
  if (typeof value === "string") return value;
  if (!record(value)) return "";
  const language = (locale ?? (typeof navigator !== "undefined" ? navigator.language : "en")).slice(0, 2);
  const localized = value[language] ?? value.en ?? value.ru;
  return typeof localized === "string" ? localized : "";
}
