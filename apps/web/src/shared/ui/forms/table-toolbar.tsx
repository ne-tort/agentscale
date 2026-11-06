import { useId, useState, type ReactNode } from "react";

export interface TableToolbarProps {
  readonly label: string;
  readonly query?: string;
  readonly onQueryChange?: (query: string) => void;
  readonly searchLabel?: string;
  readonly placeholder?: string;
  readonly resultCount?: number;
  readonly resultLabel?: (count: number) => string;
  readonly children?: ReactNode;
  readonly className?: string;
}

export function TableToolbar({
  label,
  query,
  onQueryChange,
  searchLabel = `Search ${label}`,
  placeholder = "Filter records",
  resultCount,
  resultLabel = (count) => `${count} ${count === 1 ? "result" : "results"}`,
  children,
  className,
}: TableToolbarProps) {
  const generatedId = useId();
  const searchId = `${generatedId}-search`;
  const controlled = query !== undefined;
  const [internalQuery, setInternalQuery] = useState("");
  const value = controlled ? query : internalQuery;

  const updateQuery = (nextQuery: string) => {
    if (!controlled) setInternalQuery(nextQuery);
    onQueryChange?.(nextQuery);
  };

  return <div aria-label={`${label} table filters`} className={["form-table-toolbar", className].filter(Boolean).join(" ")} role="group">
    <label className="form-table-toolbar__search-label" htmlFor={searchId}>{searchLabel}</label>
    <input
      className="form-control form-table-toolbar__search"
      id={searchId}
      onChange={(event) => updateQuery(event.currentTarget.value)}
      placeholder={placeholder}
      type="search"
      value={value}
    />
    {children && <div className="form-table-toolbar__filters">{children}</div>}
    {resultCount !== undefined && <span aria-live="polite" className="form-table-toolbar__count">{resultLabel(resultCount)}</span>}
  </div>;
}