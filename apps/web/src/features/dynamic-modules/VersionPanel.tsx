import { useId, useState, type FormEvent } from "react";
import type { DynamicModuleDefinition, DynamicModuleVersion } from "../../api/dynamic";
import { Button, StatusBadge } from "../../shared/ui";
import { TextAreaField, TextField } from "../../shared/ui/forms";
import { CapabilityDeniedNotice, DynamicModuleErrorNotice, DynamicModulesEmpty } from "./screen-states";
import type { DynamicModuleAccess } from "./capability";
import {
  formatDateTime,
  MANIFEST_UNAVAILABLE,
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
  // The version payload carries the digest but not the manifest body, so an
  // unread manifest is reported as absent rather than rendered as an empty object.
  if (manifest === undefined) {
    return <details className="dynamic-modules__manifest">
      <summary>{label}</summary>
      <div className="dynamic-modules__manifest-body">
        <p className="dynamic-modules__hint">{MANIFEST_UNAVAILABLE}</p>
      </div>
    </details>;
  }
  const summary = summarizeManifest(manifest);
  return <details className="dynamic-modules__manifest">
    <summary>{label}</summary>
    <div className="dynamic-modules__manifest-body">
      <dl className="dynamic-modules__manifest-facts">
        <div><dt>Kind</dt><dd>{summary.kind}</dd></div>
        <div><dt>Schema</dt><dd>{summary.schemaVersion}</dd></div>
        <div><dt>App id</dt><dd><code>{summary.appId}</code></dd></div>
        <div><dt>Version</dt><dd><code>{summary.version}</code></dd></div>
        <div><dt>Artifact digest</dt><dd><code>{summary.artifactDigest ?? "Not declared"}</code></dd></div>
        <div><dt>Screens</dt><dd>{summary.screenCount}</dd></div>
      </dl>
      <div className="dynamic-modules__manifest-columns">
        <section aria-label="Manifest routes">
          <h4>Routes</h4>
          {summary.routes.length === 0
            ? <p className="dynamic-modules__hint">The stored manifest declares no routes.</p>
            : <ul>{summary.routes.map((route) => <li key={route}><code>{route}</code></li>)}</ul>}
        </section>
        <section aria-label="Requested capabilities">
          <h4>Requested capabilities</h4>
          {summary.capabilities.length === 0
            ? <p className="dynamic-modules__hint">No capability is requested by this manifest.</p>
            : <ul>{summary.capabilities.map((capability) => <li key={`${capability.id}@${capability.version}`}><code>{capability.id}</code> v{capability.version}</li>)}</ul>}
        </section>
        <section aria-label="Manifest limits and data policy">
          <h4>Limits and data policy</h4>
          {summary.limits.length === 0 && summary.dataPolicy.length === 0
            ? <p className="dynamic-modules__hint">No limits or data policy declared.</p>
            : <ul>
              {summary.limits.map((limit) => <li key={`limit-${limit.key}`}><code>{limit.key}</code>: {limit.value}</li>)}
              {summary.dataPolicy.map((entry) => <li key={`policy-${entry.key}`}><code>{entry.key}</code>: {entry.value}</li>)}
            </ul>}
        </section>
      </div>
      <pre className="dynamic-modules__manifest-json" tabIndex={0}>{manifestText(manifest)}</pre>
      <p className="dynamic-modules__hint">
        A manifest is the requested contract, not a grant. Its routes, queries and actions are
        validated on the server before publication and re-checked on every capability call.
      </p>
    </div>
  </details>;
}

