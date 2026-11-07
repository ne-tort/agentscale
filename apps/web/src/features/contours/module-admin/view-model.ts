import type { ModuleAdminMode, ModuleAdminRow } from "./service";

function text(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

/** What the company may do with the module. Rendered as words by the screen. */
export type ModuleRights = "writable" | "readOnly" | "childMayEdit" | "childReadOnly";

/** A value the catalog did not return. The screen says which one is missing. */
export type ModuleGap = "source" | "version" | "rights";

export interface ModuleEvidence {
  source?: string;
  version?: string;
  rights?: ModuleRights;
  bindKind?: "local" | "global";
  childMayEdit?: boolean;
  gaps: ModuleGap[];
}

/**
 * Only values the API actually returned are reported here. When one of them is
 * missing the screen names the missing value — it used to print "API contract
 * gap", which told the reader nothing they could act on.
 */
export function moduleEvidence(row: ModuleAdminRow, mode: ModuleAdminMode): ModuleEvidence {
  const source = text(row.source);
  const version = text(row.version) ?? text(row.module_version) ?? text(row.content_version);
  const bindKind = row.bind_kind === "local" || row.bind_kind === "global" ? row.bind_kind : undefined;
  const childMayEdit = typeof row.child_may_edit === "boolean" ? row.child_may_edit : undefined;
  let rights: ModuleRights | undefined;
  if (mode === "company" && typeof row.writable === "boolean") {
    rights = row.writable ? "writable" : "readOnly";
  } else if (mode === "admin" && childMayEdit !== undefined) {
    rights = childMayEdit ? "childMayEdit" : "childReadOnly";
  }
  const gaps: ModuleGap[] = [];
  if (!source) gaps.push("source");
  if (!version) gaps.push("version");
  if (!rights) gaps.push("rights");
  return { source, version, rights, bindKind, childMayEdit, gaps };
}

export function recordId(row: ModuleAdminRow, fallback = ""): string {
  return text(row.id) ?? text(row.module_id) ?? text(row.project_id) ?? text(row.cabinet_id) ?? text(row.company_id) ?? fallback;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function templateRows(rows: ModuleAdminRow[]): ModuleAdminRow[] {
  return rows.filter((row) => isRecord(row.body) && row.body.active !== false);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
