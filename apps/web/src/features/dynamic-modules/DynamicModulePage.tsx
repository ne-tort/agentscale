import { useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../../app/context";
import { describeDynamicModulesApi } from "../../api/dynamic";
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
  ROUTES_UNAVAILABLE,
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
  const availability = useMemo(() => describeDynamicModulesApi(api), [api]);
  const service = useMemo(() => createDynamicModulesService({ api, access }), [api, access]);
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
      <Breadcrumbs items={[{ label: "Dynamic modules", href: "/dynamic-modules" }, { label: "Definition" }]} />
      <h1 className="dynamic-modules__title">Definition unavailable</h1>
      <DynamicModuleErrorNotice error={new DynamicModuleServiceError(
        "DYNAMIC_MODULES_VALIDATION_FAILED",
        "This route carries no definition id, so no registry lookup was attempted.",
      )} />
      <p><Link to="/dynamic-modules">Back to definitions</Link></p>
    </div></main>;
  }

  if (!access.canRead) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: "Dynamic modules", href: "/dynamic-modules" }, { label: definitionId }]} />
      <h1 className="dynamic-modules__title">Definition</h1>
      <CapabilityDeniedNotice missing={access.missing} operation="read" />
    </div></main>;
  }

  if (!availability.available) {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: "Dynamic modules", href: "/dynamic-modules" }, { label: definitionId }]} />
      <h1 className="dynamic-modules__title">Definition</h1>
      <DynamicModulesApiNotice missing={availability.missing} />
    </div></main>;
  }

  if (detail.state.status === "loading") {
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: "Dynamic modules", href: "/dynamic-modules" }, { label: definitionId }]} />
      <DynamicModulesLoading label="Loading definition, versions and grants…" />
    </div></main>;
  }

  if (detail.state.status === "error") {
    const notFound = detail.state.error.status === 404;
    return <main className="dynamic-modules"><div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: "Dynamic modules", href: "/dynamic-modules" }, { label: definitionId }]} />
      <h1 className="dynamic-modules__title">{notFound ? "Definition not found" : "Definition unavailable"}</h1>
      <DynamicModuleErrorNotice error={detail.state.error} onRetry={detail.reload} retryLabel="Reload definition" />
      <p><Link to="/dynamic-modules">Back to definitions</Link></p>
    </div></main>;
  }

  const { definition, versions } = detail.state.data;
  const archived = isArchived(definition);

  async function confirmArchive() {
    const nextErrors = validateArchiveForm({ reason: archiveReason });
    setArchiveErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await archive.run(async () => {
      const updated = await service.archiveDefinition({
        definitionId: definition.id,
        values: { reason: archiveReason.trim() || ARCHIVE_REASON_CODE },
        ...(definition.latestVersion ? { expectedVersion: definition.latestVersion } : {}),
      });
      archive.succeed(`Definition archived (${updated.status}). New installations are denied and active instances drain.`);
      refresh();
    });
    if (ok) {
      setArchiveOpen(false);
      setArchiveReason("");
    }
  }

  return <main className="dynamic-modules" aria-labelledby="dynamic-module-title">
    <div className="dynamic-modules__inner">
      <Breadcrumbs items={[{ label: "Dynamic modules", href: "/dynamic-modules" }, { label: definition.displayName }]} />
      <header className="dynamic-modules__header">
        <div>
          <p className="dynamic-modules__eyebrow">Definition · {definition.id}</p>
          <h1 className="dynamic-modules__title" id="dynamic-module-title">{definition.displayName}</h1>
          <p className="dynamic-modules__subtitle">{definitionSubtitle(definition)}</p>
          {definition.description && <p className="dynamic-modules__description">{definition.description}</p>}
        </div>
        <div className="dynamic-modules__header-actions">
          <StatusBadge variant={definitionStatusTone(definition.status)}>{definitionStatusLabel(definition.status)}</StatusBadge>
          <Button onClick={refresh} variant="secondary">Refresh</Button>
          {access.canManage && !archived && <Button onClick={() => { archive.reset(); setArchiveErrors({}); setArchiveOpen(true); }} variant="danger">Archive</Button>}
        </div>
      </header>

      {archived && <p className="dynamic-modules__notice dynamic-modules__notice--warning" role="status">
        This definition is archived{definition.archivedAt ? ` since ${formatDateTime(definition.archivedAt)}` : ""}. New
        installations are denied; existing instances drain. Archived definitions are immutable and read-only here.
      </p>}
      {archive.error && <DynamicModuleErrorNotice error={archive.error} />}
      {archive.notice && <p className="dynamic-modules__notice" role="status">{archive.notice}</p>}

      <dl className="dynamic-modules__facts">
        <div><dt>App id</dt><dd><code>{definition.appId}</code></dd></div>
        <div><dt>Kind</dt><dd>{definitionKindLabel(definition.kind)}</dd></div>
        <div><dt>Latest version</dt><dd><code>{definition.latestVersion ?? UNKNOWN_VALUE}</code></dd></div>
        <div><dt>Created</dt><dd>{formatDateTime(definition.createdAt)}</dd></div>
        <div><dt>Updated</dt><dd>{formatDateTime(definition.updatedAt)}</dd></div>
        <div><dt>Created by</dt><dd>{definition.createdBy ?? UNKNOWN_VALUE}</dd></div>
      </dl>

      <section className="dynamic-modules__panel" aria-label="Definition routes">
        <h2>Registered routes</h2>
        {definition.routes === undefined
          ? <p className="dynamic-modules__hint">{ROUTES_UNAVAILABLE}</p>
          : definition.routes.length === 0
            ? <p className="dynamic-modules__hint">This definition declares no routes, so it cannot be opened from the shell navigation.</p>
            : <ul className="dynamic-modules__route-list">
              {definition.routes.map((route) => <li key={route.path}><code>{route.path}</code><span>screen {route.screenId}</span></li>)}
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
        confirmLabel="Archive definition"
        description="Archiving is a server operation with an audit trail: new installations are denied, active instances drain, and published versions stay immutable. This cannot be undone from this screen."
        onCancel={() => { if (!archive.busy) setArchiveOpen(false); }}
        onConfirm={() => void confirmArchive()}
        open={archiveOpen}
        title={`Archive ${definition.displayName}?`}
      >
        <form className="dynamic-modules__form" noValidate onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void confirmArchive(); }}>
          <TextAreaField
            error={archiveErrors.reason}
            hint={`Optional audit reason code (a-z, 0-9, underscore). Empty records ${ARCHIVE_REASON_CODE}. Free text is not sent: audit records stay redactable.`}
            id="dynamic-modules-archive-reason"
            label="Archive reason code"
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
