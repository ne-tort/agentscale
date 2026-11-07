import { useId, useState, type FormEvent } from "react";
import type { DynamicModuleDefinition, DynamicModuleVersion } from "../../api/dynamic";
import { useI18n } from "../../i18n/useT";
import { Button, StatusBadge } from "../../shared/ui";
import { TextAreaField, TextField } from "../../shared/ui/forms";
import { CapabilityDeniedNotice, DynamicModuleErrorNotice, DynamicModulesEmpty } from "./screen-states";
import type { DynamicModuleAccess } from "./capability";
import {
  formatDateTime,
  manifestText,
  sortVersionsDesc,
  summarizeManifest,
  suggestNextVersion,
  versionStatusLabel,
  versionStatusTone,
  UNKNOWN_VALUE,
} from "./mapping";
import type { DynamicModulesService } from "./service";
import { useSubmitController } from "./hooks";
import {
  manifestDraftText,
  starterManifestText,
  validatePublishForm,
  type FieldErrors,
} from "./validation";

export interface VersionPanelProps {
  readonly service: DynamicModulesService;
  readonly access: DynamicModuleAccess;
  readonly definition: DynamicModuleDefinition;
  readonly versions: readonly DynamicModuleVersion[];
  readonly onChanged: () => void;
}

function ManifestViewer({ label, manifest }: { readonly label: string; readonly manifest: DynamicModuleVersion["manifest"] }) {
  const { t } = useI18n();
  // The version payload carries the digest but not the manifest body, so an
  // unread manifest is reported as absent rather than rendered as an empty object.
  if (manifest === undefined) {
    return <details className="dynamic-modules__manifest">
      <summary>{label}</summary>
      <div className="dynamic-modules__manifest-body">
        <p className="dynamic-modules__hint">{t("dynamicModules.manifestNotReported")}</p>
      </div>
    </details>;
  }
  const summary = summarizeManifest(manifest);
  return <details className="dynamic-modules__manifest">
    <summary>{label}</summary>
    <div className="dynamic-modules__manifest-body">
      <dl className="dynamic-modules__manifest-facts">
        <div><dt>{t("dynamicModules.manifestKind")}</dt><dd>{summary.kind}</dd></div>
        <div><dt>{t("dynamicModules.manifestSchema")}</dt><dd>{summary.schemaVersion}</dd></div>
        <div><dt>{t("dynamicModules.manifestAppId")}</dt><dd><code>{summary.appId}</code></dd></div>
        <div><dt>{t("dynamicModules.manifestVersion")}</dt><dd><code>{summary.version}</code></dd></div>
        <div><dt>{t("dynamicModules.manifestDigest")}</dt><dd><code>{summary.artifactDigest ?? t("dynamicModules.manifestDigestMissing")}</code></dd></div>
        <div><dt>{t("dynamicModules.manifestScreens")}</dt><dd>{summary.screenCount}</dd></div>
      </dl>
      <div className="dynamic-modules__manifest-columns">
        <section aria-label={t("dynamicModules.manifestRoutesAria")}>
          <h4>{t("dynamicModules.manifestRoutesHeading")}</h4>
          {summary.routes.length === 0
            ? <p className="dynamic-modules__hint">{t("dynamicModules.manifestRoutesEmpty")}</p>
            : <ul>{summary.routes.map((route) => <li key={route}><code>{route}</code></li>)}</ul>}
        </section>
        <section aria-label={t("dynamicModules.manifestCapabilitiesAria")}>
          <h4>{t("dynamicModules.manifestCapabilitiesHeading")}</h4>
          {summary.capabilities.length === 0
            ? <p className="dynamic-modules__hint">{t("dynamicModules.manifestCapabilitiesEmpty")}</p>
            : <ul>{summary.capabilities.map((capability) => <li key={`${capability.id}@${capability.version}`}><code>{capability.id}</code> v{capability.version}</li>)}</ul>}
        </section>
        <section aria-label={t("dynamicModules.manifestLimitsAria")}>
          <h4>{t("dynamicModules.manifestLimitsHeading")}</h4>
          {summary.limits.length === 0 && summary.dataPolicy.length === 0
            ? <p className="dynamic-modules__hint">{t("dynamicModules.manifestLimitsEmpty")}</p>
            : <ul>
              {summary.limits.map((limit) => <li key={`limit-${limit.key}`}><code>{limit.key}</code>: {limit.value}</li>)}
              {summary.dataPolicy.map((entry) => <li key={`policy-${entry.key}`}><code>{entry.key}</code>: {entry.value}</li>)}
            </ul>}
        </section>
      </div>
      <pre className="dynamic-modules__manifest-json" tabIndex={0}>{manifestText(manifest)}</pre>
      <p className="dynamic-modules__hint">
        {t("dynamicModules.manifestFooter")}
      </p>
    </div>
  </details>;
}

