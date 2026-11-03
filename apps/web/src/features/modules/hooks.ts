import { useCallback, useEffect, useMemo, useState } from "react";
import type { ModuleListQuery, ModuleManifest, ModulePage, ModuleRow, ModuleRuntimeScope, ModuleRuntimeService, ModuleViewDefinition } from "./contracts";

export interface ModuleTableState {
  readonly page?: ModulePage;
  readonly loading: boolean;
  readonly error?: string;
  readonly pageNumber: number;
  readonly pageSize: number;
  readonly search: string;
  readonly setPage: (page: number) => void;
  readonly setSearch: (search: string) => void;
  readonly refresh: () => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Module data could not be loaded.";
}

export function useModuleTable(
  service: ModuleRuntimeService,
  scope: ModuleRuntimeScope,
  tableSlug: string,
  pageSize = 20,
  chatsAll = false,
  filters?: Readonly<Record<string, unknown>>,
  enabled = true,
): ModuleTableState {
  const [page, setPageNumber] = useState(1);
  const [search, setSearchValue] = useState("");
  const [settledSearch, setSettledSearch] = useState("");
  const [epoch, setEpoch] = useState(0);
  const [state, setState] = useState<{ page?: ModulePage; loading: boolean; error?: string }>({ loading: true });

  useEffect(() => {
    const timeout = window.setTimeout(() => setSettledSearch(search.trim()), 250);
    return () => window.clearTimeout(timeout);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    if (!enabled) { setState({ loading: false }); return () => controller.abort(); }
    setState((current) => ({ ...current, loading: true, error: undefined }));
    const query: ModuleListQuery = { page, pageSize, search: settledSearch, ...(chatsAll ? { chats: "all" } : {}), ...(filters ? { filters } : {}) };
    void service.listRows(scope, tableSlug, query, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setState({ page: result, loading: false });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState((current) => ({ ...current, loading: false, error: errorMessage(error) }));
    });
    return () => controller.abort();
  }, [chatsAll, enabled, epoch, filters, page, pageSize, scope, service, settledSearch, tableSlug]);

  const setSearch = useCallback((value: string) => { setSearchValue(value); setPageNumber(1); }, []);
  const setPage = useCallback((value: number) => setPageNumber(Math.max(1, value)), []);
  const refresh = useCallback(() => setEpoch((value) => value + 1), []);
  return { ...state, pageNumber: page, pageSize, search, setPage, setSearch, refresh };
}

export function useModuleProfileRows(service: ModuleRuntimeService, scope: ModuleRuntimeScope, tableSlug: string | undefined, refreshKey = 0): { rows: ModulePage["items"]; loading: boolean; error?: string } {
  const [state, setState] = useState<{ rows: ModulePage["items"]; loading: boolean; error?: string }>({ rows: [], loading: true });
  useEffect(() => {
    if (!tableSlug) { setState({ rows: [], loading: false }); return undefined; }
    const controller = new AbortController();
    setState((current) => ({ ...current, loading: true, error: undefined }));
    void service.listRows(scope, tableSlug, { page: 1, pageSize: 200 }, { signal: controller.signal }).then((page) => {
      if (!controller.signal.aborted) setState({ rows: page.items, loading: false });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState((current) => ({ ...current, loading: false, error: errorMessage(error) }));
    });
    return () => controller.abort();
  }, [refreshKey, scope, service, tableSlug]);
  return state;
}

export interface AggregateRowsState {
  readonly rowsByTable: Readonly<Record<string, ModulePage["items"]>>;
  readonly loading: boolean;
  readonly error?: string;
}

export function aggregateSourceTables(view: ModuleViewDefinition): readonly string[] {
  const names = new Set<string>();
  if (Array.isArray(view.ui_json.columns)) {
    for (const column of view.ui_json.columns) {
      if (!column || typeof column.source !== "string") continue;
      const match = column.source.match(/^aggregate\.([a-z][a-z0-9_]{0,63})\.[a-z][a-z0-9_]{0,63}$/);
      if (match) names.add(match[1]);
    }
  }
  return [...names];
}

