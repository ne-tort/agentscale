import { useId, useState, type FormEvent } from "react";
import type { DynamicModuleDefinition, DynamicModuleInstance, DynamicModuleVersion } from "../../api/dynamic";
import { useI18n } from "../../i18n/useT";
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
  versionStatusLabel,
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
  const { t, locale } = useI18n();
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
  // The version picker shows the version number and its state in words; the digest is
  // a long identifier, so only a shortened form follows as a hint of which build it is.
  const versionOptions = installableVersions.map((version) => ({
    value: version.id,
    label: `${version.version} · ${versionStatusLabel(version.status, t)} · ${version.digest.slice(0, 19)}…`,
  }));

  const update = (patch: Partial<InstallFormValues>) => {
    const next = { ...values, ...patch };
    setValues(next);
    if (attempted) setErrors(validateInstallForm(next, installableVersions.map((version) => version.id), t));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAttempted(true);
    const nextErrors = validateInstallForm(values, installableVersions.map((version) => version.id), t);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await submit.run(async () => {
      const instance = await service.installInstance({
        definitionId: definition.id,
        values,
        knownVersionIds: installableVersions.map((version) => version.id),
      });
      submit.succeed(t("dynamicModules.installDone", {
        version: instance.version ?? UNKNOWN_VALUE,
        scope: instance.projectId
          ? t("dynamicModules.scopeProject", { id: instance.projectId })
          : t("dynamicModules.scopeCabinet", { id: instance.cabinetId ?? UNKNOWN_VALUE }),
        id: instance.id,
        status: instanceStatusLabel(instance.status, t),
      }));
      onChanged();
      setValues(EMPTY_FORM);
      setAttempted(false);
    });
    if (ok) setErrors({});
  }

  const installBlocked = definition.status === "archived" || installableVersions.length === 0;

  return <section className="dynamic-modules__panel" aria-labelledby={`${formId}-installations`}>
    <h2 id={`${formId}-installations`}>{t("dynamicModules.installationsHeading")}</h2>
    <p className="dynamic-modules__hint">
      {t("dynamicModules.installationsHint")}
    </p>

    {loading && <p className="dynamic-modules__hint" role="status">{t("dynamicModules.installationsLoading")}</p>}
    {error && <DynamicModuleErrorNotice error={error} onRetry={onReload} retryLabel={t("dynamicModules.reloadInstallations")} />}

    {!loading && !error && instances.length === 0 && <DynamicModulesEmpty
      description={t("dynamicModules.installationsEmptyDescription")}
      title={t("dynamicModules.installationsEmptyTitle")}
    />}

    {instances.length > 0 && <div className="ui-table-scroll" role="region" aria-label={t("dynamicModules.installationsTableAria")} tabIndex={0}>
      <table className="ui-table dynamic-modules__table">
        <caption>{t("dynamicModules.installationsCaption", { name: definition.displayName })}</caption>
        <thead>
          <tr>
            <th scope="col">{t("dynamicModules.colInstance")}</th>
            <th scope="col">{t("dynamicModules.colVersion")}</th>
            <th scope="col">{t("dynamicModules.colScope")}</th>
            <th scope="col">{t("dynamicModules.colStatus")}</th>
            <th scope="col">{t("dynamicModules.colInstalledBy")}</th>
            <th scope="col">{t("dynamicModules.colInstalled")}</th>
          </tr>
        </thead>
        <tbody>
          {sortInstances(instances).map((instance) => <tr key={instance.id}>
            <th scope="row"><code>{instance.id}</code></th>
            <td><code>{instance.version}</code></td>
            <td>{instanceScopeLabel(instance, t)}</td>
            <td><StatusBadge variant={instanceStatusTone(instance.status)}>{instanceStatusLabel(instance.status, t)}</StatusBadge></td>
            <td>{instance.installedBy ?? UNKNOWN_VALUE}</td>
            <td>{formatDateTime(instance.installedAt, locale)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>}

    {!access.canManage
      ? <CapabilityDeniedNotice missing={access.missing} operation="manage">
        <p className="dynamic-modules__hint">{t("dynamicModules.installDeniedHint")}</p>
      </CapabilityDeniedNotice>
      : <form className="dynamic-modules__form" noValidate onSubmit={(event) => void handleSubmit(event)}>
        <h3>{t("dynamicModules.installHeading")}</h3>
        {installBlocked && <p className="dynamic-modules__hint" role="status">
          {definition.status === "archived"
            ? t("dynamicModules.installArchived")
            : t("dynamicModules.installNoVersions")}
        </p>}
        <div className="dynamic-modules__form-grid">
          <SelectField
            error={errors.versionId}
            hint={t("dynamicModules.versionSelectHint")}
            id={`${formId}-version`}
            label={t("dynamicModules.versionSelectLabel")}
            onValueChange={(value) => update({ versionId: value ?? "" })}
            options={versionOptions}
            placeholder={t("dynamicModules.versionSelectPlaceholder")}
            required
            value={values.versionId}
          />
          <SelectField
            error={errors.cabinetId}
            hint={cabinets.state.status === "error" ? t("dynamicModules.cabinetLoadFailed") : t("dynamicModules.cabinetHint")}
            id={`${formId}-cabinet`}
            label={t("dynamicModules.cabinetLabel")}
            onValueChange={(value) => update({ cabinetId: value ?? "", projectId: "" })}
            options={cabinetOptions}
            placeholder={t("dynamicModules.cabinetPlaceholder")}
            required
            value={values.cabinetId}
          />
          <SelectField
            error={errors.scope}
            id={`${formId}-scope`}
            label={t("dynamicModules.scopeLabel")}
            onValueChange={(value) => update({ scope: (value ?? "") as InstallFormValues["scope"], projectId: "" })}
            options={[{ value: "cabinet", label: t("dynamicModules.scopeCabinetOption") }, { value: "project", label: t("dynamicModules.scopeProjectOption") }]}
            placeholder={t("dynamicModules.scopePlaceholder")}
            required
            value={values.scope}
          />
          <SelectField
            disabled={values.scope !== "project" || !values.cabinetId}
            error={errors.projectId}
            hint={values.scope === "project" ? t("dynamicModules.projectHint") : t("dynamicModules.projectHintInactive")}
            id={`${formId}-project`}
            label={t("dynamicModules.projectLabel")}
            onValueChange={(value) => update({ projectId: value ?? "" })}
            options={projectOptions}
            placeholder={values.scope === "project" ? t("dynamicModules.projectPlaceholder") : t("dynamicModules.projectNotApplicable")}
            value={values.projectId}
          />
        </div>
        {projects.state.status === "error" && <DynamicModuleErrorNotice error={projects.state.error} onRetry={projects.reload} retryLabel={t("dynamicModules.reloadProjects")} />}
        {cabinets.state.status === "error" && <DynamicModuleErrorNotice error={cabinets.state.error} onRetry={cabinets.reload} retryLabel={t("dynamicModules.reloadCabinets")} />}
        {submit.error && <DynamicModuleErrorNotice error={submit.error} />}
        {submit.notice && <p className="dynamic-modules__notice" role="status">{submit.notice}</p>}
        <div className="dynamic-modules__actions">
          <Button disabled={submit.busy || installBlocked} type="submit" variant="primary">{submit.busy ? t("dynamicModules.installing") : t("dynamicModules.installSubmit")}</Button>
        </div>
      </form>}
  </section>;
}
