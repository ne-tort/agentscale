import { useEffect, useId, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../../app/context";
import {
  DYNAMIC_MODULE_STATUSES,
  describeDynamicModulesApi,
  type DynamicModuleDefinitionSummary,
  type DynamicModuleStatus,
} from "../../api/dynamic";
import { Breadcrumbs, Button, StatusBadge, TableToolbar } from "../../shared/ui";
import { SelectField } from "../../shared/ui/forms/index";
import { useDynamicModuleAccess } from "./access";
import type { DynamicModuleAccess } from "./capability";
import {
  definitionStatusLabel,
  definitionStatusTone,
  definitionSubtitle,
  formatDateTime,
  UNKNOWN_VALUE,
} from "./mapping";
import { createDynamicModulesService, type DynamicModulesService } from "./service";
import { useResource } from "./hooks";
import {
  CapabilityDeniedNotice,
  DynamicModuleErrorNotice,
  DynamicModulesApiNotice,
  DynamicModulesEmpty,
  DynamicModulesLoading,
} from "./screen-states";
import "./dynamic-modules.css";

const LIST_LIMIT = 100;
const SEARCH_DEBOUNCE_MS = 250;

export interface DynamicModulesAvailability {
  readonly available: boolean;
  readonly missing: readonly string[];
}

export interface DynamicModulesScreenProps {
  readonly service: DynamicModulesService;
  readonly access: DynamicModuleAccess;
  readonly availability?: DynamicModulesAvailability;
  readonly initialSearch?: string;
  readonly initialStatus?: DynamicModuleStatus;
}

export function definitionHref(definitionId: string): string {
  return `/dynamic-modules/${encodeURIComponent(definitionId)}`;
}

function statusOptions(): ReadonlyArray<{ readonly value: DynamicModuleStatus; readonly label: string }> {
  return DYNAMIC_MODULE_STATUSES.map((status) => ({ value: status, label: definitionStatusLabel(status) }));
}

export function DynamicModulesScreen({
  service,
  access,
  availability = { available: true, missing: [] },
  initialSearch = "",
  initialStatus,
}: DynamicModulesScreenProps) {
  const titleId = useId();
  const [searchInput, setSearchInput] = useState(initialSearch);
  const [search, setSearch] = useState(initialSearch);
  const [status, setStatus] = useState<DynamicModuleStatus | "">(initialStatus ?? "");

  useEffect(() => {
    const handle = window.setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  const enabled = access.canRead && availability.available;
  const { state, reload } = useResource(
    (signal) => service.listDefinitions({
      ...(search ? { search } : {}),
      ...(status ? { status } : {}),
      limit: LIST_LIMIT,
    }, { signal }),
    `list:${search}:${status}`,
    { enabled },
  );

  if (!access.canRead) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <DynamicModulesHeader titleId={titleId} onRefresh={undefined} busy={false} />
      <CapabilityDeniedNotice missing={access.missing} operation="read" />
    </div></main>;
  }

  if (!availability.available) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <DynamicModulesHeader titleId={titleId} onRefresh={undefined} busy={false} />
      <DynamicModulesApiNotice missing={availability.missing} />
    </div></main>;
  }

  const items: readonly DynamicModuleDefinitionSummary[] = state.data?.items ?? [];
  const hasFilter = search.length > 0 || status !== "";
  const loading = state.status === "loading";

  return <main className="dynamic-modules" aria-labelledby={titleId}>
    <div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: "Workspace", href: "/projects" }, { label: "Dynamic modules" }]} />
      <DynamicModulesHeader
        busy={loading}
        onRefresh={reload}
        titleId={titleId}
      />

      <TableToolbar
        label="Dynamic module definitions"
        placeholder="Search by name or app id"
        query={searchInput}
        onQueryChange={setSearchInput}
        resultCount={state.status === "ready" ? items.length : undefined}
        resultLabel={(count) => `${count} ${count === 1 ? "definition" : "definitions"}`}
      >
        <SelectField
          className="dynamic-modules__status-filter"
          id="dynamic-modules-status-filter"
          label="Status"
          onValueChange={(value) => setStatus((value ?? "") as DynamicModuleStatus | "")}
          options={statusOptions()}
          placeholder="All statuses"
          value={status}
        />
      </TableToolbar>

      {state.status === "error" && <DynamicModuleErrorNotice error={state.error} onRetry={reload} retryLabel="Reload definitions" />}

      {loading && <DynamicModulesLoading />}

      {!loading && state.status === "ready" && items.length === 0 && <DynamicModulesEmpty
        description={hasFilter
          ? "No definition matches the current search and status filter. Clear the filters to see the whole registry page."
          : "Publish a manifest version to register the first definition. Requested capabilities become a grant only after an explicit decision."}
        title={hasFilter ? "No matching definitions" : "No dynamic module definitions yet"}
      />}

      {!loading && items.length > 0 && <section className="dynamic-modules__table-section" aria-label="Dynamic module definitions">
        <table className="ui-table dynamic-modules__table">
          <caption>Dynamic module definitions visible in this tenant scope</caption>
          <thead>
            <tr>
              <th scope="col">Definition</th>
              <th scope="col">Kind</th>
              <th scope="col">Status</th>
              <th scope="col">Latest version</th>
              <th scope="col">Versions</th>
              <th scope="col">Capabilities</th>
              <th scope="col">Updated</th>
              <th scope="col"><span className="dynamic-modules__sr-only">Open</span></th>
            </tr>
          </thead>
          <tbody>
            {items.map((definition) => <tr key={definition.id}>
              <th scope="row" className="dynamic-modules__name-cell">
                <Link className="dynamic-modules__name-link" to={definitionHref(definition.id)}>{definition.displayName}</Link>
                <code className="dynamic-modules__app-id">{definition.appId}</code>
              </th>
              <td>{definition.kind === "declarative-app" ? "Declarative app" : "Isolated plugin"}</td>
              <td><StatusBadge variant={definitionStatusTone(definition.status)}>{definitionStatusLabel(definition.status)}</StatusBadge></td>
              <td><code>{definition.latestVersion ?? UNKNOWN_VALUE}</code></td>
              <td>{definition.versionCount}</td>
              <td>{definition.requestedCapabilityCount}</td>
              <td>{formatDateTime(definition.updatedAt)}</td>
              <td className="dynamic-modules__row-action">
                <Link aria-label={`Open definition ${definition.displayName}`} className="ui-button ui-button--secondary ui-button--small" to={definitionHref(definition.id)}>Open</Link>
                <span className="dynamic-modules__row-subtitle">{definitionSubtitle(definition)}</span>
              </td>
            </tr>)}
          </tbody>
        </table>
        {state.data?.nextCursor && <p className="dynamic-modules__hint" role="status">
          Showing the first {LIST_LIMIT} definitions. Narrow the search or status filter to reach the rest;
          this screen does not claim a complete registry snapshot.
        </p>}
      </section>}
    </div>
  </main>;
}

