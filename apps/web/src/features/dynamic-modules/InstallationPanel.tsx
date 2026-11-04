import { useId, useState, type FormEvent } from "react";
import type { DynamicModuleDefinition, DynamicModuleInstance, DynamicModuleVersion } from "../../api/dynamic";
import { Button, StatusBadge } from "../../shared/ui";
import { SelectField } from "../../shared/ui/forms/index";
import type { DynamicModuleAccess } from "./capability";
import { useResource, useSubmitController } from "./hooks";
import {
  formatDateTime,
  instanceScopeLabel,
  instanceStatusLabel,
  instanceStatusTone,
  sortInstances,
  sortVersionsDesc,
  UNKNOWN_VALUE,
} from "./mapping";
import type { DynamicModulesService, DynamicModuleServiceError } from "./service";
import {
  CapabilityDeniedNotice,
  DynamicModuleErrorNotice,
  DynamicModulesEmpty,
} from "./screen-states";
import { validateInstallForm, type FieldErrors, type InstallFormValues } from "./validation";

export interface InstallationPanelProps {
  readonly service: DynamicModulesService;
  readonly access: DynamicModuleAccess;
  readonly definition: DynamicModuleDefinition;
  readonly versions: readonly DynamicModuleVersion[];
  readonly instances: readonly DynamicModuleInstance[];
  readonly loading: boolean;
  readonly error?: DynamicModuleServiceError;
  readonly onChanged: () => void;
  readonly onReload: () => void;
}

const EMPTY_FORM: InstallFormValues = { cabinetId: "", projectId: "", scope: "", versionId: "" };

