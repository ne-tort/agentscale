import { useId, useState, type FormEvent } from "react";
import type { DynamicModuleDefinition, DynamicModuleGrant } from "../../api/dynamic";
import { useI18n } from "../../i18n/useT";
import { Button, ConfirmationDialog, StatusBadge } from "../../shared/ui";
import { SelectField, TextField } from "../../shared/ui/forms/index";
import type { DynamicModuleAccess } from "./capability";
import { useResource, useSubmitController } from "./hooks";
import {
  formatDateTime,
  grantState,
  grantStateLabel,
  grantStateTone,
  isRevocable,
  sortGrants,
  UNKNOWN_VALUE,
} from "./mapping";
import type { DynamicModuleServiceError, DynamicModulesService } from "./service";
import {
  CapabilityDeniedNotice,
  DynamicModuleErrorNotice,
  DynamicModulesEmpty,
} from "./screen-states";
import { validateGrantForm, type FieldErrors, type GrantFormValues } from "./validation";

/** Audit reason code for a manual revocation; free text could carry sensitive content. */
export const REVOKE_REASON_CODE = "revoked_from_registry_screen";

const EMPTY_FORM: GrantFormValues = {
  cabinetId: "",
  projectId: "",
  scope: "",
  capabilityId: "",
  capabilityVersion: "1",
  expiresAt: "",
  reason: "",
};

export interface GrantPanelProps {
  readonly service: DynamicModulesService;
  readonly access: DynamicModuleAccess;
  readonly definition: DynamicModuleDefinition;
  readonly grants: readonly DynamicModuleGrant[];
  readonly loading: boolean;
  readonly error?: DynamicModuleServiceError;
  readonly onChanged: () => void;
  readonly onReload: () => void;
}