export async function listAllModuleRows(service: ModuleRuntimeService, scope: ModuleRuntimeScope, tableSlug: string, signal: AbortSignal, chatsAll = false): Promise<ModulePage["items"]> {
  const pageSize = 200;
  const maxPages = 100;
  const rows: ModulePage["items"][number][] = [];
  let total = 0;
  for (let pageNumber = 1; pageNumber <= maxPages; pageNumber += 1) {
    const result = await service.listRows(scope, tableSlug, { page: pageNumber, pageSize, ...(chatsAll ? { chats: "all" as const } : {}) }, { signal });
    rows.push(...result.items);
    total = result.total;
    if (rows.length >= total || result.items.length === 0) return rows;
  }
  if (rows.length < total) throw new Error(`Aggregate source '${tableSlug}' exceeds the ${pageSize * maxPages}-row limit.`);
  return rows;
}

export function useModuleAggregateRows(
  view: ModuleViewDefinition,
  manifest: ModuleManifest,
  service: ModuleRuntimeService,
  scope: ModuleRuntimeScope,
  enabled = true,
  refreshKey = 0,
): AggregateRowsState {
  const sourceTables = useMemo(() => aggregateSourceTables(view), [view]);
  const [state, setState] = useState<AggregateRowsState>({ rowsByTable: {}, loading: sourceTables.length > 0 && enabled });

  useEffect(() => {
    if (!enabled || sourceTables.length === 0) {
      setState({ rowsByTable: {}, loading: false });
      return undefined;
    }
    const controller = new AbortController();
    setState({ rowsByTable: {}, loading: true });
    void Promise.all(sourceTables.map(async (tableSlug) => {
      const table = manifest.tables.find((item) => item.slug === tableSlug);
      if (!table || table.enabled === false) throw new Error(`Aggregate source table '${tableSlug}' is missing or disabled.`);
      const rows = await listAllModuleRows(service, scope, tableSlug, controller.signal, table.scope?.chats === "all");
      return [tableSlug, rows] as const;
    })).then((entries) => {
      if (!controller.signal.aborted) setState({ rowsByTable: Object.fromEntries(entries), loading: false });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ rowsByTable: {}, loading: false, error: errorMessage(error) });
    });
    return () => controller.abort();
  }, [enabled, manifest.tables, refreshKey, scope, service, sourceTables]);

  return state;
}

export function aggregateListCount(
  manifest: ModuleManifest,
  rowsByTable: Readonly<Record<string, ModulePage["items"]>>,
  view: ModuleViewDefinition,
  row: ModuleRow,
  column: Record<string, unknown>,
): number | undefined {
  if (typeof column.source !== "string" || !view.table_slug) return undefined;
  const match = column.source.match(/^aggregate\.([a-z][a-z0-9_]{0,63})\.([a-z][a-z0-9_]{0,63})$/);
  if (!match) return undefined;
  const [, tableSlug, field] = match;
  const childColumns = manifest.columns.filter((item) => item.table_slug === tableSlug && item.name.endsWith("_id"));
  const foreignKey = typeof column.foreign_key === "string" ? column.foreign_key : childColumns.length === 1 ? childColumns[0].name : undefined;
  const joinField = typeof column.join_field === "string" ? column.join_field : "row_id";
  if (joinField !== "row_id" && !manifest.columns.some((item) => item.table_slug === view.table_slug && item.name === joinField)) return undefined;
  if (!foreignKey) return undefined;
  const joinValue = joinField === "row_id" ? row.row_id : row.body[joinField];
  if (joinValue === undefined || joinValue === null) return undefined;
  const rows = rowsByTable[tableSlug];
  if (!rows) return undefined;
  return rows.filter((item) => item.body[foreignKey] === joinValue).reduce((count, item) => {
    const value = item.body[field];
    return count + (Array.isArray(value) ? value.length : value === undefined || value === null ? 0 : 1);
  }, 0);
}