function DynamicModulesHeader({ titleId, onRefresh, busy }: { readonly titleId: string; readonly onRefresh?: () => void; readonly busy: boolean }) {
  return <header className="dynamic-modules__header">
    <div>
      <p className="dynamic-modules__eyebrow">Platform registry</p>
      <h1 id={titleId}>Dynamic modules</h1>
      <p className="dynamic-modules__subtitle">
        Declarative app and isolated plugin definitions, their immutable versions, installations
        and capability grants. Published artifacts are immutable; edits create a new version.
      </p>
    </div>
    {onRefresh && <div className="dynamic-modules__header-actions">
      <Button loading={busy} onClick={onRefresh} variant="secondary">{busy ? "Refreshing…" : "Refresh"}</Button>
    </div>}
  </header>;
}

/** Route-level container: resolves the shell API client and the effective grant. */
export function DynamicModulesPage() {
  const api = useApi();
  const access = useDynamicModuleAccess();
  const availability = useMemo(() => describeDynamicModulesApi(api), [api]);
  const service = useMemo(() => createDynamicModulesService({ api, access }), [api, access]);
  const [searchParams] = useSearchParams();
  const statusParam = searchParams.get("status");
  const initialStatus = statusParam && (DYNAMIC_MODULE_STATUSES as readonly string[]).includes(statusParam)
    ? statusParam as DynamicModuleStatus
    : undefined;
  return <DynamicModulesScreen
    access={access}
    availability={availability}
    initialSearch={searchParams.get("q") ?? ""}
    {...(initialStatus ? { initialStatus } : {})}
    service={service}
  />;
}
