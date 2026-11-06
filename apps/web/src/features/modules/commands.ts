import type { JsonRecord, ModuleActionResult, ModuleManifest, ModuleRuntimeRouteState, ModuleViewDefinition } from "./contracts";

export interface ModuleActionInputField {
  readonly name: string;
  readonly label?: unknown;
  readonly type: "text" | "number" | "bool" | "datetime" | "json" | "enum";
  readonly required?: boolean;
  readonly values?: readonly string[];
}

export type ModuleCollectionRowAction =
  | { readonly kind: "invoke_action"; readonly action: string; readonly label?: unknown; readonly tooltip?: unknown; readonly icon?: unknown; readonly when?: JsonRecord; readonly params_form?: readonly ModuleActionInputField[] }
  | { readonly kind: "open_view"; readonly view: string; readonly label?: unknown; readonly tooltip?: unknown; readonly icon?: unknown; readonly when?: JsonRecord };

export type ModuleCollectionRowActionParse =
  | { readonly ok: true; readonly action: ModuleCollectionRowAction }
  | { readonly ok: false; readonly error: string };

const RESULT_STATUSES = new Set(["queued", "draft", "ready", "skipped", "sent", "processing", "started", "completed", "error", "failed"]);

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validWhen(value: unknown): value is JsonRecord {
  if (value === undefined) return true;
  if (!isRecord(value) || typeof value.field !== "string" || !value.field) return false;
  const keys = Object.keys(value);
  if (keys.some((key) => key !== "field" && key !== "eq" && key !== "in")) return false;
  if (("eq" in value) === ("in" in value)) return false;
  return !("in" in value) || (Array.isArray(value.in) && value.in.length > 0);
}

export function parseModuleCollectionRowAction(value: unknown): ModuleCollectionRowActionParse {
  if (!isRecord(value)) return { ok: false, error: "row action must be an object." };
  const allowed = new Set(["kind", "action", "view", "label", "tooltip", "icon", "when", "params_form"]);
  const unknown = Object.keys(value).find((key) => !allowed.has(key));
  if (unknown) return { ok: false, error: `row action has unsupported property '${unknown}'.` };
  if (!validWhen(value.when)) return { ok: false, error: "row action when must declare one field comparison (eq or in)." };
  const kind = value.kind ?? (typeof value.action === "string" ? "invoke_action" : undefined);
  if (kind === "invoke_action" && typeof value.action === "string" && value.action) {
    if (value.view !== undefined) return { ok: false, error: "invoke_action cannot also declare a view." };
    const paramsForm = parseModuleActionInputFields(value.params_form);
    if (paramsForm.error) return { ok: false, error: paramsForm.error };
    return { ok: true, action: { kind, action: value.action, ...(value.label !== undefined ? { label: value.label } : {}), ...(value.tooltip !== undefined ? { tooltip: value.tooltip } : {}), ...(value.icon !== undefined ? { icon: value.icon } : {}), ...(isRecord(value.when) ? { when: value.when } : {}), ...(paramsForm.fields ? { params_form: paramsForm.fields } : {}) } };
  }
  if (kind === "open_view" && typeof value.view === "string" && value.view) {
    if (value.action !== undefined || value.params_form !== undefined) return { ok: false, error: "open_view cannot declare an action or params_form." };
    return { ok: true, action: { kind, view: value.view, ...(value.label !== undefined ? { label: value.label } : {}), ...(value.tooltip !== undefined ? { tooltip: value.tooltip } : {}), ...(value.icon !== undefined ? { icon: value.icon } : {}), ...(isRecord(value.when) ? { when: value.when } : {}) } };
  }
  return { ok: false, error: "row action must invoke a declared action or open a declared view." };
}

