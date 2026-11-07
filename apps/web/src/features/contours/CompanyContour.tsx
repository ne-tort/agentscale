import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useI18n } from "../../i18n/useT";
import type { ContourRow, ContourService, ContourInput } from "./service";
import { ConfirmAction, DataTable, Field, FormPanel, Notice, TextInput, useRows } from "./ui";
import "./contours.css";

export interface CompanyContourProps { companyId: string; service: ContourService }
type Tab = "employees" | "cabinets" | "keys" | "models";
type EmployeeLifecycle = "disable" | "enable" | "delete" | "restore";

/**
 * Copy key per lifecycle action. The action names stay in code; only the notice
 * a person reads is translated, so adding an action cannot silently produce an
 * English sentence on a Russian screen.
 */
const EMPLOYEE_NOTICES: Record<EmployeeLifecycle, string> = {
  disable: "contours.noticeEmployeeDisabled",
  enable: "contours.noticeEmployeeEnabled",
  delete: "contours.noticeEmployeeDeleted",
  restore: "contours.noticeEmployeeRestored",
};

function text(row: ContourRow, key: string): string { const value = row[key]; return typeof value === "string" ? value : ""; }
function splitIds(value: string): string[] { return value.split(",").map((part) => part.trim()).filter(Boolean); }
function employeeErrorMessage(error: unknown, fallback: string, ...secrets: string[]): string {
  const message = error instanceof Error ? error.message : fallback;
  return secrets.some((secret) => secret && message.includes(secret)) ? fallback : message;
}

function EmployeeConfirmAction({ label, confirmLabel, onConfirm, disabled = false, danger = true }: {
  label: string; confirmLabel?: string; onConfirm: () => Promise<boolean>; disabled?: boolean; danger?: boolean;
}) {
  const { t } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const [failed, setFailed] = useState(false);
  return confirming
    ? <span className="contour-confirm"><span>{label}?</span><button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => { void onConfirm().then((saved) => { if (saved) { setConfirming(false); setFailed(false); } else setFailed(true); }); }}>{failed ? t("common.retry") : confirmLabel ?? t("contours.confirm")}</button><button type="button" onClick={() => { setConfirming(false); setFailed(false); }} disabled={disabled}>{t("common.cancel")}</button></span>
    : <button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => setConfirming(true)}>{label}</button>;
}

