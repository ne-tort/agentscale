import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import type { Translate } from "../../../i18n/catalogue";
import { useI18n } from "../../../i18n/useT";
import { enumLabel } from "../ui";
import type { ModuleAdminMode, ModuleAdminRow, ModuleAdminService } from "./service";
import { displayValue, moduleEvidence, recordId, templateRows, type ModuleGap, type ModuleRights } from "./view-model";
import "./module-admin.css";

export interface ModuleAdminPageProps {
  mode: ModuleAdminMode;
  service: ModuleAdminService;
  companyId?: string;
}

/** Rights as the reader sees them: no "(API)" tail, no field names. */
const RIGHTS_KEYS: Record<ModuleRights, string> = {
  writable: "contours.rightsWritable",
  readOnly: "contours.rightsReadOnly",
  childMayEdit: "contours.rightsChildMayEdit",
  childReadOnly: "contours.rightsChildReadOnly",
};

/** A missing value is named, not explained as a contract problem. */
const GAP_KEYS: Record<ModuleGap, string> = {
  source: "contours.gapSource",
  version: "contours.gapVersion",
  rights: "contours.gapRights",
};

/** `platform_assigned` / `company_local` in words; anything else as it arrived. */
function sourceLabel(value: string, t: Translate): string {
  if (value === "platform_assigned") return t("contours.sourcePlatform");
  if (value === "company_local") return t("contours.sourceCompany");
  return value;
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function ModuleEvidencePanel({ row, mode }: { row: ModuleAdminRow; mode: ModuleAdminMode }) {
  const { t } = useI18n();
  const evidence = moduleEvidence(row, mode);
  return <section className="module-admin__evidence" aria-label={t("contours.detailsAria")}>
    <dl>
      <div><dt>{t("contours.evidenceSource")}</dt><dd>{evidence.source ? sourceLabel(evidence.source, t) : t("contours.notSpecifiedM")}</dd></div>
      <div><dt>{t("contours.evidenceVersion")}</dt><dd>{evidence.version ?? t("contours.notSpecifiedF")}</dd></div>
      <div><dt>{t("contours.evidenceRights")}</dt><dd>{evidence.rights ? t(RIGHTS_KEYS[evidence.rights]) : t("contours.notSpecifiedPlural")}</dd></div>
      <div><dt>{t("contours.evidenceBindKind")}</dt><dd>{evidence.bindKind ? enumLabel(evidence.bindKind, t) : t("contours.notSpecifiedM")}</dd></div>
      <div><dt>{t("contours.evidenceChildMayEdit")}</dt><dd>{evidence.childMayEdit === undefined ? t("contours.notSpecifiedN") : evidence.childMayEdit ? t("common.yes") : t("common.no")}</dd></div>
    </dl>
    {evidence.gaps.map((gap) => <p className="module-admin__gap" key={gap}>{t(GAP_KEYS[gap])}</p>)}
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

function failureMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && error.status === 403;
}

export function ModuleAdminPage({ mode, service, companyId }: ModuleAdminPageProps) {
  const { t, plural } = useI18n();
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
        : Promise.reject(new Error(t("contours.companyContextMissing")));
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
          setNotice({ error: failureMessage(error, t("common.requestFailed")) });
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [companyId, mode, retryVersion, service]); // eslint-disable-line react-hooks/exhaustive-deps

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
        : Promise.reject(new Error(t("contours.companyContextMissing")));
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
        setNotice({ error: failureMessage(error, t("common.requestFailed")) });
      }
    })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [companyId, mode, retryVersion, selectedId, service]); // eslint-disable-line react-hooks/exhaustive-deps

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
        setNotice({ error: failureMessage(error, t("common.requestFailed")) });
      }
    });
    return () => { active = false; };
  }, [boundCabinetIds, retryVersion, selectedCabinetId, selectedId, service, templateTableAvailable]); // eslint-disable-line react-hooks/exhaustive-deps

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
        throw new Error(t("contours.readbackMismatch"));
      }
      setNotice({ success });
      setPending(undefined);
    } catch (error) {
      setPermissionDenied(isForbidden(error));
      setNotice({ error: failureMessage(error, t("common.requestFailed")) });
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
        t("contours.noticeGrantsUpdated"),
        true,
        (readback) => Boolean(readback && sameIds(ids(readback.module.company_ids), action.nextIds)),
      );
    } else if (action.kind === "cabinets") {
      const save = mode === "admin"
        ? service.patchAdminModule(module.id as string, { cabinet_ids: action.nextIds })
        : service.patchCompanyModule(companyId as string, module.id as string, { cabinet_ids: action.nextIds });
      void perform(
        () => save,
        t("contours.noticeCabinetsUpdated"),
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
    }, t("contours.noticeModuleCreated"), false);
  }

  const title = mode === "admin" ? t("contours.moduleAdminTitle") : t("contours.moduleCompanyTitle");
  return <main className="module-admin" aria-busy={loading}>
    <header className="module-admin__header">
      <div><p className="module-admin__eyebrow">{mode === "admin" ? t("contours.eyebrowPlatform") : t("contours.eyebrowCompany")} / {t("contours.eyebrowModules")}</p><h1>{title}</h1></div>
      <form className="module-admin__create" onSubmit={(event) => void createModule(event)}><label htmlFor="module-new-name">{mode === "admin" ? t("contours.newPlatformModule") : t("contours.newCompanyModule")}</label><input id="module-new-name" maxLength={200} placeholder={t("contours.moduleNamePlaceholder")} value={newModuleName} onChange={(event) => setNewModuleName(event.target.value)} disabled={permissionDenied} /><button type="submit" disabled={busy || permissionDenied || !newModuleName.trim() || (mode === "company" && !companyId)}>{t("common.create")}</button></form>
    </header>
    {notice.error && <p className="module-admin__notice module-admin__notice--error" role="alert">{notice.error}</p>}
    {notice.success && <p className="module-admin__notice" role="status">{notice.success}</p>}
    {notice.error && <button type="button" disabled={loading || busy} onClick={() => { setPermissionDenied(false); setRetryVersion((value) => value + 1); }}>{t("common.retry")}</button>}
    <div className="module-admin__layout">
      <aside className="module-admin__catalog" aria-label={t("contours.catalogTitle")}>
        <div className="module-admin__section-heading"><h2>{t("contours.catalogTitle")}</h2><span>{modules.length}</span></div>
        <label className="module-admin__search" htmlFor="module-search">{t("contours.searchModules")}<input id="module-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        {loading && modules.length === 0 && <p role="status">{t("contours.loadingCatalog")}</p>}
        {loading && modules.length > 0 && !module && <p role="status">{t("contours.loadingDetails")}</p>}
        {!loading && modules.length === 0 && !notice.error && <p>{mode === "company" ? t("contours.companyNoModules") : t("contours.noModules")}</p>}
        {!loading && modules.length > 0 && visibleModules.length === 0 && <p>{t("contours.noModuleMatches")}</p>}
        {mode === "company" && <p className="module-admin__muted">{t("contours.companyCatalogHint")}</p>}
        {visibleModules.map((item) => {
          const id = recordId(item);
          return <button className="module-admin__module-option" key={id} type="button" aria-current={selectedId === id ? "true" : undefined} onClick={() => { setSelectedId(id); setPending(undefined); setNotice({}); }}>
            <strong>{rowName(item, "name", "id")}</strong><span>{enumLabel(item.status, t)}</span>
            <small>{mode === "admin"
              ? `${plural("contours.catalogCompanies", count(item.company_grants_count))} · ${plural("contours.catalogCabinets", count(item.cabinet_bindings_count))}`
              : `${sourceLabel(displayValue(item.source), t)} · ${plural("contours.catalogCabinets", count(item.cabinet_bindings_count))}`}</small>
          </button>;
        })}
      </aside>
      {module && <section className="module-admin__details" aria-label={t("contours.detailsAria")}>
        <header className="module-admin__section-heading" title={recordId(module)}><div><h2>{rowName(module, "name", "id")}</h2></div><span className="module-admin__status">{enumLabel(module.status, t)}</span></header>
        <ModuleEvidencePanel row={module} mode={mode} />
        <section className="module-admin__section" aria-label={t("contours.bindingHierarchy")}>
          <h3>{t("contours.bindingHierarchy")}</h3>
          <p>{t("contours.bindingHierarchyHint")}</p>
          <p className="module-admin__gap">{t("contours.actionsUnavailable")}</p>
          {mode === "company" && <p className="module-admin__muted">{t("contours.companyOwnersHint")}</p>}
        </section>

        {mode === "admin" && <section className="module-admin__section"><div className="module-admin__section-heading"><h3>{t("contours.companyGrantsTitle")}</h3><span>{t("contours.grantsSelected", { count: grantDraft.length })} · {t("contours.grantsScope", { scope: enumLabel(module.company_grant_scope, t) })}</span></div>
          {allCompanyScope && <p>{t("contours.allScopeNotice")}</p>}
          {companies.length === 0 ? <p>{t("contours.companiesEmpty")}</p> : <div className="module-admin__checks">{companies.map((company) => {
            const id = recordId(company);
            return <label key={id} title={id}><input type="checkbox" checked={allCompanyScope || grantDraft.includes(id)} disabled={busy || permissionDenied || allCompanyScope} onChange={() => toggleDraft(grantDraft, setGrantDraft, id)} /><span>{rowName(company, "name", "id")}</span></label>;
          })}</div>}
          {!allCompanyScope && <button type="button" disabled={busy || permissionDenied || sameIds(grantDraft, ids(module.company_ids))} onClick={() => reviewListChange("grants", grantDraft, ids(module.company_ids))}>{t("contours.reviewGrantChanges")}</button>}
        </section>}

        <section className="module-admin__section"><div className="module-admin__section-heading"><h3>{t("contours.cabinetBindingsTitle")}</h3><span>{t("contours.boundCount", { count: cabinetDraft.length })}</span></div>
          {cabinetBindings.length === 0 ? <p>{t("contours.cabinetBindingsEmpty")}</p> : <div className="module-admin__checks">{cabinetBindings.map((cabinet) => {
            const id = recordId(cabinet);
            const bound = cabinetDraft.includes(id);
            const moduleCabinet = Array.isArray(module.cabinets)
              ? (module.cabinets as ModuleAdminRow[]).find((row) => recordId(row) === id)
              : undefined;
            const installedCabinet = id === selectedCabinetId ? installed : undefined;
            const bindingDetail = moduleCabinet ?? installedCabinet;
            const effectiveKind = bindingDetail?.bind_kind === "local" || bindingDetail?.bind_kind === "global" ? enumLabel(bindingDetail.bind_kind, t) : t("contours.notSpecifiedM");
            const effectiveEdit = typeof bindingDetail?.child_may_edit === "boolean" ? bindingDetail.child_may_edit ? t("common.yes") : t("common.no") : t("contours.notSpecifiedN");
            return <label key={id} title={id}><input type="checkbox" checked={bound} disabled={busy || permissionDenied} onChange={() => toggleDraft(cabinetDraft, setCabinetDraft, id)} /><span>{rowName(cabinet, "cabinet_name", "cabinet_id")}</span><small>{t("contours.bindingDetails", { state: bound ? t("contours.bound") : t("contours.notBound"), company: displayValue(cabinet.company_name), kind: effectiveKind, edit: effectiveEdit })}</small></label>;
          })}</div>}
          <button type="button" disabled={busy || permissionDenied || sameIds(cabinetDraft, boundCabinetIds)} onClick={() => reviewListChange("cabinets", cabinetDraft, boundCabinetIds)}>{t("contours.reviewCabinetChanges")}</button>
          <div className="module-admin__inline-fields">
            <label>{t("contours.boundCabinet")}<select value={selectedCabinetId} onChange={(event) => setSelectedCabinetId(event.target.value)}><option value="">{t("contours.chooseBoundCabinet")}</option>{cabinetBindings.filter((row) => boundCabinetIds.includes(recordId(row))).map((row) => <option key={recordId(row)} value={recordId(row)}>{rowName(row, "cabinet_name", "cabinet_id")}</option>)}</select></label>
            {selectedCabinetId && <span>{installed
              ? t("contours.installationInstalled", {
                status: enumLabel(installed.status, t),
                kind: installed.bind_kind === "local" || installed.bind_kind === "global" ? enumLabel(installed.bind_kind, t) : t("contours.notSpecifiedM"),
                edit: typeof installed.child_may_edit === "boolean" ? installed.child_may_edit ? t("common.yes") : t("common.no") : t("contours.notSpecifiedN"),
              })
              : t("contours.installationMissing")}</span>}
          </div>
          {selectedCabinetId && <div className="module-admin__templates"><h4>{t("contours.availableTemplates")}</h4>{!templateTableAvailable && <p className="module-admin__gap">{t("contours.templatesUnavailable")}</p>}{templateTableAvailable && templates.length === 0 && <p>{t("contours.templatesEmpty")}</p>}{templates.map((row, index) => {
            const body = row.body as Record<string, unknown>;
            const file = typeof body.file === "object" && body.file !== null ? body.file as Record<string, unknown> : {};
            return <div key={recordId(row, `template-${index}`)}><strong>{displayValue(body.name ?? body.label ?? body.template_type)}</strong><span>{displayValue(body.template_type)}</span><small>{displayValue(file.filename ?? file.name ?? file.storage_key)}</small></div>;
          })}</div>}
        </section>

        <section className="module-admin__section"><div className="module-admin__section-heading"><h3>{t("contours.projectBindingsTitle")}</h3><span>{t("contours.boundCount", { count: projectBindings.length })}</span></div>
          {projectBindings.length === 0 && <p>{t("contours.projectsEmpty")}</p>}
          {projectBindings.length > 0 && <div className="module-admin__table-scroll"><table><thead><tr><th>{t("contours.colProject")}</th><th>{t("contours.colCompany")}</th><th>{t("contours.colCabinet")}</th><th>{t("contours.colBindKind")}</th><th>{t("contours.colChildRights")}</th><th>{t("contours.colUsage")}</th><th>{t("contours.colManagement")}</th></tr></thead><tbody>{projectBindings.map((project) => {
            const id = recordId(project);
            return <tr key={id}><td><Link to={`/projects/${encodeURIComponent(id)}/settings`} title={id}>{rowName(project, "project_name", "project_id")}</Link></td><td>{displayValue(project.company_name ?? project.company_id)}</td><td>{displayValue(project.cabinet_id)}</td><td>{enumLabel(project.bind_kind, t)}</td><td>{typeof project.child_may_edit === "boolean" ? project.child_may_edit ? t("contours.rightsChildMayEdit") : t("contours.rightsChildReadOnly") : t("contours.notSpecifiedPlural")}</td><td>{t("contours.usageCell", { tokens: displayValue(project.agent_tokens_used), requests: displayValue(project.agent_requests) })}</td><td><Link to={`/projects/${encodeURIComponent(id)}/settings`}>{t("contours.manageProject")}</Link></td></tr>;
          })}</tbody></table></div>}
          <p className="module-admin__muted">{t("contours.projectBindingsHint")}</p>
        </section>

        <section className="module-admin__section"><h3>{t("contours.moduleMetadataTitle")}</h3>{metaDocuments.length === 0 ? <p>{t("contours.metadataEmpty")}</p> : <div className="module-admin__meta-list">{metaDocuments.map((document) => <details key={stringValue(document, "slug")}><summary>{stringValue(document, "slug") || t("contours.unnamedDocument")}<small>{t("contours.updatedAt", { at: displayValue(document.updated_at) })}</small></summary><pre>{JSON.stringify(document.body, null, 2)}</pre></details>)}</div>}</section>
        {selectedCabinet && <p className="module-admin__muted">{t("contours.bindingCabinetFooter", { name: rowName(selectedCabinet, "cabinet_name", "cabinet_id") })}</p>}
      </section>}
    </div>
    {pending && <div className="module-admin__confirm" role="alertdialog" aria-modal="true" aria-labelledby="module-admin-confirm-title">
      <h2 id="module-admin-confirm-title">{t("contours.confirmTitle")}</h2>
      {pending.kind === "grants" && <><p>{t("contours.grantsSummary", { added: pending.added, removed: pending.removed })}</p>{pending.removed > 0 && <p>{t("contours.grantsWarning")}</p>}</>}
      {pending.kind === "cabinets" && <><p>{t("contours.cabinetsSummary", { added: pending.added, removed: pending.removed })}</p>{pending.removed > 0 && <p>{t("contours.cabinetsWarning")}</p>}</>}

      <div><button type="button" disabled={busy} onClick={confirmPending}>{t("contours.confirm")}</button><button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("common.cancel")}</button></div>
    </div>}
  </main>;
}

function sameIds(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((id) => right.includes(id));
}
