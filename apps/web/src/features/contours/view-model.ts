export interface PageResult<T> { items: T[]; page: number; pageSize: number; total: number; pageCount: number }

function searchable(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value) ?? "";
  return String(value);
}

export function filterPage<T extends Record<string, unknown>>(
  rows: T[], query: string, page: number, pageSize: number, fields: Array<keyof T | string> = Object.keys(rows[0] ?? {}),
): PageResult<T> {
  const term = query.trim().toLocaleLowerCase();
  const filtered = term ? rows.filter((row) => fields.some((field) => searchable(row[field as keyof T]).toLocaleLowerCase().includes(term))) : rows;
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(Math.max(1, Math.trunc(page) || 1), pageCount);
  return { items: filtered.slice((safePage - 1) * pageSize, safePage * pageSize), page: safePage, pageSize, total: filtered.length, pageCount };
}

export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value) ?? "";
  return String(value);
}
