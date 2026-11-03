import { useEffect, useMemo, useState, type FormEvent, type InputHTMLAttributes, type ReactNode } from "react";
import type { ContourRow } from "./service";
import { display, filterPage } from "./view-model";

export function useRows<T extends ContourRow>(loader: () => Promise<T[]>, dependencies: unknown[] = []) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const refresh = async () => {
    setLoading(true); setError(undefined);
    try { setRows(await loader()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Request failed."); }
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
  rows, columns, loading, error, onRetry, query, onQuery, actions, empty = "No records found.", rowLabel = (row) => String(row.name ?? row.id),
}: {
  rows: T[]; columns: Array<{ key: string; label: string }>; loading: boolean; error?: string; onRetry: () => void;
  query: string; onQuery: (query: string) => void; actions?: (row: T) => ReactNode; empty?: string; rowLabel?: (row: T) => string;
}) {
  const [page, setPage] = useState(1);
  const pageData = useMemo(() => filterPage(rows, query, page, 10, columns.map((column) => column.key)), [columns, page, query, rows]);
  useEffect(() => setPage(1), [query]);
  return <section className="contour-table-wrap" aria-busy={loading}>
    <div className="contour-table-tools"><label>Search<input type="search" value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Filter this list" /></label><span>{pageData.total} records</span></div>
    {loading && <p className="contour-state" role="status">Loading…</p>}
    {error && <div className="contour-error" role="alert"><span>{error}</span><button type="button" onClick={onRetry}>Retry</button></div>}
    {!loading && !error && rows.length === 0 && <p className="contour-state">{empty}</p>}
    {!loading && !error && rows.length > 0 && pageData.total === 0 && <p className="contour-state">No matches. Clear the search to see all records.</p>}
    {!loading && !error && pageData.total > 0 && <>
      <div className="contour-table-scroll"><table><thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.label}</th>)}{actions && <th scope="col">Actions</th>}</tr></thead><tbody>{pageData.items.map((row) => <tr key={row.id} aria-label={rowLabel(row)}>{columns.map((column) => <td key={column.key} title={display(row[column.key])}>{display(row[column.key])}</td>)}{actions && <td className="contour-actions">{actions(row)}</td>}</tr>)}</tbody></table></div>
      <div className="contour-pagination"><span>Page {pageData.page} of {pageData.pageCount}</span><button type="button" onClick={() => setPage((value) => value - 1)} disabled={pageData.page <= 1}>Previous</button><button type="button" onClick={() => setPage((value) => value + 1)} disabled={pageData.page >= pageData.pageCount}>Next</button></div>
    </>}
  </section>;
}

export function ConfirmAction({ label, confirmLabel = "Confirm", onConfirm, disabled = false, danger = true }: {
  label: string; confirmLabel?: string; onConfirm: () => void; disabled?: boolean; danger?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  return confirming
    ? <span className="contour-confirm"><span>{label}?</span><button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => { setConfirming(false); onConfirm(); }}>{confirmLabel}</button><button type="button" onClick={() => setConfirming(false)} disabled={disabled}>Cancel</button></span>
    : <button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => setConfirming(true)}>{label}</button>;
}

export function FormPanel({ title, children, onSubmit, submitLabel, busy, onCancel }: {
  title: string; children: ReactNode; onSubmit: (event: FormEvent<HTMLFormElement>) => void; submitLabel: string; busy?: boolean; onCancel?: () => void;
}) {
  return <form className="contour-form" onSubmit={onSubmit}><h3>{title}</h3><div className="contour-form-grid">{children}</div><div className="contour-form-actions"><button type="submit" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>{onCancel && <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>}</div></form>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="contour-field">{label}{children}</label>; }
export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) { return <input {...props} />; }
