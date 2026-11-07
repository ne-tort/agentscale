import type { ModuleAdminMode, ModuleAdminRow } from "./service";

function text(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

export interface ModuleEvidence {
  source?: string;
  version?: string;
  rights?: string;
  bindKind?: string;
  childMayEdit?: boolean;
  gaps: string[];
}

export function moduleEvidence(row: ModuleAdminRow, mode: ModuleAdminMode): ModuleEvidence {
  const source = text(row.source);
  const version = text(row.version) ?? text(row.module_version) ?? text(row.content_version);
  const bindKind = row.bind_kind === "local" || row.bind_kind === "global" ? row.bind_kind : undefined;
  const childMayEdit = typeof row.child_may_edit === "boolean" ? row.child_may_edit : undefined;
  let rights: string | undefined;
  if (mode === "company" && typeof row.writable === "boolean") {
    rights = row.writable ? "Writable (API)" : "Read only (API)";
  } else if (mode === "admin" && childMayEdit !== undefined) {
    rights = childMayEdit ? "Child may edit (API)" : "Child read only (API)";
  }
  const gaps: string[] = [];
  // These surface on a screen a company administrator reads. "API contract gap"
  // was a note to the frontend team that ended up in front of a customer; what
  // they need to know is simply that the value is missing.
  if (!source) gaps.push("Источник не указан.");
  if (!version) gaps.push("Версия модуля не указана.");
  if (!rights) gaps.push("Права не указаны.");
  return { source, version, rights, bindKind, childMayEdit, gaps };
}

export function recordId(row: ModuleAdminRow, fallback = ""): string {
  return text(row.id) ?? text(row.module_id) ?? text(row.project_id) ?? text(row.cabinet_id) ?? text(row.company_id) ?? fallback;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not provided by API";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function templateRows(rows: ModuleAdminRow[]): ModuleAdminRow[] {
  return rows.filter((row) => isRecord(row.body) && row.body.active !== false);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}