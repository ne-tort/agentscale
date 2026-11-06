import { useId, useState, type FormEvent } from "react";
import type { DynamicModuleDefinition, DynamicModuleGrant } from "../../api/dynamic";
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
    if (attempted) setErrors(validateGrantForm(next));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAttempted(true);
    const nextErrors = validateGrantForm(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    const ok = await grant.run(async () => {
      const created = await service.grantCapability({ definitionId: definition.id, values });
      grant.succeed(`Granted ${created.capabilityId} v${created.capabilityVersion} for ${created.scope} scope.`);
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
      revoke.succeed(`Revoked ${revoked.capabilityId} for ${revoked.scope} scope. Affected instances drain before they stop.`);
      onChanged();
    });
    if (ok) setPendingRevoke(undefined);
  }

  return <section className="dynamic-modules__panel" aria-labelledby={`${formId}-grants`}>
    <h2 id={`${formId}-grants`}>Capability grants</h2>
    <p className="dynamic-modules__hint">
      A manifest requests capabilities; only an explicit grant makes them effective. Grants are
      bound to this definition, its scope, and an optional expiry, and every call is re-checked by
      the gateway. {activeCount} of {ordered.length} listed {ordered.length === 1 ? "grant is" : "grants are"} currently active.
    </p>

    {loading && <p className="dynamic-modules__hint" role="status">Loading grants…</p>}
    {error && <DynamicModuleErrorNotice error={error} onRetry={onReload} retryLabel="Reload grants" />}

    {!loading && !error && ordered.length === 0 && <DynamicModulesEmpty
      description="No capability has been granted to this definition in this tenant scope. Deny-by-default applies until a grant exists."
      title="No grants"
    />}

    {ordered.length > 0 && <div className="ui-table-scroll" role="region" aria-label="Definition capability grants" tabIndex={0}>
      <table className="ui-table dynamic-modules__table">
        <caption>Capability grants for {definition.displayName}</caption>
        <thead>
          <tr>
            <th scope="col">Capability</th>
            <th scope="col">State</th>
            <th scope="col">Scope</th>
            <th scope="col">Granted by</th>
            <th scope="col">Expires</th>
            <th scope="col">Revoked</th>
            <th scope="col"><span className="dynamic-modules__sr-only">Actions</span></th>
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
              <td><StatusBadge variant={grantStateTone(state)}>{grantStateLabel(state)}</StatusBadge></td>
              <td>{entry.projectId ? `Project ${entry.projectId}` : `Cabinet ${entry.cabinetId}`}</td>
              <td>{entry.grantedBy}</td>
              <td>{entry.expiresAt ? formatDateTime(entry.expiresAt) : "No expiry"}</td>
              <td>{entry.revokedAt ? formatDateTime(entry.revokedAt) : UNKNOWN_VALUE}</td>
              <td className="dynamic-modules__row-action">
                {access.canManage && isRevocable(entry, now)
                  ? <Button
                    aria-label={`Revoke ${entry.capabilityId} v${entry.capabilityVersion}`}
                    disabled={revoke.busy}
                    onClick={() => { revoke.reset(); setPendingRevoke(entry); }}
                    size="small"
                    variant="danger"
                  >Revoke</Button>
                  : <span className="dynamic-modules__hint">{access.canManage ? "Not revocable" : "Read only"}</span>}
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
      confirmLabel="Revoke grant"
      description={pendingRevoke
        ? `Revoke ${pendingRevoke.capabilityId} v${pendingRevoke.capabilityVersion} for ${pendingRevoke.projectId ? `project ${pendingRevoke.projectId}` : `cabinet ${pendingRevoke.cabinetId}`}? Live instances drain and affected capability calls are denied immediately. The audit event records the reason code ${REVOKE_REASON_CODE}.`
        : "Revoke this capability grant?"}
      onCancel={() => { if (!revoke.busy) setPendingRevoke(undefined); }}
      onConfirm={() => void confirmRevoke()}
      open={pendingRevoke !== undefined}
      title="Revoke capability grant"
    />

    {!access.canManage
      ? <CapabilityDeniedNotice missing={access.missing} operation="manage">
        <p className="dynamic-modules__hint">Granting and revoking capabilities are registry commands.</p>
      </CapabilityDeniedNotice>
      : <form className="dynamic-modules__form" noValidate onSubmit={(event) => void handleSubmit(event)}>
        <h3>Grant a capability</h3>
        <div className="dynamic-modules__form-grid">
          <TextField
            autoComplete="off"
            error={errors.capabilityId}
            hint="Dotted identifier from the manifest, for example equipment.offers.read."
            id={`${formId}-capability`}
            label="Capability id"
            onChange={(event) => update({ capabilityId: event.currentTarget.value })}
            required
            value={values.capabilityId}
          />
          <TextField
            error={errors.capabilityVersion}
            hint="Positive integer version declared by the manifest."
            id={`${formId}-capability-version`}
            inputMode="numeric"
            label="Capability version"
            onChange={(event) => update({ capabilityVersion: event.currentTarget.value })}
            required
            value={values.capabilityVersion}
          />
          <SelectField
            error={errors.cabinetId}
            id={`${formId}-cabinet`}
            label="Cabinet"
            onValueChange={(value) => update({ cabinetId: value ?? "", projectId: "" })}
            options={(cabinets.state.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }))}
            placeholder="Select a cabinet"
            required
            value={values.cabinetId}
          />
          <SelectField
            error={errors.scope}
            id={`${formId}-scope`}
            label="Scope"
            onValueChange={(value) => update({ scope: (value ?? "") as GrantFormValues["scope"], projectId: "" })}
            options={[{ value: "cabinet", label: "Cabinet" }, { value: "project", label: "Project" }]}
            placeholder="Select a scope"
            required
            value={values.scope}
          />
          <SelectField
            disabled={values.scope !== "project" || !values.cabinetId}
            error={errors.projectId}
            id={`${formId}-project`}
            label="Project"
            onValueChange={(value) => update({ projectId: value ?? "" })}
            options={(projects.state.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }))}
            placeholder={values.scope === "project" ? "Select a project" : "Not applicable"}
            value={values.projectId}
          />
          <TextField
            error={errors.expiresAt}
            hint="Optional ISO-8601 date and time; must be in the future."
            id={`${formId}-expires`}
            label="Expires at"
            onChange={(event) => update({ expiresAt: event.currentTarget.value })}
            placeholder="2026-12-31T18:00"
            type="datetime-local"
            value={values.expiresAt}
          />
        </div>
        <TextField
          error={errors.reason}
          hint="Optional audit reason code (a-z, 0-9, underscore). Free text is not accepted: audit records stay redactable."
          id={`${formId}-reason`}
          label="Reason code"
          maxLength={64}
          onChange={(event) => update({ reason: event.currentTarget.value })}
          value={values.reason}
        />
        {cabinets.state.status === "error" && <DynamicModuleErrorNotice error={cabinets.state.error} onRetry={cabinets.reload} retryLabel="Reload cabinets" />}
        {projects.state.status === "error" && <DynamicModuleErrorNotice error={projects.state.error} onRetry={projects.reload} retryLabel="Reload projects" />}
        {grant.error && <DynamicModuleErrorNotice error={grant.error} />}
        {grant.notice && <p className="dynamic-modules__notice" role="status">{grant.notice}</p>}
        <div className="dynamic-modules__actions">
          <Button disabled={grant.busy} type="submit" variant="primary">{grant.busy ? "Granting…" : "Grant capability"}</Button>
        </div>
      </form>}
  </section>;
}
