import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { ContourRow, ContourService, ContourInput } from "./service";
import { ConfirmAction, DataTable, Field, FormPanel, Notice, TextInput, useRows } from "./ui";
import "./contours.css";

export interface CompanyContourProps { companyId: string; service: ContourService }
type Tab = "employees" | "cabinets" | "keys" | "models";
const tabs: Array<{ id: Tab; label: string }> = [
  { id: "employees", label: "Employees" }, { id: "cabinets", label: "Cabinet access" },
  { id: "keys", label: "AI keys" }, { id: "models", label: "AI models" },
];
function text(row: ContourRow, key: string): string { const value = row[key]; return typeof value === "string" ? value : ""; }
function splitIds(value: string): string[] { return value.split(",").map((part) => part.trim()).filter(Boolean); }
function employeeErrorMessage(error: unknown, ...secrets: string[]): string {
  const message = error instanceof Error ? error.message : "Employee operation failed.";
  return secrets.some((secret) => secret && message.includes(secret)) ? "Employee operation failed." : message;
}

function EmployeeConfirmAction({ label, confirmLabel = "Confirm", onConfirm, disabled = false, danger = true }: {
  label: string; confirmLabel?: string; onConfirm: () => Promise<boolean>; disabled?: boolean; danger?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const [failed, setFailed] = useState(false);
  return confirming
    ? <span className="contour-confirm"><span>{label}?</span><button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => { void onConfirm().then((saved) => { if (saved) { setConfirming(false); setFailed(false); } else setFailed(true); }); }}>{failed ? "Retry" : confirmLabel}</button><button type="button" onClick={() => { setConfirming(false); setFailed(false); }} disabled={disabled}>Cancel</button></span>
    : <button type="button" className={danger ? "contour-danger" : ""} disabled={disabled} onClick={() => setConfirming(true)}>{label}</button>;
}