function parseModuleActionInputFields(value: unknown): { fields?: ModuleActionInputField[]; error?: string } {
  if (value === undefined) return {};
  if (!Array.isArray(value)) return { error: "row action params_form must be an array." };
  const names = new Set<string>();
  const fields: ModuleActionInputField[] = [];
  for (const [index, raw] of value.entries()) {
    if (!isRecord(raw) || typeof raw.name !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(raw.name) || names.has(raw.name)) return { error: `row action params_form[${index}] has an invalid or duplicate name.` };
    if (!["text", "number", "bool", "datetime", "json", "enum"].includes(String(raw.type))) return { error: `row action params_form[${index}] has an unsupported type.` };
    if (raw.required !== undefined && typeof raw.required !== "boolean") return { error: `row action params_form[${index}].required must be boolean.` };
    if (raw.type === "enum" && (!Array.isArray(raw.values) || raw.values.length === 0 || raw.values.some((item) => typeof item !== "string"))) return { error: `row action params_form[${index}] enum requires string values.` };
    if (raw.type !== "enum" && raw.values !== undefined) return { error: `row action params_form[${index}] values are only supported for enum fields.` };
    if (Object.keys(raw).some((key) => !["name", "label", "type", "required", "values"].includes(key))) return { error: `row action params_form[${index}] has an unsupported property.` };
    names.add(raw.name);
    fields.push({ name: raw.name, type: raw.type as ModuleActionInputField["type"], ...(raw.label !== undefined ? { label: raw.label } : {}), ...(raw.required !== undefined ? { required: raw.required } : {}), ...(Array.isArray(raw.values) ? { values: raw.values as string[] } : {}) });
  }
  return { fields };
}

export function collectModuleActionParams(fields: readonly ModuleActionInputField[], formData: FormData): { params?: JsonRecord; error?: string } {
  const params: JsonRecord = {};
  for (const field of fields) {
    const raw = formData.get(field.name);
    if (field.type === "bool") {
      params[field.name] = raw === "true";
      continue;
    }
    const text = String(raw ?? "").trim();
    if (!text) {
      if (field.required) return { error: `Заполните обязательный параметр «${field.name}».` };
      continue;
    }
    if (field.type === "number") {
      const number = Number(text);
      if (!Number.isFinite(number)) return { error: `Параметр «${field.name}» должен быть числом.` };
      params[field.name] = number;
    } else if (field.type === "json") {
      try {
        const value: unknown = JSON.parse(text);
        if (typeof value !== "object" || value === null) return { error: `Параметр «${field.name}» должен быть JSON-объектом или массивом.` };
        params[field.name] = value;
      } catch {
        return { error: `Параметр «${field.name}» должен содержать корректный JSON.` };
      }
    } else if (field.type === "enum") {
      if (!field.values?.includes(text)) return { error: `Параметр «${field.name}» имеет недопустимое значение.` };
      params[field.name] = text;
    } else if (field.type === "datetime") {
      const value = new Date(text);
      if (Number.isNaN(value.getTime())) return { error: `Параметр «${field.name}» должен быть датой и временем.` };
      params[field.name] = value.toISOString();
    } else {
      params[field.name] = text;
    }
  }
  return { params };
}

export function parseModuleColumnValue(column: { readonly name: string; readonly type: string; readonly enum?: { readonly values?: readonly string[] } }, raw: string): { readonly value?: unknown; readonly skip?: boolean; readonly error?: string } {
  if (column.type === "text") return { value: raw };
  const text = raw.trim();
  if (!text) return { skip: true };
  if (column.type === "number") {
    const value = Number(text);
    return Number.isFinite(value) ? { value } : { error: `Поле «${column.name}» должно быть числом.` };
  }
  if (column.type === "datetime") {
    const value = new Date(text);
    return Number.isNaN(value.getTime()) ? { error: `Поле «${column.name}» должно содержать дату и время.` } : { value: value.toISOString() };
  }
  if (column.type === "json" || column.type === "file_ref") {
    try {
      const value: unknown = JSON.parse(text);
      if (typeof value !== "object" || value === null) return { error: `Поле «${column.name}» должно быть JSON-объектом или массивом.` };
      return { value };
    } catch {
      return { error: `Поле «${column.name}» должно содержать корректный JSON.` };
    }
  }
  if (column.type === "enum") return column.enum?.values && !column.enum.values.includes(text) ? { error: `Поле «${column.name}» имеет недопустимое значение.` } : { value: text };
  if (column.type === "ref") return { value: text };
  return { error: `Поле «${column.name}» использует неподдерживаемый ввод.` };
}