export function CompanyContour({ companyId, service }: CompanyContourProps) {
  const { t } = useI18n();
  const tabs: Array<{ id: Tab; label: string }> = [
    { id: "employees", label: t("contours.tabEmployees") },
    { id: "cabinets", label: t("contours.tabCabinets") },
    { id: "keys", label: t("contours.tabKeys") },
    { id: "models", label: t("contours.tabModels") },
  ];
  const [tab, setTab] = useState<Tab>("employees");
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState<{ error?: string; success?: string }>({});
  const [busy, setBusy] = useState(false);
  const [showDeletedEmployees, setShowDeletedEmployees] = useState(false);
  const [showEmployeeCreate, setShowEmployeeCreate] = useState(false);
  const [newEmployeeLogin, setNewEmployeeLogin] = useState("");
  const [newEmployeePassword, setNewEmployeePassword] = useState("");
  const [newEmployeeName, setNewEmployeeName] = useState("");
  const [newEmployeeRole, setNewEmployeeRole] = useState<"member" | "company.admin">("member");
  const [newEmployeeEmail, setNewEmployeeEmail] = useState("");
  const [editingEmployee, setEditingEmployee] = useState<ContourRow>();
  const [employeeEmail, setEmployeeEmail] = useState("");
  const [employeePassword, setEmployeePassword] = useState("");
  const [selectedCabinet, setSelectedCabinet] = useState("");
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [showKeyForm, setShowKeyForm] = useState(false);
  const [editingKey, setEditingKey] = useState<ContourRow>();
  const [keyName, setKeyName] = useState("");
  const [keyProvider, setKeyProvider] = useState("openai");
  const [keyApiKind, setKeyApiKind] = useState("openai_api");
  const [keySecret, setKeySecret] = useState("");
  const [keyCatalogId, setKeyCatalogId] = useState("");
  const [keyRotateId, setKeyRotateId] = useState("");
  const [keyRotateSecret, setKeyRotateSecret] = useState("");
  const [confirmRotateSecret, setConfirmRotateSecret] = useState(false);
  const [keyModelsKeyId, setKeyModelsKeyId] = useState("");
  const [renewId, setRenewId] = useState("");
  const [renewMonths, setRenewMonths] = useState(1);
  const [showModelForm, setShowModelForm] = useState(false);
  const [editingModel, setEditingModel] = useState<ContourRow>();
  const [modelName, setModelName] = useState("");
  const [modelIds, setModelIds] = useState("");
  const [modelProvider, setModelProvider] = useState("");
  const [modelDescription, setModelDescription] = useState("");
  const [employeeData, setEmployeeData] = useState<{ companyId: string; deleted: boolean; rows: ContourRow[] }>({ companyId, deleted: showDeletedEmployees, rows: [] });
  const [employeeLoadState, setEmployeeLoadState] = useState<{ companyId: string; deleted: boolean; loading: boolean; error?: string }>({ companyId, deleted: showDeletedEmployees, loading: true });
  const employeeRequestRef = useRef(0);
  const employeeScopeRef = useRef({ companyId, showDeletedEmployees });
  employeeScopeRef.current = { companyId, showDeletedEmployees };
  const employees = {
    rows: employeeData.companyId === companyId && employeeData.deleted === showDeletedEmployees ? employeeData.rows : [],
    setRows: (update: ContourRow[] | ((rows: ContourRow[]) => ContourRow[])) => setEmployeeData((current) => {
      if (current.companyId !== companyId || current.deleted !== showDeletedEmployees) return current;
      return { ...current, rows: typeof update === "function" ? update(current.rows) : update };
    }),
    loading: employeeLoadState.companyId !== companyId || employeeLoadState.deleted !== showDeletedEmployees || employeeLoadState.loading,
    error: employeeLoadState.companyId === companyId && employeeLoadState.deleted === showDeletedEmployees ? employeeLoadState.error : undefined,
    refresh: async () => {
      const requestId = ++employeeRequestRef.current;
      const scope = employeeScopeRef.current;
      setEmployeeLoadState({ companyId: scope.companyId, deleted: scope.showDeletedEmployees, loading: true });
      try {
        const rows = await service.listEmployees(scope.companyId, scope.showDeletedEmployees);
        if (requestId === employeeRequestRef.current && employeeScopeRef.current.companyId === scope.companyId && employeeScopeRef.current.showDeletedEmployees === scope.showDeletedEmployees) {
          setEmployeeData({ companyId: scope.companyId, deleted: scope.showDeletedEmployees, rows });
          setEmployeeLoadState({ companyId: scope.companyId, deleted: scope.showDeletedEmployees, loading: false });
        }
      } catch (error) {
        if (requestId === employeeRequestRef.current && employeeScopeRef.current.companyId === scope.companyId && employeeScopeRef.current.showDeletedEmployees === scope.showDeletedEmployees) setEmployeeLoadState({ companyId: scope.companyId, deleted: scope.showDeletedEmployees, loading: false, error: error instanceof Error ? error.message : t("contours.employeeListFailed") });
      } finally {
        if (requestId === employeeRequestRef.current && employeeScopeRef.current.companyId === scope.companyId && employeeScopeRef.current.showDeletedEmployees === scope.showDeletedEmployees) setEmployeeLoadState((current) => ({ ...current, loading: false }));
      }
    },
  };
  useEffect(() => {
    void employees.refresh();
    return () => { employeeRequestRef.current += 1; };
  }, [service, companyId, showDeletedEmployees]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setEditingEmployee(undefined); setEmployeeEmail(""); setEmployeePassword("");
    setShowEmployeeCreate(false); setNewEmployeeLogin(""); setNewEmployeePassword(""); setNewEmployeeName(""); setNewEmployeeEmail("");
    setNotice({}); setBusy(false);
  }, [companyId, showDeletedEmployees]);
  const cabinets = useRows(() => service.listCompanyCabinets(companyId), [service, companyId]);
  const keys = useRows(() => service.listCompanyKeys(companyId), [service, companyId]);
  const models = useRows(() => service.listCompanyModels(companyId), [service, companyId]);
  const keyModels = useRows(async () => keyModelsKeyId ? service.listKeyModels(companyId, keyModelsKeyId) : [], [service, companyId, keyModelsKeyId]);
  const assignments = useRows(async () => selectedCabinet ? (await service.listAssignments(companyId, selectedCabinet)).map((row): ContourRow => ({ ...row, id: text(row, "employee_id") || row.id })) : [], [service, companyId, selectedCabinet]);
  const availableEmployees = useMemo(() => showDeletedEmployees ? [] : employees.rows.filter((employee) => !assignments.rows.some((assigned) => assigned.id === employee.id || assigned.employee_id === employee.id)), [assignments.rows, employees.rows, showDeletedEmployees]);
  const visibleEditingEmployee = employeeData.companyId === companyId ? editingEmployee : undefined;

  async function perform(action: () => Promise<unknown>, refresh: () => Promise<void>, success: string): Promise<boolean> {
    setBusy(true); setNotice({});
    try { await action(); await refresh(); setNotice({ success }); return true; }
    catch (error) { setNotice({ error: error instanceof Error ? error.message : t("contours.operationFailed") }); return false; }
    finally { setBusy(false); }
  }
  function resetKeyForm() { setShowKeyForm(false); setEditingKey(undefined); setKeyName(""); setKeyProvider("openai"); setKeyApiKind("openai_api"); setKeySecret(""); setKeyCatalogId(""); }
  function startEditKey(row: ContourRow) { setEditingKey(row); setKeyName(text(row, "name")); setKeyProvider(text(row, "provider") || "openai"); setKeyApiKind(text(row, "api_kind") || "openai_api"); setKeySecret(""); setKeyCatalogId(text(row, "catalog_entry_id")); setShowKeyForm(true); }
  function startEditModel(row: ContourRow) { setEditingModel(row); setModelName(text(row, "name")); setModelIds(Array.isArray(row.model_ids) ? row.model_ids.join(", ") : ""); setModelProvider(text(row, "provider")); setModelDescription(text(row, "description")); setShowModelForm(true); }
  async function saveEmployee(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!editingEmployee) return;
    const emailChanged = employeeEmail !== text(editingEmployee, "contact_email");
    if (!emailChanged && !employeePassword) {
      setEditingEmployee(undefined);
      setNotice({ success: t("contours.noticeNoEmployeeChanges") });
      return;
    }
    const requestCompanyId = companyId;
    const requestDeletedView = showDeletedEmployees;
    let verifiedEmployee = editingEmployee;
    setBusy(true); setNotice({});
    try {
      if (emailChanged) {
        verifiedEmployee = await service.updateEmployeeEmail(requestCompanyId, editingEmployee.id, employeeEmail.trim() || null, () => employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView);
        if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) employees.setRows((rows) => rows.map((row) => row.id === verifiedEmployee.id ? { ...row, ...verifiedEmployee } : row));
      }
      if (employeePassword) {
        verifiedEmployee = await service.setEmployeePassword(requestCompanyId, editingEmployee.id, employeePassword, () => employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView);
        if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) employees.setRows((rows) => rows.map((row) => row.id === verifiedEmployee.id ? { ...row, ...verifiedEmployee } : row));
      }
      if (employeeScopeRef.current.companyId !== requestCompanyId || employeeScopeRef.current.showDeletedEmployees !== requestDeletedView) return;
      employees.setRows((rows) => rows.map((row) => row.id === verifiedEmployee.id ? { ...row, ...verifiedEmployee } : row));
      setEditingEmployee(undefined); setNotice({ success: t("contours.noticeEmployeeSaved") });
    } catch (error) { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setNotice({ error: employeeErrorMessage(error, t("contours.employeeOperationFailed"), employeePassword) }); }
    finally { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) { setEmployeePassword(""); setBusy(false); } }
  }
  async function createEmployee(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestCompanyId = companyId;
    const requestDeletedView = showDeletedEmployees;
    setBusy(true); setNotice({});
    try {
      const saved = await service.createEmployee(requestCompanyId, {
        login: newEmployeeLogin.trim(), password: newEmployeePassword,
        display_name: newEmployeeName.trim() || null, role: newEmployeeRole,
        contact_email: newEmployeeEmail.trim() || null,
      }, () => employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView);
      if (employeeScopeRef.current.companyId !== requestCompanyId || employeeScopeRef.current.showDeletedEmployees !== requestDeletedView) return;
      if (!showDeletedEmployees) employees.setRows((rows) => [saved, ...rows.filter((row) => row.id !== saved.id)]);
      setShowEmployeeCreate(false); setNewEmployeeLogin(""); setNewEmployeeName(""); setNewEmployeeEmail("");
      setNewEmployeeRole("member"); setNotice({ success: t("contours.noticeEmployeeCreated") });
    } catch (error) {
      if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setNotice({ error: employeeErrorMessage(error, t("contours.employeeOperationFailed"), newEmployeePassword) });
    } finally { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) { setNewEmployeePassword(""); setBusy(false); } }
  }
  async function mutateEmployee(row: ContourRow, action: EmployeeLifecycle): Promise<boolean> {
    const requestCompanyId = companyId;
    const requestDeletedView = showDeletedEmployees;
    setBusy(true); setNotice({});
    try {
      const saved = await service[`${action}Employee`](requestCompanyId, row.id, () => employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView);
      if (employeeScopeRef.current.companyId !== requestCompanyId || employeeScopeRef.current.showDeletedEmployees !== requestDeletedView) return false;
      const movedToDeletedList = typeof saved.deleted_at === "string" && saved.deleted_at.trim().length > 0;
      employees.setRows((rows) => {
        const remaining = rows.filter((item) => item.id !== saved.id);
        return movedToDeletedList === showDeletedEmployees ? [saved, ...remaining] : remaining;
      });
      setNotice({ success: t(EMPLOYEE_NOTICES[action]) });
      return true;
    } catch (error) {
      if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setNotice({ error: employeeErrorMessage(error, t("contours.employeeOperationFailed")) });
      return false;
    } finally { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setBusy(false); }
  }
  async function saveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: ContourInput = { name: keyName.trim(), provider: keyProvider.trim(), api_kind: keyApiKind.trim(), catalog_entry_id: keyCatalogId.trim() || null };
    if (!editingKey && keySecret) body.secret = keySecret;
    if (!await perform(() => editingKey ? service.patchCompanyKey(companyId, editingKey.id, body) : service.createCompanyKey(companyId, body), keys.refresh, editingKey ? t("contours.noticeKeyUpdated") : t("contours.noticeKeyCreated"))) return;
    resetKeyForm();
  }
  async function saveModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: ContourInput = { name: modelName.trim(), model_ids: splitIds(modelIds), provider: modelProvider.trim() || null, description: modelDescription.trim() || null };
    if (!await perform(() => editingModel ? service.patchCompanyModel(companyId, editingModel.id, body) : service.createCompanyModel(companyId, body), models.refresh, editingModel ? t("contours.noticeModelUpdated") : t("contours.noticeModelCreated"))) return;
    setShowModelForm(false); setEditingModel(undefined); setModelName(""); setModelIds(""); setModelProvider(""); setModelDescription("");
  }
  async function toggleKeyModel(row: ContourRow, enabled: boolean) {
    if (!keyModelsKeyId) return;
    const selections = keyModels.rows.map((model) => ({ model_id: model.id, enabled: model.id === row.id ? enabled : Boolean(model.enabled), is_default: model.id === row.id ? enabled && Boolean(model.is_default) : Boolean(model.is_default) }));
    await perform(() => service.updateKeyModels(companyId, keyModelsKeyId, selections), keyModels.refresh, t("contours.noticeKeyModelsUpdated"));
  }
  async function setKeyModelDefault(row: ContourRow) {
    if (!keyModelsKeyId) return;
    const selections = keyModels.rows.map((model) => ({ model_id: model.id, enabled: model.id === row.id ? true : Boolean(model.enabled), is_default: model.id === row.id }));
    await perform(() => service.updateKeyModels(companyId, keyModelsKeyId, selections), keyModels.refresh, t("contours.noticeDefaultModelUpdated"));
  }
  const employeeColumns = [{ key: "display_name", label: t("contours.colEmployee") }, { key: "login", label: t("contours.colLogin") }, { key: "contact_email", label: t("contours.fieldContactEmail") }, { key: "role", label: t("contours.colRole") }, { key: "status", label: t("common.status") }, ...(showDeletedEmployees ? [{ key: "deleted_at", label: t("contours.colDeletedAt") }] : [])];
  const cabinetColumns = [{ key: "name", label: t("contours.colCabinet") }, { key: "id", label: t("contours.colId") }, { key: "status", label: t("common.status") }];
  const keyColumns = [{ key: "name", label: t("contours.colKey") }, { key: "provider", label: t("contours.colProvider") }, { key: "api_kind", label: t("contours.colApiKind") }, { key: "status", label: t("common.status") }, { key: "secret_hint", label: t("contours.colSecret") }];
  const modelColumns = [{ key: "name", label: t("contours.colModel") }, { key: "provider", label: t("contours.colProvider") }, { key: "model_ids", label: t("contours.colModelIds") }, { key: "reasoning_level", label: t("contours.colReasoning") }];

  return <main className="contours company-contour">
    <header className="contour-heading" title={companyId}><div><p className="contour-eyebrow">{t("contours.eyebrow")}</p><h1>{t("contours.title")}</h1><p className="contour-help">{t("contours.help")}</p></div></header>
    <nav className="contour-tabs" aria-label={t("contours.eyebrow")}>{tabs.map((item) => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} onClick={() => { setTab(item.id); setQuery(""); setNotice({}); }}>{item.label}</button>)}</nav>
    <Notice {...notice} />
    {tab === "employees" && <section className="contour-panel"><div className="contour-heading"><div><h2>{t("contours.tabEmployees")}</h2><Field label={t("contours.employeeListFilter")}><select value={showDeletedEmployees ? "deleted" : "active"} onChange={(event) => { setShowDeletedEmployees(event.target.value === "deleted"); setQuery(""); setEditingEmployee(undefined); setEmployeePassword(""); setShowEmployeeCreate(false); setNewEmployeePassword(""); }}><option value="active">{t("contours.employeesViewActive")}</option><option value="deleted">{t("contours.employeesViewDeleted")}</option></select></Field></div>{!showDeletedEmployees && <button type="button" disabled={busy} onClick={() => { setShowEmployeeCreate((value) => !value); setNewEmployeePassword(""); }}>{showEmployeeCreate ? t("contours.closeCreateForm") : t("contours.addEmployee")}</button>}</div>
      {showEmployeeCreate && !showDeletedEmployees && <FormPanel title={t("contours.createEmployeeTitle")} onSubmit={(event) => void createEmployee(event)} submitLabel={t("contours.createEmployeeSubmit")} busy={busy} onCancel={() => { setShowEmployeeCreate(false); setNewEmployeePassword(""); }}><Field label={t("contours.fieldLogin")}><TextInput required minLength={3} maxLength={64} autoComplete="username" value={newEmployeeLogin} onChange={(event) => setNewEmployeeLogin(event.target.value)} /></Field><Field label={t("contours.fieldPassword")}><TextInput required type="password" minLength={8} maxLength={200} autoComplete="new-password" value={newEmployeePassword} onChange={(event) => setNewEmployeePassword(event.target.value)} /></Field><Field label={t("contours.fieldDisplayName")}><TextInput value={newEmployeeName} onChange={(event) => setNewEmployeeName(event.target.value)} /></Field><Field label={t("contours.fieldRole")}><select aria-label={t("contours.fieldRole")} value={newEmployeeRole} onChange={(event) => setNewEmployeeRole(event.target.value as "member" | "company.admin")}><option value="member">{t("contours.roleMember")}</option><option value="company.admin">{t("contours.roleCompanyAdmin")}</option></select></Field><Field label={t("contours.fieldContactEmail")}><TextInput type="email" maxLength={320} value={newEmployeeEmail} onChange={(event) => setNewEmployeeEmail(event.target.value)} /></Field></FormPanel>}
      <DataTable rows={employees.rows} columns={employeeColumns} loading={employees.loading} error={employees.error} onRetry={() => void employees.refresh()} query={query} onQuery={setQuery} actions={(row) => showDeletedEmployees
        ? <EmployeeConfirmAction label={t("common.restore")} confirmLabel={t("contours.confirmRestore")} disabled={busy} danger={false} onConfirm={() => mutateEmployee(row, "restore")} />
        : <><button type="button" disabled={busy} onClick={() => { setEditingEmployee(row); setEmployeeEmail(text(row, "contact_email")); setEmployeePassword(""); }}>{t("contours.editContactPassword")}</button>{text(row, "status") === "disabled" ? <button type="button" disabled={busy} onClick={() => void mutateEmployee(row, "enable")}>{t("contours.enable")}</button> : <EmployeeConfirmAction label={t("contours.disable")} confirmLabel={t("contours.confirmDisable")} disabled={busy} onConfirm={() => mutateEmployee(row, "disable")} />}<EmployeeConfirmAction label={t("common.delete")} confirmLabel={t("contours.confirmDelete")} disabled={busy} onConfirm={() => mutateEmployee(row, "delete")} /></>} empty={showDeletedEmployees ? t("contours.employeeDeletedEmpty") : t("contours.employeeEmpty")} />
      {visibleEditingEmployee && !showDeletedEmployees && <FormPanel title={t("contours.updateEmployeeTitle", { name: text(visibleEditingEmployee, "display_name") || visibleEditingEmployee.id })} onSubmit={(event) => void saveEmployee(event)} submitLabel={t("contours.saveEmployee")} busy={busy} onCancel={() => setEditingEmployee(undefined)}><Field label={t("contours.fieldContactEmail")}><TextInput type="email" maxLength={320} value={employeeEmail} onChange={(event) => setEmployeeEmail(event.target.value)} /></Field><Field label={t("contours.fieldNewPassword")}><TextInput type="password" minLength={8} maxLength={200} value={employeePassword} onChange={(event) => setEmployeePassword(event.target.value)} autoComplete="new-password" /></Field></FormPanel>}
    </section>}
    {tab === "cabinets" && <section className="contour-panel"><h2>{t("contours.cabinetsTitle")}</h2>
      <div className="contour-inline"><Field label={t("contours.fieldCabinet")}><select value={selectedCabinet} onChange={(event) => setSelectedCabinet(event.target.value)}><option value="">{t("contours.chooseCabinet")}</option>{cabinets.rows.map((row) => <option key={row.id} value={row.id}>{text(row, "name") || row.id}</option>)}</select></Field><Field label={t("contours.fieldEmployeeToAssign")}><select value={selectedEmployee} onChange={(event) => setSelectedEmployee(event.target.value)} disabled={!selectedCabinet || availableEmployees.length === 0}><option value="">{t("contours.chooseEmployee")}</option>{availableEmployees.map((row) => <option key={row.id} value={row.id}>{text(row, "display_name") || row.id}</option>)}</select></Field><button type="button" disabled={busy || !selectedCabinet || !selectedEmployee} onClick={() => void perform(() => service.assignEmployee(companyId, selectedCabinet, selectedEmployee), assignments.refresh, t("contours.noticeEmployeeAssigned")).then((saved) => { if (saved) setSelectedEmployee(""); })}>{t("contours.assign")}</button></div>
      {cabinets.error && <Notice error={cabinets.error} />} {!cabinets.loading && cabinets.rows.length === 0 && !cabinets.error && <p className="contour-state">{t("contours.cabinetsUnavailable")}</p>}
      {selectedCabinet && <><h3>{t("contours.assignedEmployees")}</h3><DataTable rows={assignments.rows} columns={[{ key: "employee_email", label: t("contours.colEmployee") }, { key: "employee_id", label: t("contours.colEmployeeId") }, { key: "role", label: t("contours.colRole") }, { key: "status", label: t("contours.colAssignmentStatus") }]} loading={assignments.loading} error={assignments.error} onRetry={() => void assignments.refresh()} query={query} onQuery={setQuery} actions={(row) => <ConfirmAction label={t("contours.revokeAccess")} disabled={busy} onConfirm={() => void perform(() => service.revokeAssignment(companyId, selectedCabinet, text(row, "employee_id") || row.id), assignments.refresh, t("contours.noticeAssignmentRevoked"))} />} empty={t("contours.assignmentsEmpty")} rowLabel={(row) => text(row, "display_name") || row.id} /></>}
      {!selectedCabinet && <p className="contour-state">{t("contours.selectCabinetHint")}</p>}
    </section>}
    {tab === "keys" && <section className="contour-panel"><div className="contour-heading"><h2>{t("contours.keysTitle")}</h2><button type="button" onClick={() => { setEditingKey(undefined); setShowKeyForm((value) => !value); }}>{t("contours.addKey")}</button></div>
      {showKeyForm && <FormPanel title={editingKey ? t("contours.editKeyTitle") : t("contours.createKeyTitle")} onSubmit={(event) => void saveKey(event)} submitLabel={editingKey ? t("contours.saveKey") : t("contours.createKey")} busy={busy} onCancel={resetKeyForm}><Field label={t("common.name")}><TextInput required maxLength={200} value={keyName} onChange={(event) => setKeyName(event.target.value)} /></Field><Field label={t("contours.fieldProvider")}><TextInput required value={keyProvider} onChange={(event) => setKeyProvider(event.target.value)} /></Field><Field label={t("contours.fieldApiKind")}><TextInput required value={keyApiKind} onChange={(event) => setKeyApiKind(event.target.value)} /></Field><Field label={t("contours.fieldCatalogEntry")}><TextInput maxLength={64} value={keyCatalogId} onChange={(event) => setKeyCatalogId(event.target.value)} /></Field><Field label={editingKey ? t("contours.fieldSecretLocked") : t("contours.fieldSecretOptional")}><TextInput type="password" value={keySecret} onChange={(event) => setKeySecret(event.target.value)} autoComplete="new-password" disabled={Boolean(editingKey)} /></Field></FormPanel>}
      <DataTable rows={keys.rows.map((row) => ({ ...row, secret_hint: row.has_secret ? t("contours.secretConfigured") : t("contours.secretMissing") }))} columns={keyColumns} loading={keys.loading} error={keys.error} onRetry={() => void keys.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => startEditKey(row)}>{t("common.edit")}</button><button type="button" disabled={busy} onClick={() => { setKeyModelsKeyId(row.id); setRenewId(""); setKeyRotateId(""); }}>{t("contours.openModels")}</button><button type="button" disabled={busy} onClick={() => { setRenewId(row.id); setKeyRotateId(""); setKeyModelsKeyId(""); }}>{t("contours.renew")}</button><button type="button" disabled={busy} onClick={() => { setKeyRotateId(row.id); setRenewId(""); setKeyModelsKeyId(""); setKeyRotateSecret(""); setConfirmRotateSecret(false); }}>{t("contours.rotateSecret")}</button><ConfirmAction label={t("common.delete")} disabled={busy} onConfirm={() => void perform(() => service.deleteCompanyKey(companyId, row.id), keys.refresh, t("contours.noticeKeyDeleted"))} /></>} empty={t("contours.keysEmpty")} />
      {keyModelsKeyId && <section className="contour-panel"><div className="contour-heading"><div><h3>{t("contours.keyModelsTitle")}</h3><p className="contour-help">{t("contours.keyModelsHelp")}</p></div><button type="button" onClick={() => setKeyModelsKeyId("")}>{t("common.close")}</button></div><DataTable rows={keyModels.rows} columns={[{ key: "name", label: t("contours.colModel") }, { key: "provider", label: t("contours.colProvider") }, { key: "model_ids", label: t("contours.colModelIds") }, { key: "enabled", label: t("contours.colEnabled") }, { key: "is_default", label: t("contours.colDefault") }]} loading={keyModels.loading} error={keyModels.error} onRetry={() => void keyModels.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => void toggleKeyModel(row, !Boolean(row.enabled))}>{row.enabled ? t("contours.disable") : t("contours.enable")}</button><button type="button" disabled={busy || !row.enabled} onClick={() => void setKeyModelDefault(row)}>{row.is_default ? t("contours.defaultWord") : t("contours.makeDefault")}</button></>} empty={t("contours.keyModelsEmpty")} /></section>}
      {renewId && <FormPanel title={t("contours.renewTitle")} onSubmit={(event) => { event.preventDefault(); void perform(() => service.renewCompanyKey(companyId, renewId, renewMonths), keys.refresh, t("contours.noticeKeyRenewed")).then((saved) => { if (saved) setRenewId(""); }); }} submitLabel={t("contours.renewSubmit")} busy={busy} onCancel={() => setRenewId("")}><Field label={t("contours.fieldMonths")}><TextInput type="number" min={1} max={24} value={renewMonths} onChange={(event) => setRenewMonths(Number(event.target.value))} /></Field></FormPanel>}
      {keyRotateId && <FormPanel title={t("contours.rotateTitle")} onSubmit={(event) => { event.preventDefault(); if (!confirmRotateSecret) return; void perform(() => service.rotateCompanyKey(companyId, keyRotateId, keyRotateSecret), keys.refresh, t("contours.noticeSecretRotated")).then((saved) => { if (saved) { setKeyRotateId(""); setKeyRotateSecret(""); setConfirmRotateSecret(false); } }); }} submitLabel={t("contours.rotateSubmit")} busy={busy} onCancel={() => { setKeyRotateId(""); setConfirmRotateSecret(false); }}><Field label={t("contours.fieldNewSecret")}><TextInput type="password" value={keyRotateSecret} onChange={(event) => setKeyRotateSecret(event.target.value)} minLength={1} required autoComplete="new-password" /></Field><label className="contour-field"><span><input type="checkbox" checked={confirmRotateSecret} onChange={(event) => setConfirmRotateSecret(event.target.checked)} required /> {t("contours.rotateAcknowledgement")}</span></label></FormPanel>}
    </section>}
    {tab === "models" && <section className="contour-panel"><div className="contour-heading"><h2>{t("contours.modelsTitle")}</h2><button type="button" onClick={() => { setEditingModel(undefined); setShowModelForm((value) => !value); }}>{t("contours.addModel")}</button></div>
      {showModelForm && <FormPanel title={editingModel ? t("contours.editModelTitle") : t("contours.createModelTitle")} onSubmit={(event) => void saveModel(event)} submitLabel={editingModel ? t("contours.saveModel") : t("contours.createModel")} busy={busy} onCancel={() => { setShowModelForm(false); setEditingModel(undefined); }}><Field label={t("contours.fieldDisplayName")}><TextInput required maxLength={128} value={modelName} onChange={(event) => setModelName(event.target.value)} /></Field><Field label={t("contours.fieldProvider")}><TextInput maxLength={64} value={modelProvider} onChange={(event) => setModelProvider(event.target.value)} /></Field><Field label={t("contours.fieldModelIds")}><TextInput value={modelIds} onChange={(event) => setModelIds(event.target.value)} /></Field><Field label={t("contours.fieldDescription")}><TextInput maxLength={512} value={modelDescription} onChange={(event) => setModelDescription(event.target.value)} /></Field></FormPanel>}
      <DataTable rows={models.rows} columns={modelColumns} loading={models.loading} error={models.error} onRetry={() => void models.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => startEditModel(row)}>{t("common.edit")}</button><ConfirmAction label={t("common.delete")} disabled={busy} onConfirm={() => void perform(() => service.deleteCompanyModel(companyId, row.id), models.refresh, t("contours.noticeModelDeleted"))} /></>} empty={t("contours.modelsEmpty")} />
    </section>}
  </main>;
}
