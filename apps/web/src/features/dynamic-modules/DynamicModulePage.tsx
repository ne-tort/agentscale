import { useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../../app/context";
import { describeDynamicModulesApi } from "../../api/dynamic";
import { useI18n } from "../../i18n/useT";
import { Breadcrumbs, Button, StatusBadge } from "../../shared/ui";
import { ConfirmationDialog, TextAreaField } from "../../shared/ui/forms/index";
import { useDynamicModuleAccess } from "./access";
import type { DynamicModuleAccess } from "./capability";
import { useResource, useSubmitController } from "./hooks";
import {
  definitionKindLabel,
  definitionStatusLabel,
  definitionStatusTone,
  definitionSubtitle,
  formatDateTime,
  isArchived,
  UNKNOWN_VALUE,
} from "./mapping";
import { createDynamicModulesService, DynamicModuleServiceError, type DynamicModulesService } from "./service";
import { GrantPanel } from "./GrantPanel";
import { InstallationPanel } from "./InstallationPanel";
import { CapabilityDeniedNotice, DynamicModuleErrorNotice, DynamicModulesApiNotice, DynamicModulesLoading } from "./screen-states";
import { VersionPanel } from "./VersionPanel";
import { validateArchiveForm, type FieldErrors } from "./validation";
import "./dynamic-modules.css";

/** Same reason-code rule as revocation: codes, not free text, reach the audit trail. */
export const ARCHIVE_REASON_CODE = "archived_from_registry_screen";

export function DynamicModulePage() {
  const { definitionId = "" } = useParams();
  const api = useApi();
  const access = useDynamicModuleAccess();
  const { t } = useI18n();
  const availability = useMemo(() => describeDynamicModulesApi(api), [api]);
  const service = useMemo(() => createDynamicModulesService({ api, access, translate: t }), [api, access, t]);
  return <ManageDynamicModule
    access={access}
    availability={availability}
    definitionId={definitionId}
    service={service}
  />;
}

export interface ManageDynamicModuleProps {
  readonly service: DynamicModulesService;
  readonly access: DynamicModuleAccess;
  readonly availability?: { readonly available: boolean; readonly missing: readonly string[] };
  readonly definitionId: string;
}

export function ManageDynamicModule({
  service,
  access,
  availability = { available: true, missing: [] },
  definitionId,
}: ManageDynamicModuleProps) {
  const { t, locale, plural } = useI18n();
  const [revision, setRevision] = useState(0);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveReason, setArchiveReason] = useState("");
  const [archiveErrors, setArchiveErrors] = useState<FieldErrors>({});
  const archive = useSubmitController();
  const enabled = access.canRead && availability.available && definitionId.length > 0;

  const detail = useResource(
    (signal) => service.loadDefinition(definitionId, { signal }),
    `detail:${definitionId}:${revision}`,
    { enabled },
  );
  const instances = useResource(
    (signal) => service.listInstances(definitionId, { signal }),
    `instances:${definitionId}:${revision}`,
    { enabled: enabled && detail.state.status === "ready" },
  );
  const grants = useResource(
    (signal) => service.listGrants(definitionId, { signal }),
    `grants:${definitionId}:${revision}`,
    { enabled: enabled && detail.state.status === "ready" },
  );

  const refresh = () => setRevision((value) => value + 1);

  if (!definitionId) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: t("dynamicModules.title"), href: "/dynamic-modules" }, { label: t("dynamicModules.definitionHeading") }]} label={t("dynamicModules.breadcrumb")} />
      <h1 className="dynamic-modules__title">{t("dynamicModules.definitionUnavailable")}</h1>
      <DynamicModuleErrorNotice error={new DynamicModuleServiceError(
        "DYNAMIC_MODULES_VALIDATION_FAILED",
        t("dynamicModules.noDefinitionId"),
      )} />
      <p><Link to="/dynamic-modules">{t("dynamicModules.backToDefinitions")}</Link></p>
    </div></main>;
  }

  if (!access.canRead) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: t("dynamicModules.title"), href: "/dynamic-modules" }, { label: definitionId }]} label={t("dynamicModules.breadcrumb")} />
      <h1 className="dynamic-modules__title">{t("dynamicModules.definitionHeading")}</h1>
      <CapabilityDeniedNotice missing={access.missing} operation="read" />
    </div></main>;
  }

  if (!availability.available) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: t("dynamicModules.title"), href: "/dynamic-modules" }, { label: definitionId }]} label={t("dynamicModules.breadcrumb")} />
      <h1 className="dynamic-modules__title">{t("dynamicModules.definitionHeading")}</h1>
      <DynamicModulesApiNotice missing={availability.missing} />
    </div></main>;
  }

  if (detail.state.status === "loading") {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: t("dynamicModules.title"), href: "/dynamic-modules" }, { label: definitionId }]} label={t("dynamicModules.breadcrumb")} />
      <DynamicModulesLoading label={t("dynamicModules.loadingDefinition")} />
    </div></main>;
  }

  if (detail.state.status === "error") {
    const notFound = detail.state.error.status === 404;
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: t("dynamicModules.title"), href: "/dynamic-modules" }, { label: definitionId }]} label={t("dynamicModules.breadcrumb")} />
      <h1 className="dynamic-modules__title">{notFound ? t("dynamicModules.definitionNotFound") : t("dynamicModules.definitionUnavailable")}</h1>
      <DynamicModuleErrorNotice error={detail.state.error} onRetry={detail.reload} retryLabel={t("dynamicModules.reloadDefinition")} />
      <p><Link to="/dynamic-modules">{t("dynamicModules.backToDefinitions")}</Link></p>
    </div></main>;
  }

  const { definition, versions } = detail.state.data;
  const archived = isArchived(definition);

  async function confirmArchive() {
    const nextErrors = validateArchiveForm({ reason: archiveReason }, t);
    setArchiveErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await archive.run(async () => {
      const updated = await service.archiveDefinition({
        definitionId: definition.id,
        values: { reason: archiveReason.trim() || ARCHIVE_REASON_CODE },
        ...(definition.latestVersion ? { expectedVersion: definition.latestVersion } : {}),
      });
      archive.succeed(t("dynamicModules.archiveDone", { status: definitionStatusLabel(updated.status, t) }));
      refresh();
    });
    if (ok) {
      setArchiveOpen(false);
      setArchiveReason("");
    }
  }

  return <main className="dynamic-modules" aria-labelledby="dynamic-module-title">
    <div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: t("dynamicModules.title"), href: "/dynamic-modules" }, { label: definition.displayName }]} label={t("dynamicModules.breadcrumb")} />
      <header className="dynamic-modules__header">
        <div>
          <p className="dynamic-modules__eyebrow" title={definition.id}>{t("dynamicModules.definitionHeading")}</p>
          <h1 className="dynamic-modules__title" id="dynamic-module-title">{definition.displayName}</h1>
          <p className="dynamic-modules__subtitle">{definitionSubtitle(definition, t, plural)}</p>
          {definition.description && <p className="dynamic-modules__description">{definition.description}</p>}
        </div>
        <div className="dynamic-modules__header-actions">
          <StatusBadge variant={definitionStatusTone(definition.status)}>{definitionStatusLabel(definition.status, t)}</StatusBadge>
          <Button onClick={refresh} variant="secondary">{t("dynamicModules.refresh")}</Button>
          {access.canManage && !archived && <Button onClick={() => { archive.reset(); setArchiveErrors({}); setArchiveOpen(true); }} variant="danger">{t("dynamicModules.archiveAction")}</Button>}
        </div>
      </header>

      {archived && <p className="dynamic-modules__notice dynamic-modules__notice--warning" role="status">
        {definition.archivedAt
          ? t("dynamicModules.archivedNoticeSince", { date: formatDateTime(definition.archivedAt, locale) })
          : t("dynamicModules.archivedNotice")}
      </p>}
      {archive.error && <DynamicModuleErrorNotice error={archive.error} />}
      {archive.notice && <p className="dynamic-modules__notice" role="status">{archive.notice}</p>}

      <dl className="dynamic-modules__facts">
        <div><dt>{t("dynamicModules.factAppId")}</dt><dd><code>{definition.appId}</code></dd></div>
        <div><dt>{t("dynamicModules.factKind")}</dt><dd>{definitionKindLabel(definition.kind, t)}</dd></div>
        <div><dt>{t("dynamicModules.factLatestVersion")}</dt><dd><code>{definition.latestVersion ?? UNKNOWN_VALUE}</code></dd></div>
        <div><dt>{t("dynamicModules.factCreated")}</dt><dd>{formatDateTime(definition.createdAt, locale)}</dd></div>
        <div><dt>{t("dynamicModules.factUpdated")}</dt><dd>{formatDateTime(definition.updatedAt, locale)}</dd></div>
        <div><dt>{t("dynamicModules.factCreatedBy")}</dt><dd>{definition.createdBy ?? UNKNOWN_VALUE}</dd></div>
      </dl>

      <section className="dynamic-modules__panel" aria-label={t("dynamicModules.routesAria")}>
        <h2>{t("dynamicModules.routesHeading")}</h2>
        {definition.routes === undefined
          ? <p className="dynamic-modules__hint">{t("dynamicModules.routesNotReported")}</p>
          : definition.routes.length === 0
            ? <p className="dynamic-modules__hint">{t("dynamicModules.routesEmpty")}</p>
            : <ul className="dynamic-modules__route-list">
              {definition.routes.map((route) => <li key={route.path}><code>{route.path}</code><span>{t("dynamicModules.routeScreen", { screen: route.screenId })}</span></li>)}
            </ul>}
      </section>

      <VersionPanel
        access={access}
        definition={definition}
        onChanged={refresh}
        service={service}
        versions={versions}
      />

      <InstallationPanel
        access={access}
        definition={definition}
        {...(instances.state.status === "error" ? { error: instances.state.error } : {})}
        instances={instances.state.data ?? []}
        loading={instances.state.status === "loading"}
        onChanged={refresh}
        onReload={instances.reload}
        service={service}
        versions={versions}
      />

      <GrantPanel
        access={access}
        definition={definition}
        {...(grants.state.status === "error" ? { error: grants.state.error } : {})}
        grants={grants.state.data ?? []}
        loading={grants.state.status === "loading"}
        onChanged={refresh}
        onReload={grants.reload}
        service={service}
      />

      <ConfirmationDialog
        busy={archive.busy}
        confirmLabel={t("dynamicModules.archiveConfirmLabel")}
        description={t("dynamicModules.archiveDescription")}
        onCancel={() => { if (!archive.busy) setArchiveOpen(false); }}
        onConfirm={() => void confirmArchive()}
        open={archiveOpen}
        title={t("dynamicModules.archiveTitle", { name: definition.displayName })}
      >
        <form className="dynamic-modules__form" noValidate onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void confirmArchive(); }}>
          <TextAreaField
            error={archiveErrors.reason}
            hint={t("dynamicModules.archiveReasonHint", { code: ARCHIVE_REASON_CODE })}
            id="dynamic-modules-archive-reason"
            label={t("dynamicModules.archiveReasonLabel")}
            maxLength={64}
            onChange={(event) => setArchiveReason(event.currentTarget.value)}
            rows={2}
            value={archiveReason}
          />
        </form>
      </ConfirmationDialog>
    </div>
  </main>;
}