export function validateModuleRowBody(body: JsonRecord, columns: readonly { readonly name: string; readonly type: string; readonly required?: boolean; readonly enum?: { readonly values?: readonly string[] } }[]): string | undefined {
  const known = new Set(columns.map((column) => column.name));
  const unknown = Object.keys(body).find((key) => !known.has(key));
  if (unknown) return `Поле «${unknown}» не объявлено в схеме таблицы.`;
  for (const column of columns) {
    const value = body[column.name];
    const missing = value === undefined || value === null || (typeof value === "string" && value.trim() === "");
    if (column.required && missing) return `Заполните обязательное поле «${column.name}».`;
    if (missing) continue;
    const valid = column.type === "text" ? typeof value === "string"
      : column.type === "number" ? typeof value === "number" && Number.isFinite(value)
        : column.type === "bool" ? typeof value === "boolean"
          : column.type === "datetime" ? typeof value === "string" && !Number.isNaN(Date.parse(value))
            : column.type === "json" ? typeof value === "object" && value !== null
              : column.type === "enum" ? typeof value === "string" && (!column.enum?.values || column.enum.values.includes(value))
                : column.type === "ref" ? typeof value === "string"
                  : column.type === "file_ref" ? isRecord(value) && ["asset_id", "version_id", "storage_key", "filename"].every((key) => typeof value[key] === "string" && Boolean(value[key]))
                    : column.type === "secret_ref" ? isRecord(value) && typeof value.secret_ref === "string" && Boolean(value.secret_ref) && !["secret", "password", "value", "token"].some((key) => typeof value[key] === "string" && value[key])
                      : false;
    if (!valid) return `Поле «${column.name}» имеет некорректный тип или значение (${column.type}).`;
  }
  return undefined;
}

function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => sameValue(value, right[index]));
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every((key) => key in right && sameValue(left[key], right[key]));
  }
  return false;
}

function containsValue(expected: unknown, actual: unknown): boolean {
  if (Array.isArray(expected)) return Array.isArray(actual) && expected.length === actual.length && expected.every((value, index) => containsValue(value, actual[index]));
  if (isRecord(expected)) return isRecord(actual) && Object.entries(expected).every(([key, value]) => key in actual && containsValue(value, actual[key]));
  return expected === actual;
}

export function moduleRowContainsBody(expected: JsonRecord, actual: JsonRecord): boolean {
  return containsValue(expected, actual);
}

export function sameModuleRowBody(expected: JsonRecord, actual: JsonRecord): boolean {
  return sameValue(expected, actual);
}

/**
 * Outcome of a write followed by a fresh scoped readback. Both write surfaces (the inline
 * collection add and the form view) must describe the same four outcomes with the same
 * wording: an operator has to be able to tell "backend confirmed it" from "backend says it is
 * not there" and from "we could not ask", because only the last two leave the write in doubt.
 */
export type ModuleWriteReadbackOutcome = "confirmed" | "missing" | "unavailable" | "mismatch";

const CREATE_WRITE_NOTICES: Record<ModuleWriteReadbackOutcome, string> = {
  confirmed: "Запись создана и подтверждена повторным чтением backend.",
  missing: "Неизвестно, создана ли запись: POST завершился, но свежий GET не подтвердил созданные данные. Проверьте список перед повторной отправкой.",
  unavailable: "Неизвестно, создана ли запись: POST завершился, но свежий GET недоступен. Проверьте список перед повторной отправкой.",
  mismatch: "Неизвестный результат создания: backend вернул строку с несовпадающими данными. Проверьте список и не повторяйте операцию автоматически.",
};

const UPDATE_WRITE_NOTICES: Record<ModuleWriteReadbackOutcome, string> = {
  confirmed: "Изменения сохранены и подтверждены повторным чтением backend.",
  missing: "Неизвестный результат сохранения: свежий GET не подтвердил актуальные данные строки. Проверьте список перед повторной отправкой.",
  unavailable: "Неизвестно, сохранены ли изменения: PATCH завершился, но свежий GET недоступен. Проверьте список перед повторной отправкой.",
  mismatch: "Неизвестный результат сохранения: backend вернул строку с несовпадающими данными. Проверьте список и не повторяйте операцию автоматически.",
};

export function moduleWriteNotice(kind: "create" | "update", outcome: ModuleWriteReadbackOutcome): string {
  return (kind === "create" ? CREATE_WRITE_NOTICES : UPDATE_WRITE_NOTICES)[outcome];
}

