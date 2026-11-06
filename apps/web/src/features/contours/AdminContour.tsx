import { useState, type FormEvent } from "react";
import type { ContourInput, ContourRow, ContourService } from "./service";
import { ConfirmAction, DataTable, Field, FormPanel, Notice, TextInput, useRows } from "./ui";
import "./contours.css";

export interface AdminContourProps { service: ContourService }
type Tab = "companies" | "cabinets" | "modules" | "providers" | "metrics" | "recycle";
const tabs: Array<{ id: Tab; label: string }> = [
  { id: "companies", label: "Companies" }, { id: "cabinets", label: "Cabinets" },
  { id: "modules", label: "Modules" }, { id: "providers", label: "AI providers" }, { id: "metrics", label: "Metrics" }, { id: "recycle", label: "Recycle bin" },
];
function text(row: Record<string, unknown> | undefined, key: string): string { const value = row?.[key]; return typeof value === "string" ? value : ""; }
function split(value: string): string[] { return value.split(",").map((part) => part.trim()).filter(Boolean); }
function normalizeRows(rows: ContourInput[]): ContourRow[] { return rows.map((row, index) => ({ ...row, id: text(row, "id") || text(row, "company_id") || text(row, "project_id") || text(row, "entity_id") || `row-${index}` })); }