export function InstallationPanel({
  service,
  access,
  definition,
  versions,
  instances,
  loading,
  error,
  onChanged,
  onReload,
}: InstallationPanelProps) {
  const formId = useId();
  const [values, setValues] = useState<InstallFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [attempted, setAttempted] = useState(false);
  const submit = useSubmitController();

  const installableVersions = sortVersionsDesc(versions).filter((version) => version.status === "published" || version.status === "approved");
  const cabinets = useResource(
    (signal) => service.listCabinets({ signal }),
    "cabinets",
    { enabled: access.canRead },
  );
  const projects = useResource(
    (signal) => service.listProjects(values.cabinetId, { signal }),
    `projects:${values.cabinetId}`,
    { enabled: access.canRead && values.scope === "project" && values.cabinetId.length > 0 },
  );

  const cabinetOptions = (cabinets.state.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }));
  const projectOptions = (projects.state.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }));
  const versionOptions = installableVersions.map((version) => ({
    value: version.id,
    label: `${version.version} · ${version.status} · ${version.digest.slice(0, 19)}…`,
  }));

  const update = (patch: Partial<InstallFormValues>) => {
    const next = { ...values, ...patch };
    setValues(next);
    if (attempted) setErrors(validateInstallForm(next, installableVersions.map((version) => version.id)));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAttempted(true);
    const nextErrors = validateInstallForm(values, installableVersions.map((version) => version.id));
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await submit.run(async () => {
      const instance = await service.installInstance({
        definitionId: definition.id,
        values,
        knownVersionIds: installableVersions.map((version) => version.id),
      });
      submit.succeed(`Installed ${instance.version} as ${instance.scope} scope instance ${instance.id} (${instance.status}).`);
      onChanged();
      setValues(EMPTY_FORM);
      setAttempted(false);
    });
    if (ok) setErrors({});
  }

  const installBlocked = definition.status === "archived" || installableVersions.length === 0;

  return <section className="dynamic-modules__panel" aria-labelledby={`${formId}-installations`}>
    <h2 id={`${formId}-installations`}>Installations</h2>
    <p className="dynamic-modules__hint">
      An installation pins one immutable version into a cabinet or a project. Reinstalling the
      same command is idempotent; installing a different version is a new installation.
    </p>

    {loading && <p className="dynamic-modules__hint" role="status">Loading installations…</p>}
    {error && <DynamicModuleErrorNotice error={error} onRetry={onReload} retryLabel="Reload installations" />}

    {!loading && !error && instances.length === 0 && <DynamicModulesEmpty
      description="No cabinet or project has installed this definition yet."
      title="No installations"
    />}

    {instances.length > 0 && <div className="ui-table-scroll" role="region" aria-label="Definition installations" tabIndex={0}>
      <table className="ui-table dynamic-modules__table">
        <caption>Installations of {definition.displayName}</caption>
        <thead>
          <tr>
            <th scope="col">Instance</th>
            <th scope="col">Version</th>
            <th scope="col">Scope</th>
            <th scope="col">Status</th>
            <th scope="col">Installed by</th>
            <th scope="col">Installed</th>
          </tr>
        </thead>
        <tbody>
          {sortInstances(instances).map((instance) => <tr key={instance.id}>
            <th scope="row"><code>{instance.id}</code></th>
            <td><code>{instance.version}</code></td>
            <td>{instanceScopeLabel(instance)}</td>
            <td><StatusBadge variant={instanceStatusTone(instance.status)}>{instanceStatusLabel(instance.status)}</StatusBadge></td>
            <td>{instance.installedBy ?? UNKNOWN_VALUE}</td>
            <td>{formatDateTime(instance.installedAt)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>}

    {!access.canManage
      ? <CapabilityDeniedNotice missing={access.missing} operation="manage">
        <p className="dynamic-modules__hint">Installing a definition is a registry command.</p>
      </CapabilityDeniedNotice>
      : <form className="dynamic-modules__form" noValidate onSubmit={(event) => void handleSubmit(event)}>
        <h3>Install a version</h3>
        {installBlocked && <p className="dynamic-modules__hint" role="status">
          {definition.status === "archived"
            ? "An archived definition cannot be installed."
            : "No published or approved version is available to install."}
        </p>}
        <div className="dynamic-modules__form-grid">
          <SelectField
            error={errors.versionId}
            hint="Only published or approved versions can be installed."
            id={`${formId}-version`}
            label="Version"
            onValueChange={(value) => update({ versionId: value ?? "" })}
            options={versionOptions}
            placeholder="Select a version"
            required
            value={values.versionId}
          />
          <SelectField
            error={errors.cabinetId}
            hint={cabinets.state.status === "error" ? "The cabinet directory could not be loaded." : "Cabinet that owns the installation."}
            id={`${formId}-cabinet`}
            label="Cabinet"
            onValueChange={(value) => update({ cabinetId: value ?? "", projectId: "" })}
            options={cabinetOptions}
            placeholder="Select a cabinet"
            required
            value={values.cabinetId}
          />
          <SelectField
            error={errors.scope}
            id={`${formId}-scope`}
            label="Scope"
            onValueChange={(value) => update({ scope: (value ?? "") as InstallFormValues["scope"], projectId: "" })}
            options={[{ value: "cabinet", label: "Cabinet" }, { value: "project", label: "Project" }]}
            placeholder="Select a scope"
            required
            value={values.scope}
          />
          <SelectField
            disabled={values.scope !== "project" || !values.cabinetId}
            error={errors.projectId}
            hint={values.scope === "project" ? "Project that receives the installation." : "Only used for project scope."}
            id={`${formId}-project`}
            label="Project"
            onValueChange={(value) => update({ projectId: value ?? "" })}
            options={projectOptions}
            placeholder={values.scope === "project" ? "Select a project" : "Not applicable"}
            value={values.projectId}
          />
        </div>
        {projects.state.status === "error" && <DynamicModuleErrorNotice error={projects.state.error} onRetry={projects.reload} retryLabel="Reload projects" />}
        {cabinets.state.status === "error" && <DynamicModuleErrorNotice error={cabinets.state.error} onRetry={cabinets.reload} retryLabel="Reload cabinets" />}
        {submit.error && <DynamicModuleErrorNotice error={submit.error} />}
        {submit.notice && <p className="dynamic-modules__notice" role="status">{submit.notice}</p>}
        <div className="dynamic-modules__actions">
          <Button disabled={submit.busy || installBlocked} type="submit" variant="primary">{submit.busy ? "Installing…" : "Install version"}</Button>
        </div>
      </form>}
  </section>;
}