export function GrantPanel({ service, access, definition, grants, loading, error, onChanged, onReload }: GrantPanelProps) {
  const { t, locale } = useI18n();
  const formId = useId();
  const [values, setValues] = useState<GrantFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [attempted, setAttempted] = useState(false);
  const grant = useSubmitController();
  const revoke = useSubmitController();
  const [pendingRevoke, setPendingRevoke] = useState<DynamicModuleGrant>();

  const cabinets = useResource((signal) => service.listCabinets({ signal }), "cabinets", { enabled: access.canRead });
  const projects = useResource(
    (signal) => service.listProjects(values.cabinetId, { signal }),
    `projects:${values.cabinetId}`,
    { enabled: access.canRead && values.scope === "project" && values.cabinetId.length > 0 },
  );
  /** Grants in this page's tenant scope; `now` distinguishes active from expired. */
  const now = Date.now();
  const ordered = sortGrants(grants);
  const activeCount = ordered.filter((entry) => grantState(entry, now) === "active").length;

  const update = (patch: Partial<GrantFormValues>) => {
    const next = { ...values, ...patch };
    setValues(next);
    if (attempted) setErrors(validateGrantForm(next, t));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAttempted(true);
    const nextErrors = validateGrantForm(values, t);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await grant.run(async () => {
      const created = await service.grantCapability({ definitionId: definition.id, values });
      grant.succeed(t("dynamicModules.grantDone", {
        capability: created.capabilityId,
        version: created.capabilityVersion ?? UNKNOWN_VALUE,
        scope: created.projectId
          ? t("dynamicModules.scopePhraseProject")
          : t("dynamicModules.scopePhraseCabinet"),
      }));
      onChanged();
      setValues(EMPTY_FORM);
      setAttempted(false);
    });
    if (ok) setErrors({});
  }

  async function confirmRevoke() {
    if (!pendingRevoke) return;
    const target = pendingRevoke;
    const ok = await revoke.run(async () => {
      const revoked = await service.revokeGrant({
        definitionId: definition.id,
        grantId: target.id,
        values: { reason: REVOKE_REASON_CODE },
      });
      revoke.succeed(t("dynamicModules.revokeDone", {
        capability: revoked.capabilityId,
        scope: revoked.projectId
          ? t("dynamicModules.scopePhraseProject")
          : t("dynamicModules.scopePhraseCabinet"),
      }));
      onChanged();
    });
    if (ok) setPendingRevoke(undefined);
  }

  const pendingScope = pendingRevoke === undefined
    ? ""
    : pendingRevoke.projectId
      ? t("dynamicModules.scopePhraseProject")
      : t("dynamicModules.scopePhraseCabinet");

  return <section className="dynamic-modules__panel" aria-labelledby={`${formId}-grants`}>
    <h2 id={`${formId}-grants`}>{t("dynamicModules.grantsHeading")}</h2>
    <p className="dynamic-modules__hint">
      {t("dynamicModules.grantsHint", { active: activeCount, total: ordered.length })}
    </p>

    {loading && <p className="dynamic-modules__hint" role="status">{t("dynamicModules.grantsLoading")}</p>}
    {error && <DynamicModuleErrorNotice error={error} onRetry={onReload} retryLabel={t("dynamicModules.reloadGrants")} />}

    {!loading && !error && ordered.length === 0 && <DynamicModulesEmpty
      description={t("dynamicModules.grantsEmptyDescription")}
      title={t("dynamicModules.grantsEmptyTitle")}
    />}

    {ordered.length > 0 && <div className="ui-table-scroll" role="region" aria-label={t("dynamicModules.grantsTableAria")} tabIndex={0}>
      <table className="ui-table dynamic-modules__table">
        <caption>{t("dynamicModules.grantsCaption", { name: definition.displayName })}</caption>
        <thead>
          <tr>
            <th scope="col">{t("dynamicModules.colCapability")}</th>
            <th scope="col">{t("dynamicModules.colState")}</th>
            <th scope="col">{t("dynamicModules.colScope")}</th>
            <th scope="col">{t("dynamicModules.colGrantedBy")}</th>
            <th scope="col">{t("dynamicModules.colExpires")}</th>
            <th scope="col">{t("dynamicModules.colRevoked")}</th>
            <th scope="col"><span className="dynamic-modules__sr-only">{t("dynamicModules.colActions")}</span></th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((entry) => {
            const state = grantState(entry, now);
            return <tr key={entry.id}>
              <th scope="row">
                <code>{entry.capabilityId}</code>
                <span className="dynamic-modules__row-subtitle">v{entry.capabilityVersion}</span>
              </th>
              <td><StatusBadge variant={grantStateTone(state)}>{grantStateLabel(state, t)}</StatusBadge></td>
              <td>{entry.projectId
                ? t("dynamicModules.scopeProject", { id: entry.projectId })
                : t("dynamicModules.scopeCabinet", { id: entry.cabinetId ?? UNKNOWN_VALUE })}</td>
              <td>{entry.grantedBy}</td>
              <td>{entry.expiresAt ? formatDateTime(entry.expiresAt, locale) : t("dynamicModules.noExpiry")}</td>
              <td>{entry.revokedAt ? formatDateTime(entry.revokedAt, locale) : UNKNOWN_VALUE}</td>
              <td className="dynamic-modules__row-action">
                {access.canManage && isRevocable(entry, now)
                  ? <Button
                    aria-label={t("dynamicModules.revokeAria", { capability: entry.capabilityId, version: entry.capabilityVersion ?? UNKNOWN_VALUE })}
                    disabled={revoke.busy}
                    onClick={() => { revoke.reset(); setPendingRevoke(entry); }}
                    size="small"
                    variant="danger"
                  >{t("dynamicModules.revoke")}</Button>
                  : <span className="dynamic-modules__hint">{access.canManage ? t("dynamicModules.notRevocable") : t("dynamicModules.readOnly")}</span>}
              </td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>}

    {revoke.error && <DynamicModuleErrorNotice error={revoke.error} />}
    {revoke.notice && <p className="dynamic-modules__notice" role="status">{revoke.notice}</p>}

    <ConfirmationDialog
      busy={revoke.busy}
      confirmLabel={t("dynamicModules.revokeConfirmLabel")}
      description={pendingRevoke
        ? t("dynamicModules.revokeConfirm", {
          capability: pendingRevoke.capabilityId,
          version: pendingRevoke.capabilityVersion ?? UNKNOWN_VALUE,
          scope: pendingScope,
        })
        : t("dynamicModules.revokeConfirmFallback")}
      onCancel={() => { if (!revoke.busy) setPendingRevoke(undefined); }}
      onConfirm={() => void confirmRevoke()}
      open={pendingRevoke !== undefined}
      title={t("dynamicModules.revokeTitle")}
    />

    {!access.canManage
      ? <CapabilityDeniedNotice missing={access.missing} operation="manage">
        <p className="dynamic-modules__hint">{t("dynamicModules.grantDeniedHint")}</p>
      </CapabilityDeniedNotice>
      : <form className="dynamic-modules__form" noValidate onSubmit={(event) => void handleSubmit(event)}>
        <h3>{t("dynamicModules.grantHeading")}</h3>
        <div className="dynamic-modules__form-grid">
          <TextField
            autoComplete="off"
            error={errors.capabilityId}
            hint={t("dynamicModules.capabilityIdHint")}
            id={`${formId}-capability`}
            label={t("dynamicModules.capabilityIdLabel")}
            onChange={(event) => update({ capabilityId: event.currentTarget.value })}
            required
            value={values.capabilityId}
          />
          <TextField
            error={errors.capabilityVersion}
            hint={t("dynamicModules.capabilityVersionHint")}
            id={`${formId}-capability-version`}
            inputMode="numeric"
            label={t("dynamicModules.capabilityVersionLabel")}
            onChange={(event) => update({ capabilityVersion: event.currentTarget.value })}
            required
            value={values.capabilityVersion}
          />
          <SelectField
            error={errors.cabinetId}
            id={`${formId}-cabinet`}
            label={t("dynamicModules.cabinetLabel")}
            onValueChange={(value) => update({ cabinetId: value ?? "", projectId: "" })}
            options={(cabinets.state.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }))}
            placeholder={t("dynamicModules.cabinetPlaceholder")}
            required
            value={values.cabinetId}
          />
          <SelectField
            error={errors.scope}
            id={`${formId}-scope`}
            label={t("dynamicModules.scopeLabel")}
            onValueChange={(value) => update({ scope: (value ?? "") as GrantFormValues["scope"], projectId: "" })}
            options={[{ value: "cabinet", label: t("dynamicModules.scopeCabinetOption") }, { value: "project", label: t("dynamicModules.scopeProjectOption") }]}
            placeholder={t("dynamicModules.scopePlaceholder")}
            required
            value={values.scope}
          />
          <SelectField
            disabled={values.scope !== "project" || !values.cabinetId}
            error={errors.projectId}
            id={`${formId}-project`}
            label={t("dynamicModules.projectLabel")}
            onValueChange={(value) => update({ projectId: value ?? "" })}
            options={(projects.state.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }))}
            placeholder={values.scope === "project" ? t("dynamicModules.projectPlaceholder") : t("dynamicModules.projectNotApplicable")}
            value={values.projectId}
          />
          <TextField
            error={errors.expiresAt}
            hint={t("dynamicModules.expiresAtHint")}
            id={`${formId}-expires`}
            label={t("dynamicModules.expiresAtLabel")}
            onChange={(event) => update({ expiresAt: event.currentTarget.value })}
            placeholder="2026-12-31T18:00"
            type="datetime-local"
            value={values.expiresAt}
          />
        </div>
        <TextField
          error={errors.reason}
          hint={t("dynamicModules.reasonHint")}
          id={`${formId}-reason`}
          label={t("dynamicModules.reasonLabel")}
          maxLength={64}
          onChange={(event) => update({ reason: event.currentTarget.value })}
          value={values.reason}
        />
        {cabinets.state.status === "error" && <DynamicModuleErrorNotice error={cabinets.state.error} onRetry={cabinets.reload} retryLabel={t("dynamicModules.reloadCabinets")} />}
        {projects.state.status === "error" && <DynamicModuleErrorNotice error={projects.state.error} onRetry={projects.reload} retryLabel={t("dynamicModules.reloadProjects")} />}
        {grant.error && <DynamicModuleErrorNotice error={grant.error} />}
        {grant.notice && <p className="dynamic-modules__notice" role="status">{grant.notice}</p>}
        <div className="dynamic-modules__actions">
          <Button disabled={grant.busy} type="submit" variant="primary">{grant.busy ? t("dynamicModules.granting") : t("dynamicModules.grantSubmit")}</Button>
        </div>
      </form>}
  </section>;
}
