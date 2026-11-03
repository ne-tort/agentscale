import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import type { ModuleAdminMode, ModuleAdminRow, ModuleAdminService } from "./service";
import { displayValue, moduleEvidence, recordId, templateRows } from "./view-model";
import "./module-admin.css";

export interface ModuleAdminPageProps {
  mode: ModuleAdminMode;
  service: ModuleAdminService;
  companyId?: string;
}

export function ModuleEvidencePanel({ row, mode }: { row: ModuleAdminRow; mode: ModuleAdminMode }) {
  const evidence = moduleEvidence(row, mode);
  return <section className="module-admin__evidence" aria-label="Effective module evidence">
    <dl>
      <div><dt>Effective source</dt><dd>{evidence.source ?? "API contract gap"}</dd></div>
      <div><dt>Version</dt><dd>{evidence.version ?? "API contract gap"}</dd></div>
      <div><dt>Rights</dt><dd>{evidence.rights ?? "API contract gap"}</dd></div>
      <div><dt>Bind kind</dt><dd>{evidence.bindKind ?? "Not provided by catalog API"}</dd></div>
      <div><dt>Child may edit</dt><dd>{evidence.childMayEdit === undefined ? "Not provided by catalog API" : evidence.childMayEdit ? "Yes (API)" : "No (API)"}</dd></div>
    </dl>
    {evidence.gaps.map((gap) => <p className="module-admin__gap" key={gap}>{gap}</p>)}
  </section>;
}

type Notice = { error?: string; success?: string };
type BindingReadback = { module: ModuleAdminRow; cabinets: ModuleAdminRow[] };
type PendingAction =
  | { kind: "grants"; nextIds: string[]; added: number; removed: number }
  | { kind: "cabinets"; nextIds: string[]; added: number; removed: number };

function stringValue(row: ModuleAdminRow, key: string): string {
  const value = row[key];
  return typeof value === "string" ? value : "";
}

function ids(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function rowName(row: ModuleAdminRow, primary: string, id: string): string {
  return stringValue(row, primary) || stringValue(row, id) || recordId(row);
}

function moduleSearchText(row: ModuleAdminRow): string {
  return [row.name, row.id, row.status, row.source, row.bind_kind]
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .toLocaleLowerCase();
}

function failureMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed.";
}

function isForbidden(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && error.status === 403;
}

