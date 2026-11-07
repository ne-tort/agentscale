import { useEffect, useMemo, useState, type FormEvent, type InputHTMLAttributes, type ReactNode } from "react";
import type { Translate } from "../../i18n/catalogue";
import { useI18n } from "../../i18n/useT";
import type { ContourRow } from "./service";
import { display, filterPage } from "./view-model";

/**
 * API enum values that used to reach the screen as-is ("active", "local",
 * "selected"). They are data, not words a company administrator should have to
 * decode, so they read as labels here; anything unrecognised is shown untouched.
 */
const ENUM_KEYS: Record<string, string> = {
  active: "contours.valueActive",
  pending: "contours.valuePending",
  disabled: "contours.valueDisabled",
  invited: "contours.valueInvited",
  local: "contours.valueLocal",
  global: "contours.valueGlobal",
  selected: "contours.valueSelected",
  all: "contours.valueAll",
  member: "contours.valueMember",
  "company.admin": "contours.valueCompanyAdmin",
};

/** A cell's text: booleans and known enums become words, everything else passes through. */
export function enumLabel(value: unknown, t: Translate): string {
  if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
  const key = typeof value === "string" ? ENUM_KEYS[value] : undefined;
  return key ? t(key) : display(value);
}

export function useRows<T extends ContourRow>(loader: () => Promise<T[]>, dependencies: unknown[] = []) {
  const t = useI18n().t;
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const refresh = async () => {
    setLoading(true); setError(undefined);
    try { setRows(await loader()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t("common.requestFailed")); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); // eslint-disable-line react-hooks/exhaustive-deps
  }, dependencies);
  return { rows, setRows, loading, error, refresh, setError };
}

export function Notice({ error, success }: { error?: string; success?: string }) {
  return <>{error && <p className="contour-notice contour-notice--error" role="alert">{error}</p>}{success && <p className="contour-notice contour-notice--success" role="status">{success}</p>}</>;
}

export function DataTable<T extends ContourRow>({
  rows, columns, loading, error, onRetry, query, onQuery, actions, empty, rowLabel = (row) => String(row.name ?? row.id),
}: {
  rows: T[]; columns: Array<{ key: string; label: string }>; loading: boolean; error?: string; onRetry: () => void;
  query: string; onQuery: (query: string) => void; actions?: (row: T) => ReactNode; empty?: string; rowLabel?: (row: T) => string;
}) {
  const { t, plural } = useI18n();
  const [page, setPage] = useState(1);
  const pageData = useMemo(() => filterPage(rows, query, page, 10, columns.map((column) => column.key)), [columns, page, query, rows]);
  useEffect(() => setPage(1), [query]);
  return <section className="contour-table-wrap" aria-busy={loading}>
    <div className="contour-table-tools"><label>{t("common.search")}<input type="search" value={query} onChange={(event) => onQuery(event.target.value)} placeholder={t("contours.tableFilterPlaceholder")} /></label><span>{plural("common.records", pageData.total)}</span></div>
    {loading && <p className="contour-state" role="status">{t("common.loading")}</p>}
    {error && <div className="contour-error" role="alert"><span>{error}</span><button type="button" onClick={onRetry}>{t("common.retry")}</button></div>}
    {!loading && !error && rows.length === 0 && <p className="contour-state">{empty ?? t("contours.tableEmpty")}</p>}
    {!loading && !error && rows.length > 0 && pageData.total === 0 && <p className="contour-state">{t("contours.tableNoMatches")}</p>}
    {!loading && !error && pageData.total > 0 && <>
      <div className="contour-table-scroll"><table><thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.label}</th>)}{actions && <th scope="col">{t("common.actions")}</th>}</tr></thead><tbody>{pageData.items.map((row) => <tr key={row.id} aria-label={rowLabel(row)}>{columns.map((column) => { const cell = enumLabel(row[column.key], t); return <td key={column.key} title={cell}>{cell}</td>; })}{actions && <td className="contour-actions">{actions(row)}</td>}</tr>)}</tbody></table></div>
      <div className="contour-pagination"><span>{t("common.page", { page: pageData.page, pages: pageData.pageCount })}</span><button type="button" onClick={() => setPage((value) => value - 1)} disabled={pageData.page <= 1}>{t("common.previous")}</button><button type="button" onClick={() => setPage((value) => value + 1)} disabled={pageData.page >= pageData.pageCount}>{t("common.next")}</button></div>
    </>}
  </section>;
}

export function ConfirmAction({ label, confirmLabel, onConfirm, disabled = false, danger = true }: {
  label: string; confirmLabel?: string; onConfirm: () => void; disabled?: boolean; danger?: boolean;
}) {
  const t = useI18n().t;
  const [confirming, setConfirming] = useState(false);
  return confirming
    ? <span className="contour-confirm"><span>{label}?</span><button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => { setConfirming(false); onConfirm(); }}>{confirmLabel ?? t("contours.confirm")}</button><button type="button" onClick={() => setConfirming(false)} disabled={disabled}>{t("common.cancel")}</button></span>
    : <button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => setConfirming(true)}>{label}</button>;
}

export function FormPanel({ title, children, onSubmit, submitLabel, busy, onCancel }: {
  title: string; children: ReactNode; onSubmit: (event: FormEvent<HTMLFormElement>) => void; submitLabel: string; busy?: boolean; onCancel?: () => void;
}) {
  const t = useI18n().t;
  return <form className="contour-form" onSubmit={onSubmit}><h3>{title}</h3><div className="contour-form-grid">{children}</div><div className="contour-form-actions"><button type="submit" disabled={busy}>{busy ? t("common.saving") : submitLabel}</button>{onCancel && <button type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button>}</div></form>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="contour-field">{label}{children}</label>; }
export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) { return <input {...props} />; }