export function CompanyContour({ companyId, service }: CompanyContourProps) {
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
        if (requestId === employeeRequestRef.current && employeeScopeRef.current.companyId === scope.companyId && employeeScopeRef.current.showDeletedEmployees === scope.showDeletedEmployees) setEmployeeLoadState({ companyId: scope.companyId, deleted: scope.showDeletedEmployees, loading: false, error: error instanceof Error ? error.message : "Employee list could not be loaded." });
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
    catch (error) { setNotice({ error: error instanceof Error ? error.message : "Operation failed." }); return false; }
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
      setNotice({ success: "No employee changes to save." });
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
      setEditingEmployee(undefined); setNotice({ success: "Employee details saved and verified." });
    } catch (error) { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setNotice({ error: employeeErrorMessage(error, employeePassword) }); }
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
      setNewEmployeeRole("member"); setNotice({ success: "Employee created and verified." });
    } catch (error) {
      if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setNotice({ error: employeeErrorMessage(error, newEmployeePassword) });
    } finally { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) { setNewEmployeePassword(""); setBusy(false); } }
  }
  async function mutateEmployee(row: ContourRow, action: "disable" | "enable" | "delete" | "restore"): Promise<boolean> {
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
      setNotice({ success: `Employee ${action === "delete" ? "deleted" : action === "restore" ? "restored" : action + "d"} and verified.` });
      return true;
    } catch (error) {
      if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setNotice({ error: employeeErrorMessage(error) });
      return false;
    } finally { if (employeeScopeRef.current.companyId === requestCompanyId && employeeScopeRef.current.showDeletedEmployees === requestDeletedView) setBusy(false); }
  }
  async function saveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: ContourInput = { name: keyName.trim(), provider: keyProvider.trim(), api_kind: keyApiKind.trim(), catalog_entry_id: keyCatalogId.trim() || null };
    if (!editingKey && keySecret) body.secret = keySecret;
    if (!await perform(() => editingKey ? service.patchCompanyKey(companyId, editingKey.id, body) : service.createCompanyKey(companyId, body), keys.refresh, editingKey ? "AI key updated." : "AI key created.")) return;
    resetKeyForm();
  }
  async function saveModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: ContourInput = { name: modelName.trim(), model_ids: splitIds(modelIds), provider: modelProvider.trim() || null, description: modelDescription.trim() || null };
    if (!await perform(() => editingModel ? service.patchCompanyModel(companyId, editingModel.id, body) : service.createCompanyModel(companyId, body), models.refresh, editingModel ? "AI model updated." : "AI model created.")) return;
    setShowModelForm(false); setEditingModel(undefined); setModelName(""); setModelIds(""); setModelProvider(""); setModelDescription("");
  }
  async function toggleKeyModel(row: ContourRow, enabled: boolean) {
    if (!keyModelsKeyId) return;
    const selections = keyModels.rows.map((model) => ({ model_id: model.id, enabled: model.id === row.id ? enabled : Boolean(model.enabled), is_default: model.id === row.id ? enabled && Boolean(model.is_default) : Boolean(model.is_default) }));
    await perform(() => service.updateKeyModels(companyId, keyModelsKeyId, selections), keyModels.refresh, "Key model access updated.");
  }
  async function setKeyModelDefault(row: ContourRow) {
    if (!keyModelsKeyId) return;
    const selections = keyModels.rows.map((model) => ({ model_id: model.id, enabled: model.id === row.id ? true : Boolean(model.enabled), is_default: model.id === row.id }));
    await perform(() => service.updateKeyModels(companyId, keyModelsKeyId, selections), keyModels.refresh, "Default key model updated.");
  }
  const employeeColumns = [{ key: "display_name", label: "Employee" }, { key: "login", label: "Login" }, { key: "contact_email", label: "Contact email" }, { key: "role", label: "Role" }, { key: "status", label: "Status" }, ...(showDeletedEmployees ? [{ key: "deleted_at", label: "Deleted at" }] : [])];
  const cabinetColumns = [{ key: "name", label: "Cabinet" }, { key: "id", label: "ID" }, { key: "status", label: "Status" }];
  const keyColumns = [{ key: "name", label: "Key" }, { key: "provider", label: "Provider" }, { key: "api_kind", label: "API kind" }, { key: "status", label: "Status" }, { key: "secret_hint", label: "Secret" }];
  const modelColumns = [{ key: "name", label: "Model" }, { key: "provider", label: "Provider" }, { key: "model_ids", label: "Model IDs" }, { key: "reasoning_level", label: "Reasoning" }];

  return <main className="contours company-contour">
    <header className="contour-heading"><div><p className="contour-eyebrow">Company administration</p><h1>Company contour</h1><p className="contour-help">Employees, cabinet access, provider keys and model catalog · {companyId}</p></div></header>
    <nav className="contour-tabs" aria-label="Company administration">{tabs.map((item) => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} onClick={() => { setTab(item.id); setQuery(""); setNotice({}); }}>{item.label}</button>)}</nav>
    <Notice {...notice} />
    {tab === "employees" && <section className="contour-panel"><div className="contour-heading"><div><h2>Employees</h2><Field label="Employee list"><select value={showDeletedEmployees ? "deleted" : "active"} onChange={(event) => { setShowDeletedEmployees(event.target.value === "deleted"); setQuery(""); setEditingEmployee(undefined); setEmployeePassword(""); setShowEmployeeCreate(false); setNewEmployeePassword(""); }}><option value="active">Active and disabled</option><option value="deleted">Deleted</option></select></Field></div>{!showDeletedEmployees && <button type="button" disabled={busy} onClick={() => { setShowEmployeeCreate((value) => !value); setNewEmployeePassword(""); }}>{showEmployeeCreate ? "Close create form" : "Add employee"}</button>}</div>
      {showEmployeeCreate && !showDeletedEmployees && <FormPanel title="Create employee" onSubmit={(event) => void createEmployee(event)} submitLabel="Create employee" busy={busy} onCancel={() => { setShowEmployeeCreate(false); setNewEmployeePassword(""); }}><Field label="Login"><TextInput required minLength={3} maxLength={64} autoComplete="username" value={newEmployeeLogin} onChange={(event) => setNewEmployeeLogin(event.target.value)} /></Field><Field label="Password"><TextInput required type="password" minLength={8} maxLength={200} autoComplete="new-password" value={newEmployeePassword} onChange={(event) => setNewEmployeePassword(event.target.value)} /></Field><Field label="Display name"><TextInput value={newEmployeeName} onChange={(event) => setNewEmployeeName(event.target.value)} /></Field><Field label="Role"><select aria-label="Role" value={newEmployeeRole} onChange={(event) => setNewEmployeeRole(event.target.value as "member" | "company.admin")}><option value="member">Member</option><option value="company.admin">Company admin</option></select></Field><Field label="Contact email"><TextInput type="email" maxLength={320} value={newEmployeeEmail} onChange={(event) => setNewEmployeeEmail(event.target.value)} /></Field></FormPanel>}
      <DataTable rows={employees.rows} columns={employeeColumns} loading={employees.loading} error={employees.error} onRetry={() => void employees.refresh()} query={query} onQuery={setQuery} actions={(row) => showDeletedEmployees
        ? <EmployeeConfirmAction label="Restore" confirmLabel="Confirm restore" disabled={busy} danger={false} onConfirm={() => mutateEmployee(row, "restore")} />
        : <><button type="button" disabled={busy} onClick={() => { setEditingEmployee(row); setEmployeeEmail(text(row, "contact_email")); setEmployeePassword(""); }}>{"Edit contact / password"}</button>{text(row, "status") === "disabled" ? <button type="button" disabled={busy} onClick={() => void mutateEmployee(row, "enable")}>Enable</button> : <EmployeeConfirmAction label="Disable" confirmLabel="Confirm disable" disabled={busy} onConfirm={() => mutateEmployee(row, "disable")} />}<EmployeeConfirmAction label="Delete" confirmLabel="Confirm delete" disabled={busy} onConfirm={() => mutateEmployee(row, "delete")} /></>} empty={showDeletedEmployees ? "No deleted employees are linked to this company." : "No employees are linked to this company."} />
      {visibleEditingEmployee && !showDeletedEmployees && <FormPanel title={`Update ${text(visibleEditingEmployee, "display_name") || visibleEditingEmployee.id}`} onSubmit={(event) => void saveEmployee(event)} submitLabel="Save employee" busy={busy} onCancel={() => setEditingEmployee(undefined)}><Field label="Contact email"><TextInput type="email" maxLength={320} value={employeeEmail} onChange={(event) => setEmployeeEmail(event.target.value)} /></Field><Field label="New password (leave blank to keep current)"><TextInput type="password" minLength={8} maxLength={200} value={employeePassword} onChange={(event) => setEmployeePassword(event.target.value)} autoComplete="new-password" /></Field></FormPanel>}
    </section>}
    {tab === "cabinets" && <section className="contour-panel"><h2>Cabinet assignments</h2>
      <div className="contour-inline"><Field label="Cabinet"><select value={selectedCabinet} onChange={(event) => setSelectedCabinet(event.target.value)}><option value="">Choose cabinet</option>{cabinets.rows.map((row) => <option key={row.id} value={row.id}>{text(row, "name") || row.id}</option>)}</select></Field><Field label="Employee to assign"><select value={selectedEmployee} onChange={(event) => setSelectedEmployee(event.target.value)} disabled={!selectedCabinet || availableEmployees.length === 0}><option value="">Choose employee</option>{availableEmployees.map((row) => <option key={row.id} value={row.id}>{text(row, "display_name") || row.id}</option>)}</select></Field><button type="button" disabled={busy || !selectedCabinet || !selectedEmployee} onClick={() => void perform(() => service.assignEmployee(companyId, selectedCabinet, selectedEmployee), assignments.refresh, "Employee assigned to cabinet.").then((saved) => { if (saved) setSelectedEmployee(""); })}>Assign</button></div>
      {cabinets.error && <Notice error={cabinets.error} />} {!cabinets.loading && cabinets.rows.length === 0 && !cabinets.error && <p className="contour-state">No company cabinets are available.</p>}
      {selectedCabinet && <><h3>Assigned employees</h3><DataTable rows={assignments.rows} columns={[{ key: "employee_email", label: "Employee" }, { key: "employee_id", label: "Employee ID" }, { key: "role", label: "Role" }, { key: "status", label: "Assignment status" }]} loading={assignments.loading} error={assignments.error} onRetry={() => void assignments.refresh()} query={query} onQuery={setQuery} actions={(row) => <ConfirmAction label="Revoke access" disabled={busy} onConfirm={() => void perform(() => service.revokeAssignment(companyId, selectedCabinet, text(row, "employee_id") || row.id), assignments.refresh, "Cabinet assignment revoked.")} />} empty="No employees are assigned to this cabinet." rowLabel={(row) => text(row, "display_name") || row.id} /></>}
      {!selectedCabinet && <p className="contour-state">Select a cabinet to view or edit employee assignments.</p>}
    </section>}
    {tab === "keys" && <section className="contour-panel"><div className="contour-heading"><h2>AI provider keys</h2><button type="button" onClick={() => { setEditingKey(undefined); setShowKeyForm((value) => !value); }}>＋ Add key</button></div>
      {showKeyForm && <FormPanel title={editingKey ? "Edit AI key" : "Create AI key"} onSubmit={(event) => void saveKey(event)} submitLabel={editingKey ? "Save key" : "Create key"} busy={busy} onCancel={resetKeyForm}><Field label="Name"><TextInput required maxLength={200} value={keyName} onChange={(event) => setKeyName(event.target.value)} /></Field><Field label="Provider"><TextInput required value={keyProvider} onChange={(event) => setKeyProvider(event.target.value)} /></Field><Field label="API kind"><TextInput required value={keyApiKind} onChange={(event) => setKeyApiKind(event.target.value)} /></Field><Field label="Provider catalog entry ID (optional)"><TextInput maxLength={64} value={keyCatalogId} onChange={(event) => setKeyCatalogId(event.target.value)} /></Field><Field label={editingKey ? "Secret changes are available through Rotate secret" : "Secret (optional)"}><TextInput type="password" value={keySecret} onChange={(event) => setKeySecret(event.target.value)} autoComplete="new-password" disabled={Boolean(editingKey)} /></Field></FormPanel>}
      <DataTable rows={keys.rows.map((row) => ({ ...row, secret_hint: row.has_secret ? "Configured" : "Not configured" }))} columns={keyColumns} loading={keys.loading} error={keys.error} onRetry={() => void keys.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => startEditKey(row)}>Edit</button><button type="button" disabled={busy} onClick={() => { setKeyModelsKeyId(row.id); setRenewId(""); setKeyRotateId(""); }}>Models</button><button type="button" disabled={busy} onClick={() => { setRenewId(row.id); setKeyRotateId(""); setKeyModelsKeyId(""); }}>Renew</button><button type="button" disabled={busy} onClick={() => { setKeyRotateId(row.id); setRenewId(""); setKeyModelsKeyId(""); setKeyRotateSecret(""); setConfirmRotateSecret(false); }}>Rotate secret</button><ConfirmAction label="Delete" disabled={busy} onConfirm={() => void perform(() => service.deleteCompanyKey(companyId, row.id), keys.refresh, "AI key deleted.")} /></>} empty="No AI keys configured." />
      {keyModelsKeyId && <section className="contour-panel"><div className="contour-heading"><div><h3>Models available to key</h3><p className="contour-help">Toggle access and choose one default. The API enforces company visibility and owner restrictions.</p></div><button type="button" onClick={() => setKeyModelsKeyId("")}>Close</button></div><DataTable rows={keyModels.rows} columns={[{ key: "name", label: "Model" }, { key: "provider", label: "Provider" }, { key: "model_ids", label: "Model IDs" }, { key: "enabled", label: "Enabled" }, { key: "is_default", label: "Default" }]} loading={keyModels.loading} error={keyModels.error} onRetry={() => void keyModels.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => void toggleKeyModel(row, !Boolean(row.enabled))}>{row.enabled ? "Disable" : "Enable"}</button><button type="button" disabled={busy || !row.enabled} onClick={() => void setKeyModelDefault(row)}>{row.is_default ? "Default" : "Make default"}</button></>} empty="No models are visible to this key." /></section>}
      {renewId && <FormPanel title="Renew key" onSubmit={(event) => { event.preventDefault(); void perform(() => service.renewCompanyKey(companyId, renewId, renewMonths), keys.refresh, "AI key renewed.").then((saved) => { if (saved) setRenewId(""); }); }} submitLabel="Renew" busy={busy} onCancel={() => setRenewId("")}><Field label="Months (1–24)"><TextInput type="number" min={1} max={24} value={renewMonths} onChange={(event) => setRenewMonths(Number(event.target.value))} /></Field></FormPanel>}
      {keyRotateId && <FormPanel title="Rotate key secret" onSubmit={(event) => { event.preventDefault(); if (!confirmRotateSecret) return; void perform(() => service.rotateCompanyKey(companyId, keyRotateId, keyRotateSecret), keys.refresh, "AI key secret rotated.").then((saved) => { if (saved) { setKeyRotateId(""); setKeyRotateSecret(""); setConfirmRotateSecret(false); } }); }} submitLabel="Rotate" busy={busy} onCancel={() => { setKeyRotateId(""); setConfirmRotateSecret(false); }}><Field label="New provider secret"><TextInput type="password" value={keyRotateSecret} onChange={(event) => setKeyRotateSecret(event.target.value)} minLength={1} required autoComplete="new-password" /></Field><label className="contour-field"><span><input type="checkbox" checked={confirmRotateSecret} onChange={(event) => setConfirmRotateSecret(event.target.checked)} required /> I understand that the old secret will stop working.</span></label></FormPanel>}
    </section>}
    {tab === "models" && <section className="contour-panel"><div className="contour-heading"><h2>AI models</h2><button type="button" onClick={() => { setEditingModel(undefined); setShowModelForm((value) => !value); }}>＋ Add model</button></div>
      {showModelForm && <FormPanel title={editingModel ? "Edit AI model" : "Create AI model"} onSubmit={(event) => void saveModel(event)} submitLabel={editingModel ? "Save model" : "Create model"} busy={busy} onCancel={() => { setShowModelForm(false); setEditingModel(undefined); }}><Field label="Display name"><TextInput required maxLength={128} value={modelName} onChange={(event) => setModelName(event.target.value)} /></Field><Field label="Provider"><TextInput maxLength={64} value={modelProvider} onChange={(event) => setModelProvider(event.target.value)} /></Field><Field label="Model IDs (comma separated)"><TextInput value={modelIds} onChange={(event) => setModelIds(event.target.value)} /></Field><Field label="Description"><TextInput maxLength={512} value={modelDescription} onChange={(event) => setModelDescription(event.target.value)} /></Field></FormPanel>}
      <DataTable rows={models.rows} columns={modelColumns} loading={models.loading} error={models.error} onRetry={() => void models.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => startEditModel(row)}>Edit</button><ConfirmAction label="Delete" disabled={busy} onConfirm={() => void perform(() => service.deleteCompanyModel(companyId, row.id), models.refresh, "AI model deleted.")} /></>} empty="No company AI models configured." />
    </section>}
  </main>;
}