export function ModuleAdminPage({ mode, service, companyId }: ModuleAdminPageProps) {
  const [modules, setModules] = useState<ModuleAdminRow[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [module, setModule] = useState<ModuleAdminRow>();
  const [metaDocuments, setMetaDocuments] = useState<ModuleAdminRow[]>([]);
  const [cabinetBindings, setCabinetBindings] = useState<ModuleAdminRow[]>([]);
  const [projectBindings, setProjectBindings] = useState<ModuleAdminRow[]>([]);
  const [companies, setCompanies] = useState<ModuleAdminRow[]>([]);
  const [installations, setInstallations] = useState<ModuleAdminRow[]>([]);
  const [templates, setTemplates] = useState<ModuleAdminRow[]>([]);
  const [selectedCabinetId, setSelectedCabinetId] = useState("");
  const [grantDraft, setGrantDraft] = useState<string[]>([]);
  const [cabinetDraft, setCabinetDraft] = useState<string[]>([]);
  const [pending, setPending] = useState<PendingAction>();
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [notice, setNotice] = useState<Notice>({});
  const [newModuleName, setNewModuleName] = useState("");
  const [search, setSearch] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);

  const boundCabinetIds = useMemo(() => cabinetBindings.filter((row) => row.bound === true).map((row) => recordId(row)).filter(Boolean), [cabinetBindings]);
  const visibleModules = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return query ? modules.filter((item) => moduleSearchText(item).includes(query)) : modules;
  }, [modules, search]);
  const tablesDocument = metaDocuments.find((document) => document.slug === "tables");
  const templateTableAvailable = Array.isArray(tablesDocument?.body)
    && tablesDocument.body.some((table) => typeof table === "object" && table !== null && (table as Record<string, unknown>).slug === "templates");
  const selectedCabinet = cabinetBindings.find((row) => recordId(row) === selectedCabinetId);
  const installed = installations.find((row) => recordId(row) === selectedId || row.module_id === selectedId);
  const allCompanyScope = module?.company_grant_scope === "all";


  useEffect(() => {
    let active = true;
    setLoading(true);
    setPermissionDenied(false);
    setNotice({});
    const load = mode === "admin"
      ? service.listAdminModules()
      : companyId
        ? service.listCompanyModules(companyId)
        : Promise.reject(new Error("Company ID is required to load the employee module catalog."));
    void Promise.all([load, mode === "admin" ? service.listCompanies() : Promise.resolve([])])
      .then(([items, companyRows]) => {
        if (!active) return;
        setModules(items);
        setCompanies(companyRows);
        setSelectedId((current) => current && items.some((item) => recordId(item) === current) ? current : recordId(items[0] ?? {}));
      })
      .catch((error: unknown) => {
        if (active) {
          setPermissionDenied(isForbidden(error));
          setNotice({ error: failureMessage(error) });
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [companyId, mode, retryVersion, service]);

  useEffect(() => {
    if (!selectedId) {
      setModule(undefined);
      setMetaDocuments([]);
      setCabinetBindings([]);
      setProjectBindings([]);
      setInstallations([]);
      setTemplates([]);
      setSelectedCabinetId("");
      return;
    }
    let active = true;
    setPermissionDenied(false);
    setModule(undefined);
    setMetaDocuments([]);
    setCabinetBindings([]);
    setProjectBindings([]);
    setInstallations([]);
    setTemplates([]);
    setLoading(true);
    setNotice({});
    const moduleRequest = mode === "admin"
      ? service.getAdminModule(selectedId)
      : companyId
        ? service.getCompanyModule(companyId, selectedId)
        : Promise.reject(new Error("Company ID is required to read module details."));
    void Promise.all([
      moduleRequest,
      service.listMetaDocuments(mode, selectedId, companyId),
      service.listCabinetBindings(mode, selectedId, companyId),
      service.listProjectBindings(mode, selectedId, companyId),
    ]).then(([detail, meta, cabinets, projectRows]) => {
      if (!active) return;
      setModule(detail);
      setMetaDocuments(meta);
      setCabinetBindings(cabinets);
      setProjectBindings(projectRows);
      setGrantDraft(ids(detail.company_ids));
      setCabinetDraft(cabinets.filter((row) => row.bound === true).map((row) => recordId(row)).filter(Boolean));
      const firstBoundCabinet = cabinets.find((row) => row.bound === true);
      setSelectedCabinetId(recordId(firstBoundCabinet ?? {}));
    }).catch((error: unknown) => {
      if (active) {
        setPermissionDenied(isForbidden(error));
        setNotice({ error: failureMessage(error) });
      }
    })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [companyId, mode, retryVersion, selectedId, service]);

  useEffect(() => {
    if (!selectedCabinetId || !boundCabinetIds.includes(selectedCabinetId)) {
      setInstallations([]);
      setTemplates([]);
      return;
    }
    let active = true;
    void Promise.all([
      service.listCabinetInstallations(selectedCabinetId),
      templateTableAvailable ? service.listCabinetModuleData(selectedCabinetId, selectedId, "templates") : Promise.resolve([]),
    ]).then(([installRows, templateItems]) => {
      if (!active) return;
      setInstallations(installRows);
      setTemplates(templateRows(templateItems));
    }).catch((error: unknown) => {
      if (active) {
        setPermissionDenied(isForbidden(error));
        setNotice({ error: failureMessage(error) });
      }
    });
    return () => { active = false; };
  }, [boundCabinetIds, retryVersion, selectedCabinetId, selectedId, service, templateTableAvailable]);

  async function refreshDetail(): Promise<BindingReadback | undefined> {
    if (!selectedId) return undefined;
    const moduleRequest = mode === "admin"
      ? service.getAdminModule(selectedId)
      : service.getCompanyModule(companyId as string, selectedId);
    const [detail, cabinets, projectRows, catalog] = await Promise.all([
      moduleRequest,
      service.listCabinetBindings(mode, selectedId, companyId),
      service.listProjectBindings(mode, selectedId, companyId),
      mode === "admin" ? service.listAdminModules() : service.listCompanyModules(companyId as string),
    ]);
    setModule(detail);
    setCabinetBindings(cabinets);
    setProjectBindings(projectRows);
    setModules(catalog);
    setGrantDraft(ids(detail.company_ids));
    setCabinetDraft(cabinets.filter((row) => row.bound === true).map((row) => recordId(row)).filter(Boolean));
    if (!cabinets.some((row) => row.bound === true && recordId(row) === selectedCabinetId)) {
      setSelectedCabinetId(recordId(cabinets.find((row) => row.bound === true) ?? {}));
    }
    return { module: detail, cabinets };
  }

  async function perform(
    action: () => Promise<unknown>,
    success: string,
    reload = true,
    verifyReadback?: (readback: BindingReadback | undefined) => boolean,
  ) {
    setBusy(true);
    setNotice({});
    try {
      await action();
      const readback = reload ? await refreshDetail() : undefined;
      if (verifyReadback && !verifyReadback(readback)) {
        throw new Error("The API accepted the change, but its readback does not match the requested bindings.");
      }
      setNotice({ success });
      setPending(undefined);
    } catch (error) {
      setPermissionDenied(isForbidden(error));
      setNotice({ error: failureMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  function toggleDraft(list: string[], setList: (value: string[]) => void, id: string) {
    setList(list.includes(id) ? list.filter((item) => item !== id) : [...list, id]);
  }

  function reviewListChange(kind: "grants" | "cabinets", nextIds: string[], currentIds: string[]) {
    setPending({ kind, nextIds, added: nextIds.filter((id) => !currentIds.includes(id)).length, removed: currentIds.filter((id) => !nextIds.includes(id)).length });
  }

  function confirmPending() {
    if (!pending || !module) return;
    const action = pending;
    if (action.kind === "grants") {
      void perform(
        () => service.patchAdminModule(module.id as string, { company_ids: action.nextIds }),
        "Company grants updated and verified.",
        true,
        (readback) => Boolean(readback && sameIds(ids(readback.module.company_ids), action.nextIds)),
      );
    } else if (action.kind === "cabinets") {
      const save = mode === "admin"
        ? service.patchAdminModule(module.id as string, { cabinet_ids: action.nextIds })
        : service.patchCompanyModule(companyId as string, module.id as string, { cabinet_ids: action.nextIds });
      void perform(
        () => save,
        "Cabinet bindings updated and verified.",
        true,
        (readback) => Boolean(readback && sameIds(
          readback.cabinets.filter((row) => row.bound === true).map((row) => recordId(row)).filter(Boolean),
          action.nextIds,
        )),
      );
    }
  }

  async function createModule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newModuleName.trim();
    if (!name) return;
    await perform(async () => {
      const created = mode === "admin"
        ? await service.createAdminModule(name)
        : await service.createCompanyModule(companyId as string, name);
      const newId = recordId(created);
      const next = mode === "admin"
        ? await service.listAdminModules()
        : await service.listCompanyModules(companyId as string);
      setModules(next);
      if (newId) setSelectedId(newId);
      setNewModuleName("");
    }, "Module created.", false);
  }

  const title = mode === "admin" ? "Module administration" : "Employee module catalog";
  return <main className="module-admin" aria-busy={loading}>
    <header className="module-admin__header">
      <div><p className="module-admin__eyebrow">{mode === "admin" ? "Platform" : "Company"} / Modules</p><h1>{title}</h1></div>
      <form className="module-admin__create" onSubmit={(event) => void createModule(event)}><label htmlFor="module-new-name">{mode === "admin" ? "New platform module" : "New company module"}</label><input id="module-new-name" maxLength={200} placeholder="Module name" value={newModuleName} onChange={(event) => setNewModuleName(event.target.value)} disabled={permissionDenied} /><button type="submit" disabled={busy || permissionDenied || !newModuleName.trim() || (mode === "company" && !companyId)}>Create</button></form>
    </header>
    {notice.error && <p className="module-admin__notice module-admin__notice--error" role="alert">{notice.error}</p>}
    {notice.success && <p className="module-admin__notice" role="status">{notice.success}</p>}
    {notice.error && <button type="button" disabled={loading || busy} onClick={() => { setPermissionDenied(false); setRetryVersion((value) => value + 1); }}>Retry</button>}
    <div className="module-admin__layout">
      <aside className="module-admin__catalog" aria-label="Module catalog">
        <div className="module-admin__section-heading"><h2>Catalog</h2><span>{modules.length}</span></div>
        <label className="module-admin__search" htmlFor="module-search">Search modules<input id="module-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        {loading && modules.length === 0 && <p role="status">Loading catalog…</p>}
        {loading && modules.length > 0 && !module && <p role="status">Loading module details…</p>}
        {!loading && modules.length === 0 && !notice.error && <p>{mode === "company" ? "No modules are currently available to this company." : "No modules returned by API."}</p>}
        {!loading && modules.length > 0 && visibleModules.length === 0 && <p>No modules match this search.</p>}
        {mode === "company" && <p className="module-admin__muted">This catalog lists modules already visible to the company (selected grant, all-company scope, or company-local modules); the API has no company-accessible global catalog for ungranted modules. Platform administrators manage selected grants.</p>}
        {visibleModules.map((item) => {
          const id = recordId(item);
          return <button className="module-admin__module-option" key={id} type="button" aria-current={selectedId === id ? "true" : undefined} onClick={() => { setSelectedId(id); setPending(undefined); setNotice({}); }}>
            <strong>{rowName(item, "name", "id")}</strong><span>{displayValue(item.status)}</span>
            <small>{mode === "admin"
              ? `${displayValue(item.company_grants_count)} company grants · ${displayValue(item.cabinet_bindings_count)} cabinet binds`
              : `${displayValue(item.source)} · ${displayValue(item.cabinet_bindings_count)} cabinet binds`}</small>
          </button>;
        })}
      </aside>
      {module && <section className="module-admin__details" aria-label="Selected module details">
        <header className="module-admin__section-heading"><div><h2>{rowName(module, "name", "id")}</h2><code>{recordId(module)}</code></div><span className="module-admin__status">{displayValue(module.status)}</span></header>
        <ModuleEvidencePanel row={module} mode={mode} />
        <section className="module-admin__section" aria-label="Binding hierarchy">
          <h3>Binding hierarchy</h3>
          <p>Platform module → company grant → company cabinet binding → project binding.</p>
          <p className="module-admin__gap">Enable/disable and version actions are unavailable: no matching module routes or version field are exposed by these APIs.</p>
          {mode === "company" && <p className="module-admin__muted">Company owners can manage cabinet bindings; platform grants are managed by platform administrators.</p>}
        </section>

        {mode === "admin" && <section className="module-admin__section"><div className="module-admin__section-heading"><h3>Company grants</h3><span>{grantDraft.length} selected · API scope {displayValue(module.company_grant_scope)}</span></div>
          {allCompanyScope && <p>API scope grants this module to all companies. Individual grant edits are not applicable.</p>}
          {companies.length === 0 ? <p>No companies returned by API.</p> : <div className="module-admin__checks">{companies.map((company) => {
            const id = recordId(company);
            return <label key={id}><input type="checkbox" checked={allCompanyScope || grantDraft.includes(id)} disabled={busy || permissionDenied || allCompanyScope} onChange={() => toggleDraft(grantDraft, setGrantDraft, id)} /><span>{rowName(company, "name", "id")}</span><code>{id}</code></label>;
          })}</div>}
          {!allCompanyScope && <button type="button" disabled={busy || permissionDenied || sameIds(grantDraft, ids(module.company_ids))} onClick={() => reviewListChange("grants", grantDraft, ids(module.company_ids))}>Review grant changes</button>}
        </section>}

        <section className="module-admin__section"><div className="module-admin__section-heading"><h3>Cabinet bindings and installations</h3><span>{cabinetDraft.length} bound</span></div>
          {cabinetBindings.length === 0 ? <p>No cabinet bindings returned by API.</p> : <div className="module-admin__checks">{cabinetBindings.map((cabinet) => {
            const id = recordId(cabinet);
            const bound = cabinetDraft.includes(id);
            const moduleCabinet = Array.isArray(module.cabinets)
              ? (module.cabinets as ModuleAdminRow[]).find((row) => recordId(row) === id)
              : undefined;
            const installedCabinet = id === selectedCabinetId ? installed : undefined;
            const bindingDetail = moduleCabinet ?? installedCabinet;
            const effectiveKind = bindingDetail?.bind_kind === "local" || bindingDetail?.bind_kind === "global" ? bindingDetail.bind_kind : "API contract gap";
            const effectiveEdit = typeof bindingDetail?.child_may_edit === "boolean" ? bindingDetail.child_may_edit ? "yes (API)" : "no (API)" : "API contract gap";
            return <label key={id}><input type="checkbox" checked={bound} disabled={busy || permissionDenied} onChange={() => toggleDraft(cabinetDraft, setCabinetDraft, id)} /><span>{rowName(cabinet, "cabinet_name", "cabinet_id")}</span><small>{bound ? "Bound" : "Not bound"} · {displayValue(cabinet.company_name)} · bind {effectiveKind} · child edit {effectiveEdit}</small><code>{id}</code></label>;
          })}</div>}
          <button type="button" disabled={busy || permissionDenied || sameIds(cabinetDraft, boundCabinetIds)} onClick={() => reviewListChange("cabinets", cabinetDraft, boundCabinetIds)}>Review cabinet changes</button>
          <div className="module-admin__inline-fields">
            <label>Bound cabinet<select value={selectedCabinetId} onChange={(event) => setSelectedCabinetId(event.target.value)}><option value="">Choose a bound cabinet</option>{cabinetBindings.filter((row) => boundCabinetIds.includes(recordId(row))).map((row) => <option key={recordId(row)} value={recordId(row)}>{rowName(row, "cabinet_name", "cabinet_id")}</option>)}</select></label>
            {selectedCabinetId && <span>Installation: {installed ? `Installed / bound (API); module status ${displayValue(installed.status)}; bind ${displayValue(installed.bind_kind)}; child may edit ${typeof installed.child_may_edit === "boolean" ? installed.child_may_edit ? "yes (API)" : "no (API)" : "API contract gap"}` : "API contract gap: module is absent from the cabinet installations response."}</span>}
          </div>
          {selectedCabinetId && <div className="module-admin__templates"><h4>Available templates</h4>{!templateTableAvailable && <p className="module-admin__gap">API contract gap: module meta does not declare a templates table.</p>}{templateTableAvailable && templates.length === 0 && <p>No active template rows returned by API.</p>}{templates.map((row, index) => {
            const body = row.body as Record<string, unknown>;
            const file = typeof body.file === "object" && body.file !== null ? body.file as Record<string, unknown> : {};
            return <div key={recordId(row, `template-${index}`)}><strong>{displayValue(body.name ?? body.label ?? body.template_type)}</strong><span>{displayValue(body.template_type)}</span><small>{displayValue(file.filename ?? file.name ?? file.storage_key)}</small></div>;
          })}</div>}
        </section>

        <section className="module-admin__section"><div className="module-admin__section-heading"><h3>Project bindings</h3><span>{projectBindings.length} bound</span></div>
          {projectBindings.length === 0 && <p>No project bindings returned by API.</p>}
          {projectBindings.length > 0 && <div className="module-admin__table-scroll"><table><thead><tr><th>Project</th><th>Company</th><th>Cabinet</th><th>Bind kind</th><th>Child rights</th><th>Usage</th><th>Management</th></tr></thead><tbody>{projectBindings.map((project) => {
            const id = recordId(project);
            return <tr key={id}><td><Link to={`/projects/${encodeURIComponent(id)}/settings`}>{rowName(project, "project_name", "project_id")}</Link><code>{id}</code></td><td>{displayValue(project.company_name ?? project.company_id)}</td><td>{displayValue(project.cabinet_id)}</td><td>{displayValue(project.bind_kind)}</td><td>{typeof project.child_may_edit === "boolean" ? project.child_may_edit ? "May edit (API)" : "Read only (API)" : "API contract gap"}</td><td>{displayValue(project.agent_tokens_used)} tokens / {displayValue(project.agent_requests)} requests</td><td><Link to={`/projects/${encodeURIComponent(id)}/settings`}>Manage project</Link></td></tr>;
          })}</tbody></table></div>}
          <p className="module-admin__muted">Create or remove project bindings from project management.</p>
        </section>

        <section className="module-admin__section"><h3>Module metadata</h3>{metaDocuments.length === 0 ? <p>No meta documents returned by API.</p> : <div className="module-admin__meta-list">{metaDocuments.map((document) => <details key={stringValue(document, "slug")}><summary>{stringValue(document, "slug") || "Unnamed document"}<small>Updated {displayValue(document.updated_at)}</small></summary><pre>{JSON.stringify(document.body, null, 2)}</pre></details>)}</div>}</section>
        {selectedCabinet && <p className="module-admin__muted">Binding cabinet: {rowName(selectedCabinet, "cabinet_name", "cabinet_id")}.</p>}
      </section>}
    </div>
    {pending && <div className="module-admin__confirm" role="alertdialog" aria-modal="true" aria-labelledby="module-admin-confirm-title">
      <h2 id="module-admin-confirm-title">Confirm module binding change</h2>
      {pending.kind === "grants" && <><p>Apply company grants: bind {pending.added}, revoke {pending.removed}.</p>{pending.removed > 0 && <p>Revoking a grant also removes this company's cabinet bindings and dependent project bindings.</p>}</>}
      {pending.kind === "cabinets" && <><p>Apply cabinet installations: bind {pending.added}, revoke {pending.removed}.</p>{pending.removed > 0 && <p>Removing a cabinet binding uninstalls the module and revokes its project bindings in that cabinet.</p>}</>}

      <div><button type="button" disabled={busy} onClick={confirmPending}>Confirm</button><button type="button" disabled={busy} onClick={() => setPending(undefined)}>Cancel</button></div>
    </div>}
  </main>;
}

function sameIds(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((id) => right.includes(id));
}