export function AdminContour({ service }: AdminContourProps) {
  const [tab, setTab] = useState<Tab>("companies");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ error?: string; success?: string }>({});
  const [showCompanyForm, setShowCompanyForm] = useState(false);
  const [company, setCompany] = useState<ContourRow>();
  const [companyName, setCompanyName] = useState("");
  const [companyEmail, setCompanyEmail] = useState("");
  const [companyPhone, setCompanyPhone] = useState("");
  const [quotaCompany, setQuotaCompany] = useState<ContourRow>();
  const [quota, setQuota] = useState({ max_cabinets: "10", max_packages_per_cabinet: "50", max_bundle_import_mb: "100" });
  const [showCabinetForm, setShowCabinetForm] = useState(false);
  const [cabinet, setCabinet] = useState<ContourRow>();
  const [cabinetName, setCabinetName] = useState("");
  const [cabinetCompanyIds, setCabinetCompanyIds] = useState("");
  const [cabinetModules, setCabinetModules] = useState("");
  const [cabinetScope, setCabinetScope] = useState("selected");
  const [maxProjects, setMaxProjects] = useState("");
  const [showModuleForm, setShowModuleForm] = useState(false);
  const [module, setModule] = useState<ContourRow>();
  const [moduleName, setModuleName] = useState("");
  const [moduleCompanies, setModuleCompanies] = useState("");
  const [moduleCabinets, setModuleCabinets] = useState("");
  const [showProviderForm, setShowProviderForm] = useState(false);
  const [provider, setProvider] = useState<ContourRow>();
  const [providerId, setProviderId] = useState("");
  const [providerTitle, setProviderTitle] = useState("");
  const [providerSubtitle, setProviderSubtitle] = useState("");
  const [providerPayload, setProviderPayload] = useState("{}");
  const [metric, setMetric] = useState("agent_tokens_used");
  const [metricWindow, setMetricWindow] = useState("7d");
  const [series, setSeries] = useState<ContourRow[]>([]);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [seriesError, setSeriesError] = useState<string>();
  const companies = useRows(async () => normalizeRows(await service.listCompanies()), [service]);
  const cabinets = useRows(async () => normalizeRows(await service.listAdminCabinets()), [service]);
  const modules = useRows(async () => normalizeRows(await service.listAdminModules()), [service]);
  const providers = useRows(async () => normalizeRows(await service.listProviders()), [service]);
  const metrics = useRows(async () => normalizeRows(await service.listCompanyMetrics()), [service]);

  async function perform(action: () => Promise<unknown>, refresh: () => Promise<void>, success: string): Promise<boolean> {
    setBusy(true); setNotice({});
    try { await action(); await refresh(); setNotice({ success }); return true; }
    catch (error) { setNotice({ error: error instanceof Error ? error.message : "Operation failed." }); return false; }
    finally { setBusy(false); }
  }
  function editCompany(row: ContourRow) { setCompany(row); setCompanyName(text(row, "name")); setCompanyEmail(text(row, "contact_email")); setCompanyPhone(text(row, "phone")); setShowCompanyForm(true); }
  function editQuota(row: ContourRow) {
    const q = typeof row.cabinet_quota === "object" && row.cabinet_quota !== null ? row.cabinet_quota as Record<string, unknown> : {};
    setQuotaCompany(row); setQuota({ max_cabinets: String(q.max_cabinets ?? row.cabinets_quota ?? 10), max_packages_per_cabinet: String(q.max_packages_per_cabinet ?? 50), max_bundle_import_mb: String(q.max_bundle_import_mb ?? 100) });
  }
  async function saveCompany(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!company) return;
    if (!await perform(() => service.patchCompany(company.id, { name: companyName.trim(), contact_email: companyEmail.trim() || null, phone: companyPhone.trim() || null }), companies.refresh, "Company updated.")) return;
    setShowCompanyForm(false); setCompany(undefined);
  }
  async function saveQuota(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!quotaCompany) return;
    if (!await perform(() => service.setCompanyCabinetQuota(quotaCompany.id, { max_cabinets: Number(quota.max_cabinets), max_packages_per_cabinet: Number(quota.max_packages_per_cabinet), max_bundle_import_mb: Number(quota.max_bundle_import_mb) }), companies.refresh, "Company cabinet quota saved.")) return;
    setQuotaCompany(undefined);
  }
  function editCabinet(row?: ContourRow) {
    setCabinet(row); setCabinetName(row ? text(row, "name") : ""); setCabinetCompanyIds(row ? (Array.isArray(row.company_ids) ? row.company_ids.join(", ") : text(row, "company_id")) : "");
    setCabinetModules(row && Array.isArray(row.module_ids) ? row.module_ids.join(", ") : ""); setCabinetScope(text(row ?? {}, "company_grant_scope") || "selected"); setMaxProjects(row?.max_projects == null ? "" : String(row.max_projects)); setShowCabinetForm(true);
  }
  async function saveCabinet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const ids = split(cabinetCompanyIds);
    const body: ContourInput = cabinet ? { name: cabinetName.trim(), company_ids: ids, module_ids: split(cabinetModules), company_grant_scope: cabinetScope, ...(maxProjects ? { max_projects: Number(maxProjects), clear_max_projects: false } : { clear_max_projects: true }) } : { name: cabinetName.trim(), company_ids: ids };
    if (!await perform(() => cabinet ? service.patchAdminCabinet(cabinet.id, body) : service.createAdminCabinet(body), cabinets.refresh, cabinet ? "Cabinet updated." : "Cabinet created.")) return;
    setShowCabinetForm(false); setCabinet(undefined);
  }
  function editModule(row?: ContourRow) {
    setModule(row); setModuleName(row ? text(row, "name") : ""); setModuleCompanies(row && Array.isArray(row.company_ids) ? row.company_ids.join(", ") : ""); setModuleCabinets(row && Array.isArray(row.cabinet_ids) ? row.cabinet_ids.join(", ") : ""); setShowModuleForm(true);
  }
  async function saveModule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const body: ContourInput = { name: moduleName.trim(), company_ids: split(moduleCompanies), cabinet_ids: split(moduleCabinets) };
    if (!await perform(() => module ? service.patchAdminModule(module.id, body) : service.createAdminModule({ name: moduleName.trim() }), modules.refresh, module ? "Module and grants updated." : "Module created.")) return;
    setShowModuleForm(false); setModule(undefined);
  }
  function editProvider(row?: ContourRow) {
    setProvider(row); setProviderId(row ? text(row, "id") : ""); setProviderTitle(row ? text(row, "title") : ""); setProviderSubtitle(row ? text(row, "subtitle") : ""); setProviderPayload(row ? JSON.stringify(row.payload ?? {}, null, 2) : "{}"); setShowProviderForm(true);
  }
  async function saveProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); let payload: Record<string, unknown>;
    try { const parsed: unknown = JSON.parse(providerPayload); if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("Provider payload must be a JSON object."); payload = parsed as Record<string, unknown>; }
    catch (error) { setNotice({ error: error instanceof Error ? error.message : "Invalid JSON payload." }); return; }
    const body: ContourInput = { title: providerTitle.trim(), subtitle: providerSubtitle.trim() || null, payload };
    if (!await perform(() => provider ? service.patchProvider(provider.id, body) : service.createProvider({ ...body, id: providerId.trim() }), providers.refresh, provider ? "Provider updated." : "Provider created.")) return;
    setShowProviderForm(false); setProvider(undefined);
  }
  async function loadSeries(row: ContourRow) {
    if (!metric.trim()) { setSeriesError("Enter a metric name before loading its series."); return; }
    setSeriesLoading(true); setSeriesError(undefined); setSeries([]);
    try {
      const result = await service.getMetricSeries({ metric: metric.trim(), entity_type: "company", entity_id: text(row, "company_id") || row.id, window: metricWindow });
      setSeries(normalizeRows([result]));
    } catch (error) { setSeriesError(error instanceof Error ? error.message : "Could not load metric series."); setSeries([]); }
    finally { setSeriesLoading(false); }
  }
  const companyColumns = [{ key: "name", label: "Company" }, { key: "id", label: "ID" }, { key: "contact_email", label: "Contact" }, { key: "active_cabinets", label: "Active cabinets" }, { key: "employees_total", label: "Employees" }, { key: "status", label: "Status" }];
  const cabinetColumns = [{ key: "name", label: "Cabinet" }, { key: "id", label: "ID" }, { key: "owner_scope", label: "Owner scope" }, { key: "company_ids", label: "Company grants" }, { key: "module_ids", label: "Modules" }, { key: "status", label: "Status" }];
  const moduleColumns = [{ key: "name", label: "Module" }, { key: "id", label: "ID" }, { key: "status", label: "Status" }, { key: "company_grants_count", label: "Company grants" }, { key: "cabinet_bindings_count", label: "Cabinet bindings" }];
  const providerColumns = [{ key: "title", label: "Provider" }, { key: "id", label: "Catalog ID" }, { key: "subtitle", label: "Endpoint / note" }, { key: "payload", label: "Configuration" }, { key: "seeded", label: "Seeded" }];
  const metricColumns = [{ key: "name", label: "Company" }, { key: "company_id", label: "Company ID" }, { key: "projects_total", label: "Projects" }, { key: "active_cabinets", label: "Active cabinets" }, { key: "employees_total", label: "Employees" }, { key: "agent_tokens_used", label: "Tokens used" }, { key: "agent_requests", label: "Agent requests" }];

  return <main className="contours admin-contour">
    <header className="contour-heading"><div><p className="contour-eyebrow">Platform administration</p><h1>Admin contour</h1><p className="contour-help">Company directory, cabinet and module catalog, provider endpoints and usage metrics.</p></div></header>
    <nav className="contour-tabs" aria-label="Platform administration">{tabs.map((item) => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} onClick={() => { setTab(item.id); setQuery(""); setNotice({}); }}>{item.label}</button>)}</nav>
    <Notice {...notice} />
    {tab === "companies" && <section className="contour-panel"><h2>Companies</h2><DataTable rows={companies.rows} columns={companyColumns} loading={companies.loading} error={companies.error} onRetry={() => void companies.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => editCompany(row)}>Edit</button><button type="button" disabled={busy} onClick={() => editQuota(row)}>Cabinet quota</button><ConfirmAction label="Delete" disabled={busy} onConfirm={() => void perform(() => service.deleteCompany(row.id), companies.refresh, "Company moved to deleted state.")} /></>} empty="No active companies." />
      {showCompanyForm && company && <FormPanel title={`Edit ${company.name}`} onSubmit={(event) => void saveCompany(event)} submitLabel="Save company" busy={busy} onCancel={() => setShowCompanyForm(false)}><Field label="Name"><TextInput maxLength={200} required value={companyName} onChange={(event) => setCompanyName(event.target.value)} /></Field><Field label="Contact email"><TextInput type="email" value={companyEmail} onChange={(event) => setCompanyEmail(event.target.value)} /></Field><Field label="Phone"><TextInput maxLength={64} value={companyPhone} onChange={(event) => setCompanyPhone(event.target.value)} /></Field></FormPanel>}
      {quotaCompany && <FormPanel title={`Cabinet quotas · ${quotaCompany.name}`} onSubmit={(event) => void saveQuota(event)} submitLabel="Save quota" busy={busy} onCancel={() => setQuotaCompany(undefined)}><Field label="Max cabinets"><TextInput type="number" min={1} max={10000} required value={quota.max_cabinets} onChange={(event) => setQuota({ ...quota, max_cabinets: event.target.value })} /></Field><Field label="Max packages per cabinet"><TextInput type="number" min={0} max={500} required value={quota.max_packages_per_cabinet} onChange={(event) => setQuota({ ...quota, max_packages_per_cabinet: event.target.value })} /></Field><Field label="Max bundle import (MB)"><TextInput type="number" min={1} max={10000} required value={quota.max_bundle_import_mb} onChange={(event) => setQuota({ ...quota, max_bundle_import_mb: event.target.value })} /></Field></FormPanel>}
    </section>}
    {tab === "cabinets" && <section className="contour-panel"><div className="contour-heading"><h2>Cabinet instances</h2><button type="button" onClick={() => editCabinet()}>＋ Create cabinet</button></div>
      {showCabinetForm && <FormPanel title={cabinet ? "Edit cabinet" : "Create cabinet"} onSubmit={(event) => void saveCabinet(event)} submitLabel={cabinet ? "Save cabinet" : "Create cabinet"} busy={busy} onCancel={() => setShowCabinetForm(false)}><Field label="Name"><TextInput maxLength={200} required value={cabinetName} onChange={(event) => setCabinetName(event.target.value)} /></Field><Field label="Company IDs (comma separated)"><TextInput value={cabinetCompanyIds} onChange={(event) => setCabinetCompanyIds(event.target.value)} /></Field>{cabinet && <><Field label="Module IDs (comma separated)"><TextInput value={cabinetModules} onChange={(event) => setCabinetModules(event.target.value)} /></Field><Field label="Company grant scope"><select value={cabinetScope} onChange={(event) => setCabinetScope(event.target.value)}><option value="selected">Selected companies</option><option value="all">All companies</option></select></Field><Field label="Max projects (blank clears limit)"><TextInput type="number" min={1} value={maxProjects} onChange={(event) => setMaxProjects(event.target.value)} /></Field></>}</FormPanel>}
      <DataTable rows={cabinets.rows} columns={cabinetColumns} loading={cabinets.loading} error={cabinets.error} onRetry={() => void cabinets.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => editCabinet(row)}>Edit</button><ConfirmAction label="Delete" disabled={busy} onConfirm={() => void perform(() => service.deleteAdminCabinet(row.id), cabinets.refresh, "Cabinet archived.")} /></>} empty="No cabinets found." />
    </section>}
    {tab === "modules" && <section className="contour-panel"><div className="contour-heading"><h2>Modules</h2><button type="button" onClick={() => editModule()}>＋ Create module</button></div>
      {showModuleForm && <FormPanel title={module ? "Edit module and grants" : "Create module"} onSubmit={(event) => void saveModule(event)} submitLabel={module ? "Save module" : "Create module"} busy={busy} onCancel={() => setShowModuleForm(false)}><Field label="Name"><TextInput maxLength={200} required value={moduleName} onChange={(event) => setModuleName(event.target.value)} /></Field>{module && <><Field label="Company IDs (comma separated)"><TextInput value={moduleCompanies} onChange={(event) => setModuleCompanies(event.target.value)} /></Field><Field label="Cabinet IDs (comma separated)"><TextInput value={moduleCabinets} onChange={(event) => setModuleCabinets(event.target.value)} /></Field></>}</FormPanel>}
      <DataTable rows={modules.rows} columns={moduleColumns} loading={modules.loading} error={modules.error} onRetry={() => void modules.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => editModule(row)}>Edit grants</button><ConfirmAction label="Delete" disabled={busy} onConfirm={() => void perform(() => service.deleteAdminModule(row.id), modules.refresh, "Module deleted.")} /></>} empty="No modules found." />
    </section>}
    {tab === "providers" && <section className="contour-panel"><div className="contour-heading"><h2>AI HTTP provider catalog</h2><button type="button" onClick={() => editProvider()}>＋ Add provider</button></div><p className="contour-help">This edits the confirmed <code>ai.http_providers</code> reference catalog; it is not a separate provider CRUD API.</p>
      {showProviderForm && <FormPanel title={provider ? "Edit provider entry" : "Create provider entry"} onSubmit={(event) => void saveProvider(event)} submitLabel={provider ? "Save provider" : "Create provider"} busy={busy} onCancel={() => setShowProviderForm(false)}><Field label="Catalog ID"><TextInput required maxLength={64} disabled={Boolean(provider)} value={providerId} onChange={(event) => setProviderId(event.target.value)} /></Field><Field label="Title"><TextInput required maxLength={200} value={providerTitle} onChange={(event) => setProviderTitle(event.target.value)} /></Field><Field label="Subtitle"><TextInput maxLength={500} value={providerSubtitle} onChange={(event) => setProviderSubtitle(event.target.value)} /></Field><Field label="Configuration JSON"><textarea required value={providerPayload} onChange={(event) => setProviderPayload(event.target.value)} /></Field></FormPanel>}
      <DataTable rows={providers.rows} columns={providerColumns} loading={providers.loading} error={providers.error} onRetry={() => void providers.refresh()} query={query} onQuery={setQuery} actions={(row) => <><button type="button" disabled={busy} onClick={() => editProvider(row)}>Edit</button><ConfirmAction label="Archive" disabled={busy} onConfirm={() => void perform(() => service.deleteProvider(row.id), providers.refresh, "Provider catalog entry archived.")} /></>} empty="No HTTP providers are in the catalog." />
    </section>}
    {tab === "metrics" && <section className="contour-panel"><div className="contour-heading"><h2>Company metrics</h2><ConfirmAction label="Rebuild counters" confirmLabel="Rebuild now" danger={false} disabled={busy} onConfirm={() => void perform(service.rebuildMetrics, metrics.refresh, "Metric counters rebuilt.")} /></div><p className="contour-help">Series are read from <code>/admin/metrics/series</code>. Counter rebuilding updates derived metrics.</p>
      <div className="contour-inline"><Field label="Metric name"><TextInput required value={metric} onChange={(event) => setMetric(event.target.value)} placeholder="Metric key from the API" /></Field><Field label="Window"><select value={metricWindow} onChange={(event) => setMetricWindow(event.target.value)}><option value="1h">1 hour</option><option value="24h">24 hours</option><option value="7d">7 days</option><option value="30d">30 days</option></select></Field></div>
      <DataTable rows={metrics.rows} columns={metricColumns} loading={metrics.loading} error={metrics.error} onRetry={() => void metrics.refresh()} query={query} onQuery={setQuery} actions={(row) => <button type="button" disabled={seriesLoading} onClick={() => void loadSeries(row)}>Load series</button>} empty="No company metrics are available." />
      {seriesError && <Notice error={seriesError} />}{seriesLoading && <p role="status" className="contour-state">Loading metric series…</p>}{series.length > 0 && <section className="contour-panel"><h3>Metric series response</h3><pre>{JSON.stringify(series[0], null, 2)}</pre></section>}
    </section>}
    {tab === "recycle" && <RecycleBin service={service} />}
  </main>;
}