export function VersionPanel({ service, access, definition, versions, onChanged }: VersionPanelProps) {
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
    ));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationAttempted(true);
    const nextErrors = validatePublishForm(
      { manifestText, notes, ...(definition.latestVersion ? { expectedVersion: definition.latestVersion } : {}) },
      { appId: definition.appId, ...(definition.kind === undefined ? {} : { kind: definition.kind }) },
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
      submit.succeed(`Published version ${published.version} (${published.status}). Immutable digest ${published.digest}.`);
      onChanged();
    });
    if (ok) {
      setNotes("");
      setErrors({});
      setValidationAttempted(false);
    }
  }

  return <section className="dynamic-modules__panel" aria-labelledby={`${formId}-versions`}>
    <h2 id={`${formId}-versions`}>Versions</h2>
    <p className="dynamic-modules__hint">
      Published versions are immutable and content-addressed by digest. A manifest, route,
      capability or artifact change creates a new version; it never edits a published one.
    </p>

    {ordered.length === 0 && <DynamicModulesEmpty
      description="No version has been published for this definition yet. Publish the first immutable manifest below."
      title="No versions published"
    />}

    {ordered.length > 0 && <div className="ui-table-scroll" role="region" aria-label="Definition versions" tabIndex={0}>
      <table className="ui-table dynamic-modules__table">
        <caption>Versions of {definition.displayName}</caption>
        <thead>
          <tr>
            <th scope="col">Version</th>
            <th scope="col">Status</th>
            <th scope="col">Digest</th>
            <th scope="col">Created</th>
            <th scope="col">Published</th>
            <th scope="col">Notes</th>
            <th scope="col">Manifest</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((version) => <tr key={version.id}>
            <th scope="row"><code>{version.version}</code>{version.version === definition.latestVersion && <span className="dynamic-modules__latest-tag">latest</span>}</th>
            <td><StatusBadge variant={versionStatusTone(version.status)}>{versionStatusLabel(version.status)}</StatusBadge></td>
            <td><code className="dynamic-modules__digest">{version.digest}</code></td>
            <td>{formatDateTime(version.createdAt)}</td>
            <td>{formatDateTime(version.publishedAt)}</td>
            <td>{version.notes ?? UNKNOWN_VALUE}</td>
            <td><ManifestViewer label={`View manifest for ${version.version}`} manifest={version.manifest} /></td>
          </tr>)}
        </tbody>
      </table>
    </div>}

    {!access.canManage
      ? <CapabilityDeniedNotice missing={access.missing} operation="manage">
        <p className="dynamic-modules__hint">Publishing a version is a registry command and stays disabled without that capability.</p>
      </CapabilityDeniedNotice>
      : definition.status === "archived"
        ? <p className="dynamic-modules__hint" role="status">This definition is archived. New versions cannot be published until it is restored by the registry owner.</p>
        : <form className="dynamic-modules__form" noValidate onSubmit={(event) => void handleSubmit(event)}>
          <h3>Publish a new version</h3>
          <TextAreaField
            className="dynamic-modules__manifest-input"
            error={errors.manifest}
            hint="Closed envelope: unknown fields, embedded URLs, non-semver versions and a foreign app_id are rejected before the request."
            id={`${formId}-manifest`}
            label="Manifest JSON"
            onChange={(event) => { setManifestText(event.currentTarget.value); revalidate(event.currentTarget.value, notes); }}
            required
            rows={16}
            spellCheck={false}
            value={manifestText}
          />
          <TextField
            error={errors.notes}
            hint={`Optional release notes. The current latest version is ${definition.latestVersion ?? "none"}; it is sent as the expected version.`}
            id={`${formId}-notes`}
            label="Release notes"
            maxLength={2000}
            onChange={(event) => { setNotes(event.currentTarget.value); revalidate(manifestText, event.currentTarget.value); }}
            value={notes}
          />
          {submit.error && <DynamicModuleErrorNotice error={submit.error} />}
          {submit.notice && <p className="dynamic-modules__notice" role="status">{submit.notice}</p>}
          <div className="dynamic-modules__actions">
            <Button disabled={submit.busy} type="submit" variant="primary">{submit.busy ? "Publishing…" : "Publish version"}</Button>
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
            >Reset form</Button>
          </div>
        </form>}
  </section>;
}
