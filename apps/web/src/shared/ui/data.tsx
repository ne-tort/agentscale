import { useId, useMemo, useState, type ReactNode } from "react";
import { InlineError, LoadingState } from "./feedback";

export function filterSearchItems<T>(items: readonly T[], query: string, getSearchText: (item: T) => string): T[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [...items];
  return items.filter((item) => getSearchText(item).toLocaleLowerCase().includes(needle));
}

export interface SearchControlProps {
  readonly query?: string;
  readonly onQueryChange?: (query: string) => void;
  readonly searchLabel?: string;
  readonly placeholder?: string;
}

function useSearchQuery(query: string | undefined, onQueryChange: ((query: string) => void) | undefined) {
  const [internalQuery, setInternalQuery] = useState("");
  const isControlled = query !== undefined;
  return {
    value: isControlled ? query : internalQuery,
    onChange: (nextQuery: string) => {
      if (!isControlled) setInternalQuery(nextQuery);
      onQueryChange?.(nextQuery);
    },
  };
}

export interface SearchableColumn<T> {
  readonly id: string;
  readonly header: string;
  readonly getValue: (row: T) => ReactNode;
  readonly getSearchText?: (row: T) => string;
  readonly render?: (value: ReactNode, row: T) => ReactNode;
}

export interface SearchableTableProps<T> extends SearchControlProps {
  readonly caption: string;
  readonly rows: readonly T[];
  readonly columns: ReadonlyArray<SearchableColumn<T>>;
  readonly getRowKey: (row: T) => string | number;
  readonly loading?: boolean;
  readonly error?: string;
  readonly onRetry?: () => void;
  readonly emptyState?: ReactNode;
  readonly noResults?: ReactNode;
}

export function SearchableTable<T>({
  caption,
  rows,
  columns,
  getRowKey,
  loading = false,
  error,
  onRetry,
  emptyState = "No records yet.",
  noResults = "No matching records.",
  query,
  onQueryChange,
  searchLabel = `Search ${caption}`,
  placeholder = "Filter records",
}: SearchableTableProps<T>) {
  const generatedId = useId();
  const searchId = `${generatedId}-search`;
  const search = useSearchQuery(query, onQueryChange);
  const filteredRows = useMemo(() => filterSearchItems(rows, search.value, (row) => columns
    .map((column) => column.getSearchText?.(row) ?? searchTextFromValue(column.getValue(row)))
    .join(" ")),
  [columns, rows, search.value]);

  return <section aria-busy={loading} aria-label={`${caption} search and results`} className="ui-searchable-data">
    <div className="ui-search-tools">
      <label className="ui-search-tools__label" htmlFor={searchId}>{searchLabel}</label>
      <input
        className="ui-control ui-search-tools__input"
        id={searchId}
        onChange={(event) => search.onChange(event.currentTarget.value)}
        placeholder={placeholder}
        type="search"
        value={search.value}
      />
      <span aria-live="polite" className="ui-search-tools__count">{filteredRows.length} {filteredRows.length === 1 ? "result" : "results"}</span>
    </div>
    {loading && <LoadingState label="Loading records" />}
    {error && <InlineError message={error} action={onRetry && <button className="ui-inline-link" onClick={onRetry} type="button">Retry</button>} />}
    {!loading && rows.length === 0 && !error && <div className="ui-data-state">{emptyState}</div>}
    {!loading && rows.length > 0 && filteredRows.length === 0 && !error && <div className="ui-data-state">{noResults}</div>}
    {!loading && !error && filteredRows.length > 0 && <div aria-label={`${caption} table`} className="ui-table-scroll" role="region" tabIndex={0}>
      <table className="ui-table">
        <caption>{caption}</caption>
        <thead><tr>{columns.map((column) => <th key={column.id} scope="col">{column.header}</th>)}</tr></thead>
        <tbody>{filteredRows.map((row) => <tr key={getRowKey(row)}>
          {columns.map((column) => {
            const value = column.getValue(row);
            return <td key={column.id}>{column.render ? column.render(value, row) : value}</td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}

function searchTextFromValue(value: ReactNode): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

export interface SearchableListProps<T> extends SearchControlProps {
  readonly items: readonly T[];
  readonly getKey: (item: T) => string | number;
  readonly getSearchText: (item: T) => string;
  readonly renderItem: (item: T) => ReactNode;
  readonly emptyState?: ReactNode;
  readonly noResults?: ReactNode;
  readonly loading?: boolean;
  readonly error?: string;
  readonly onRetry?: () => void;
  readonly label: string;
}

export function SearchableList<T>({
  items,
  getKey,
  getSearchText,
  renderItem,
  emptyState = "No items yet.",
  noResults = "No matching items.",
  loading = false,
  error,
  onRetry,
  label,
  query,
  onQueryChange,
  searchLabel = `Search ${label}`,
  placeholder = "Filter items",
}: SearchableListProps<T>) {
  const generatedId = useId();
  const searchId = `${generatedId}-search`;
  const search = useSearchQuery(query, onQueryChange);
  const filteredItems = useMemo(
    () => filterSearchItems(items, search.value, getSearchText),
    [getSearchText, items, search.value],
  );

  return <section aria-busy={loading} aria-label={label} className="ui-searchable-data">
    <div className="ui-search-tools">
      <label className="ui-search-tools__label" htmlFor={searchId}>{searchLabel}</label>
      <input
        className="ui-control ui-search-tools__input"
        id={searchId}
        onChange={(event) => search.onChange(event.currentTarget.value)}
        placeholder={placeholder}
        type="search"
        value={search.value}
      />
      <span aria-live="polite" className="ui-search-tools__count">{filteredItems.length} {filteredItems.length === 1 ? "result" : "results"}</span>
    </div>
    {loading && <LoadingState label={`Loading ${label}`} />}
    {error && <InlineError message={error} action={onRetry && <button className="ui-inline-link" onClick={onRetry} type="button">Retry</button>} />}
    {!loading && items.length === 0 && !error && <div className="ui-data-state">{emptyState}</div>}
    {!loading && items.length > 0 && filteredItems.length === 0 && !error && <div className="ui-data-state">{noResults}</div>}
    {!loading && !error && filteredItems.length > 0 && <ul aria-label={label} className="ui-searchable-list">
      {filteredItems.map((item) => <li className="ui-searchable-list__item" key={getKey(item)}>{renderItem(item)}</li>)}
    </ul>}
  </section>;
}