export function moduleRowActionMatches(action: ModuleCollectionRowAction, body: JsonRecord): boolean {
  if (!action.when) return true;
  const actual = body[action.when.field as string];
  return "eq" in action.when ? actual === action.when.eq : Array.isArray(action.when.in) && action.when.in.includes(actual);
}

export function resolvePrimaryCreateRoute(
  manifest: ModuleManifest,
  sourceView: ModuleViewDefinition,
  contextRowId?: string,
): { readonly route?: ModuleRuntimeRouteState; readonly error?: string } {
  const primary = sourceView.ui_json.primary_action;
  if (!isRecord(primary) || primary.kind !== "create_row") return { error: "The collection has no supported create_row action." };
  const rowTap = isRecord(sourceView.ui_json.row_tap) ? sourceView.ui_json.row_tap : undefined;
  const targetSlug = typeof primary.view === "string" ? primary.view : typeof rowTap?.view === "string" ? rowTap.view : undefined;
  const target = targetSlug ? manifest.views.find((view) => view.slug === targetSlug && view.enabled !== false) : undefined;
  if (!target || (target.kind !== "form" && target.kind !== "detail")) return { error: `create_row must target an enabled form or detail view; '${targetSlug ?? "(missing)"}' is unavailable.` };
  if (target.table_slug !== sourceView.table_slug) return { error: `create_row target '${target.slug}' belongs to a different table.` };
  return {
    route: {
      viewSlug: target.slug,
      mode: "create",
      ...(contextRowId ? { contextRowId } : {}),
      returnTo: { viewSlug: sourceView.slug, ...(contextRowId ? { contextRowId } : {}) },
    },
  };
}

export function normalizeProjectSelection(mode: string | null, ids: readonly string[]): { readonly project_ids?: readonly string[]; readonly error?: string } {
  if (mode === "all") return { project_ids: [] };
  if (mode !== "selected") return { error: "Choose all projects or explicitly select projects." };
  const selected = [...new Set(ids.filter((id) => id.trim().length > 0))];
  return selected.length ? { project_ids: selected } : { error: "Select at least one project or choose all projects." };
}

export function validateModuleActionResult(value: unknown, expectedKind: string): ModuleActionResult {
  if (!isRecord(value)) {
    throw new Error("Backend returned an unsupported module action result.");
  }
  if (value.kind !== undefined && (typeof value.kind !== "string" || !value.kind)) {
    throw new Error("Backend returned an unsupported module action result.");
  }
  if (!expectedKind || (typeof value.kind === "string" && value.kind !== expectedKind)) {
    throw new Error(`Backend returned action result kind '${value.kind}' instead of '${expectedKind || "a declared action kind"}'.`);
  }
  if (value.ok !== undefined && typeof value.ok !== "boolean") throw new Error("Backend returned an invalid module action success flag.");
  if (value.message !== undefined && typeof value.message !== "string") throw new Error("Backend returned an invalid module action message.");
  if (value.detail !== undefined && typeof value.detail !== "string") throw new Error("Backend returned an invalid module action detail.");
  if (value.ok === false) {
    const message = typeof value.message === "string" ? value.message : typeof value.detail === "string" ? value.detail : typeof value.error === "string" ? value.error : "The module action reported failure.";
    throw new Error(message);
  }
  if (value.status !== undefined) {
    if (typeof value.status !== "string" || !RESULT_STATUSES.has(value.status)) {
      throw new Error(`Backend returned an unsupported module action status '${String(value.status)}'.`);
    }
    if (value.status === "error" || value.status === "failed") {
      const message = typeof value.message === "string" ? value.message : typeof value.detail === "string" ? value.detail : typeof value.error === "string" ? value.error : `Module action finished with status '${value.status}'.`;
      throw new Error(message);
    }
  }
  if (value.file_ref !== undefined && (!isRecord(value.file_ref) || typeof value.file_ref.asset_id !== "string" || !value.file_ref.asset_id)) {
    throw new Error("Backend returned a malformed action FileRef.");
  }
  return value as ModuleActionResult;
}

export function actionResultNotice(result: ModuleActionResult): string {
  if (typeof result.message === "string" && result.message) return result.message;
  if (typeof result.status === "string") return `Действие завершено: ${result.status}.`;
  return "Действие завершено.";
}