export function VersionPanel({ service, access, definition, versions, onChanged }: VersionPanelProps) {
  const { t, locale } = useI18n();
  const formId = useId();
  const ordered = sortVersionsDesc(versions);
  const latest = ordered[0];
  const [manifestText, setManifestText] = useState(() => (latest && latest.manifest ? manifestDraftText(latest.manifest) : starterManifestText({
    appId: definition.appId,
    ...(definition.kind === undefined ? {} : { kind: definition.kind }),
    version: suggestNextVersion(definition.latestVersion),
  })));
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [validationAttempted, setValidationAttempted] = useState(false);
  const submit = useSubmitController();
  const canPublish = access.canManage && definition.status !== "archived";

  const revalidate = (nextManifest: string, nextNotes: string) => {
    if (!validationAttempted) return;
    setErrors(validatePublishForm(
      { manifestText: nextManifest, notes: nextNotes, ...(definition.latestVersion ? { expectedVersion: definition.latestVersion } : {}) },
      { appId: definition.appId, ...(definition.kind === undefined ? {} : { kind: definition.kind }) },
      t,
    ));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationAttempted(true);
    const nextErrors = validatePublishForm(
      { manifestText, notes, ...(definition.latestVersion ? { expectedVersion: definition.latestVersion } : {}) },
      { appId: definition.appId, ...(definition.kind === undefined ? {} : { kind: definition.kind }) },
      t,
    );
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await submit.run(async () => {
      const published = await service.publishVersion({
        definitionId: definition.id,
        appId: definition.appId,
        ...(definition.kind === undefined ? {} : { kind: definition.kind }),
        values: {
          manifestText,
          notes,
          ...(definition.latestVersion ? { expectedVersion: definition.latestVersion } : {}),
        },
      });
      submit.succeed(t("dynamicModules.publishDone", {
        version: published.version,
        status: versionStatusLabel(published.status, t),
        digest: published.digest,
      }));
      onChanged();
    });
    if (ok) {
      setNotes("");
      setErrors({});
      setValidationAttempted(false);
    }
  }

  return <section className="dynamic-modules__panel" aria-labelledby={`${formId}-versions`}>
    <h2 id={`${formId}-versions`}>{t("dynamicModules.versionsHeading")}</h2>
    <p className="dynamic-modules__hint">
      {t("dynamicModules.versionsHint")}
    </p>

    {ordered.length === 0 && <DynamicModulesEmpty
      description={t("dynamicModules.versionsEmptyDescription")}
      title={t("dynamicModules.versionsEmptyTitle")}
    />}

    {ordered.length > 0 && <div className="ui-table-scroll" role="region" aria-label={t("dynamicModules.versionsTableAria")} tabIndex={0}>
      <table className="ui-table dynamic-modules__table">
        <caption>{t("dynamicModules.versionsCaption", { name: definition.displayName })}</caption>
        <thead>
          <tr>
            <th scope="col">{t("dynamicModules.colVersion")}</th>
            <th scope="col">{t("dynamicModules.colStatus")}</th>
            <th scope="col">{t("dynamicModules.colDigest")}</th>
            <th scope="col">{t("dynamicModules.colCreated")}</th>
            <th scope="col">{t("dynamicModules.colPublished")}</th>
            <th scope="col">{t("dynamicModules.colNotes")}</th>
            <th scope="col">{t("dynamicModules.colManifest")}</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((version) => <tr key={version.id}>
            <th scope="row"><code>{version.version}</code>{version.version === definition.latestVersion && <span className="dynamic-modules__latest-tag">{t("dynamicModules.latestTag")}</span>}</th>
            <td><StatusBadge variant={versionStatusTone(version.status)}>{versionStatusLabel(version.status, t)}</StatusBadge></td>
            <td><code className="dynamic-modules__digest">{version.digest}</code></td>
            <td>{formatDateTime(version.createdAt, locale)}</td>
            <td>{formatDateTime(version.publishedAt, locale)}</td>
            <td>{version.notes ?? UNKNOWN_VALUE}</td>
            <td><ManifestViewer label={t("dynamicModules.manifestViewerLabel", { version: version.version })} manifest={version.manifest} /></td>
          </tr>)}
        </tbody>
      </table>
    </div>}

    {!access.canManage
      ? <CapabilityDeniedNotice missing={access.missing} operation="manage">
        <p className="dynamic-modules__hint">{t("dynamicModules.publishDeniedHint")}</p>
      </CapabilityDeniedNotice>
      : definition.status === "archived"
        ? <p className="dynamic-modules__hint" role="status">{t("dynamicModules.publishArchived")}</p>
        : <form className="dynamic-modules__form" noValidate onSubmit={(event) => void handleSubmit(event)}>
          <h3>{t("dynamicModules.publishHeading")}</h3>
          <TextAreaField
            className="dynamic-modules__manifest-input"
            error={errors.manifest}
            hint={t("dynamicModules.manifestHint")}
            id={`${formId}-manifest`}
            label={t("dynamicModules.manifestLabel")}
            onChange={(event) => { setManifestText(event.currentTarget.value); revalidate(event.currentTarget.value, notes); }}
            required
            rows={16}
            spellCheck={false}
            value={manifestText}
          />
          <TextField
            error={errors.notes}
            hint={t("dynamicModules.notesHint", { version: definition.latestVersion ?? UNKNOWN_VALUE })}
            id={`${formId}-notes`}
            label={t("dynamicModules.notesLabel")}
            maxLength={2000}
            onChange={(event) => { setNotes(event.currentTarget.value); revalidate(manifestText, event.currentTarget.value); }}
            value={notes}
          />
          {submit.error && <DynamicModuleErrorNotice error={submit.error} />}
          {submit.notice && <p className="dynamic-modules__notice" role="status">{submit.notice}</p>}
          <div className="dynamic-modules__actions">
            <Button disabled={submit.busy} type="submit" variant="primary">{submit.busy ? t("dynamicModules.publishing") : t("dynamicModules.publishSubmit")}</Button>
            <Button
              disabled={submit.busy}
              onClick={() => {
                setManifestText(latest ? manifestDraftText(latest.manifest) : starterManifestText({
                  appId: definition.appId,
                  ...(definition.kind === undefined ? {} : { kind: definition.kind }),
                  version: suggestNextVersion(definition.latestVersion),
                }));
                setNotes("");
                setErrors({});
                setValidationAttempted(false);
                submit.reset();
              }}
              variant="quiet"
            >{t("dynamicModules.resetForm")}</Button>
          </div>
        </form>}
  </section>;
}
