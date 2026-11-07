import { useEffect, useId, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../../app/context";
import {
  DYNAMIC_MODULE_STATUSES,
  describeDynamicModulesApi,
  type DynamicModuleDefinitionSummary,
  type DynamicModuleStatus,
} from "../../api/dynamic";
import { useI18n } from "../../i18n/useT";
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

export function DynamicModulesScreen({
  service,
  access,
  availability = { available: true, missing: [] },
  initialSearch = "",
  initialStatus,
}: DynamicModulesScreenProps) {
  const { t, locale, plural } = useI18n();
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
  const statusOptions = DYNAMIC_MODULE_STATUSES.map((value) => ({ value, label: definitionStatusLabel(value, t) }));

  return <main className="dynamic-modules" aria-labelledby={titleId}>
    <div className="dynamic-modules__inner">
      <Breadcrumbs
        items={[{ label: t("dynamicModules.breadcrumbWorkspace"), href: "/projects" }, { label: t("dynamicModules.title") }]}
        label={t("dynamicModules.breadcrumb")}
      />
      <DynamicModulesHeader
        busy={loading}
        onRefresh={reload}
        titleId={titleId}
      />

      <TableToolbar
        label={t("dynamicModules.toolbarLabel")}
        searchLabel={t("dynamicModules.searchLabel")}
        placeholder={t("dynamicModules.searchPlaceholder")}
        query={searchInput}
        onQueryChange={setSearchInput}
        resultCount={state.status === "ready" ? items.length : undefined}
        resultLabel={(count) => plural("dynamicModules.resultCount", count)}
      >
        <SelectField
          className="dynamic-modules__status-filter"
          id="dynamic-modules-status-filter"
          label={t("dynamicModules.statusFilterLabel")}
          onValueChange={(value) => setStatus((value ?? "") as DynamicModuleStatus | "")}
          options={statusOptions}
          placeholder={t("dynamicModules.statusFilterAll")}
          value={status}
        />
      </TableToolbar>

      {state.status === "error" && <DynamicModuleErrorNotice error={state.error} onRetry={reload} retryLabel={t("dynamicModules.reloadDefinitions")} />}

      {loading && <DynamicModulesLoading />}

      {!loading && state.status === "ready" && items.length === 0 && <DynamicModulesEmpty
        description={hasFilter
          ? t("dynamicModules.emptyFilteredDescription")
          : t("dynamicModules.emptyDescription")}
        title={hasFilter ? t("dynamicModules.emptyFilteredTitle") : t("dynamicModules.emptyTitle")}
      />}

      {!loading && items.length > 0 && <section className="dynamic-modules__table-section" aria-label={t("dynamicModules.tableAria")}>
        <table className="ui-table dynamic-modules__table">
          <caption>{t("dynamicModules.tableCaption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("dynamicModules.colDefinition")}</th>
              <th scope="col">{t("dynamicModules.colKind")}</th>
              <th scope="col">{t("dynamicModules.colStatus")}</th>
              <th scope="col">{t("dynamicModules.colLatestVersion")}</th>
              <th scope="col">{t("dynamicModules.colVersions")}</th>
              <th scope="col">{t("dynamicModules.colCapabilities")}</th>
              <th scope="col">{t("dynamicModules.colUpdated")}</th>
              <th scope="col"><span className="dynamic-modules__sr-only">{t("dynamicModules.colOpen")}</span></th>
            </tr>
          </thead>
          <tbody>
            {items.map((definition) => <tr key={definition.id}>
              <th scope="row" className="dynamic-modules__name-cell">
                <Link className="dynamic-modules__name-link" to={definitionHref(definition.id)}>{definition.displayName}</Link>
                <code className="dynamic-modules__app-id">{definition.appId}</code>
              </th>
              <td>{definition.kind === "declarative-app" ? t("dynamicModules.kindDeclarativeApp") : t("dynamicModules.kindIsolatedPlugin")}</td>
              <td><StatusBadge variant={definitionStatusTone(definition.status)}>{definitionStatusLabel(definition.status, t)}</StatusBadge></td>
              <td><code>{definition.latestVersion ?? UNKNOWN_VALUE}</code></td>
              <td>{definition.versionCount}</td>
              <td>{definition.requestedCapabilityCount}</td>
              <td>{formatDateTime(definition.updatedAt, locale)}</td>
              <td className="dynamic-modules__row-action">
                <Link aria-label={t("dynamicModules.openDefinitionAria", { name: definition.displayName })} className="ui-button ui-button--secondary ui-button--small" to={definitionHref(definition.id)}>{t("dynamicModules.colOpen")}</Link>
                <span className="dynamic-modules__row-subtitle">{definitionSubtitle(definition, t, plural)}</span>
              </td>
            </tr>)}
          </tbody>
        </table>
        {state.data?.nextCursor && <p className="dynamic-modules__hint" role="status">
          {t("dynamicModules.nextPageHint", { limit: LIST_LIMIT })}
        </p>}
      </section>}
    </div>
  </main>;
}

function DynamicModulesHeader({ titleId, onRefresh, busy }: { readonly titleId: string; readonly onRefresh?: () => void; readonly busy: boolean }) {
  const { t } = useI18n();
  return <header className="dynamic-modules__header">
    <div>
      <p className="dynamic-modules__eyebrow">{t("dynamicModules.eyebrow")}</p>
      <h1 id={titleId}>{t("dynamicModules.title")}</h1>
      <p className="dynamic-modules__subtitle">
        {t("dynamicModules.subtitle")}. {t("dynamicModules.subtitleDetail")}
      </p>
    </div>
    {onRefresh && <div className="dynamic-modules__header-actions">
      <Button loading={busy} onClick={onRefresh} variant="secondary">{busy ? t("dynamicModules.refreshing") : t("dynamicModules.refresh")}</Button>
    </div>}
  </header>;
}

/** Route-level container: resolves the shell API client and the effective grant. */
export function DynamicModulesPage() {
  const api = useApi();
  const access = useDynamicModuleAccess();
  const { t } = useI18n();
  const availability = useMemo(() => describeDynamicModulesApi(api), [api]);
  const service = useMemo(() => createDynamicModulesService({ api, access, translate: t }), [api, access, t]);
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