function RecycleBin({ service }: { service: ContourService }) {
  const recycle = useRows(async () => normalizeRows(await service.listRecycleBin()), [service]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ error?: string; success?: string }>({});
  const [query, setQuery] = useState("");
  return <section className="contour-panel"><h2>Deleted companies, employees, cabinets and projects</h2><Notice {...notice} />
    <DataTable rows={recycle.rows} columns={[{ key: "entity", label: "Type" }, { key: "name", label: "Name" }, { key: "email", label: "Login / email" }, { key: "company_id", label: "Company" }, { key: "status", label: "Status" }, { key: "deleted_at", label: "Deleted at" }]} loading={recycle.loading} error={recycle.error} onRetry={() => void recycle.refresh()} query={query} onQuery={setQuery} empty="Recycle bin is empty." actions={(row) => <><button type="button" disabled={busy} onClick={() => { setBusy(true); void service.restoreRecycled(text(row, "entity"), row.id).then(async () => { await recycle.refresh(); setNotice({ success: "Entity restored." }); }).catch((error: unknown) => setNotice({ error: error instanceof Error ? error.message : "Restore failed." })).finally(() => setBusy(false)); }}>Restore</button><ConfirmAction label="Purge permanently" disabled={busy} onConfirm={() => { setBusy(true); void service.purgeRecycled(text(row, "entity"), row.id).then(async () => { await recycle.refresh(); setNotice({ success: "Entity permanently purged." }); }).catch((error: unknown) => setNotice({ error: error instanceof Error ? error.message : "Purge failed." })).finally(() => setBusy(false)); }} /></>} />
  </section>;
}
