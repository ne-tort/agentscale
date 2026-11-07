import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useApi, useAuth } from "../app/context";
import { useI18n } from "../i18n/useT";
import type { ApiClient, Cabinet, Project } from "../api/types";
import { ProjectsOverview, createProjectOverviewService } from "../features/projects/overview";
import { ProjectControlsPanel, createDefaultProjectControlsService } from "../features/projects/controls";
import { ProjectRuntime, createProjectRuntimeService, type RuntimeTab } from "../features/project-runtime";
import { ChatWorkspace, useChatWorkspace } from "../features/chat";
import { ProjectFilesWorkspace, createWorkspaceService } from "../features/files";
import { CabinetOverview, createCabinetOverviewService, type CabinetNavigationTarget } from "../features/cabinet";
import { ContentOpsPanel, createContentOpsService } from "../features/resources/content-ops";
import { RunsCenterPage } from "../features/runs";
import { EquipmentProcurementPage } from "../features/equipment";
import { ModelsPicker, createModelsService } from "../features/models";
import { CompanyContour, createContourService } from "../features/contours";
import { ModuleAdminPage, createModuleAdminService } from "../features/contours/module-admin";
import { ModuleRuntime } from "../features/modules/ModuleRuntime";
import { createModuleRuntimeService } from "../features/modules/service";
import { ProjectModuleResources } from "../features/resources/ProjectModuleResources";

function PageHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail?: string }) {
  return <header className="employee-page-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{detail && <p className="muted">{detail}</p>}</header>;
}

function RouteFeedback({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <section className="employee-route-feedback" role="status"><strong>{title}</strong><span>{detail}</span>{action}</section>;
}

function useCompanyScope(explicitCompanyId?: string): { companyId?: string; loading: boolean } {
  const api = useApi();
  const [companyId, setCompanyId] = useState(explicitCompanyId);
  const [loading, setLoading] = useState(!explicitCompanyId);
  useEffect(() => {
    if (explicitCompanyId) {
      setCompanyId(explicitCompanyId);
      setLoading(false);
      return;
    }
    let current = true;
    setCompanyId(undefined);
    setLoading(true);
    void api.me().then((user) => {
      if (current) setCompanyId(user.company_id ?? user.company?.id ?? user.employee?.memberships?.[0]?.company_id);
    }).catch(() => { if (current) setCompanyId(undefined); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [api, explicitCompanyId]);
  return { companyId, loading };
}

async function createModelSession(api: Pick<ApiClient, "createSession">, projectId: string, modelId: string): Promise<string> {
  const session = await api.createSession(projectId, { model: modelId });
  if (!session.id || session.project_id !== projectId) throw new Error("Backend did not confirm the new model session.");
  return session.id;
}

// The login screen has its own module: it owns preference state (language,
// theme) and a settings popover, which does not belong in this barrel of
// workspace pages.
export { LoginPage } from "./LoginPage";

export function ProjectsPage() {
  const auth = useAuth();
  const { t } = useI18n();
  const [searchParams] = useSearchParams();
  const { tokens } = auth;
  const service = useMemo(() => createProjectOverviewService({ getAccessToken: () => tokens?.access_token }), [tokens?.access_token]);
  if (!auth.employee) return <RouteFeedback title={t("pages.employeeUnavailable")} detail={t("pages.employeeUnavailableDetail")} />;
  return <ProjectsOverview service={service} employeeId={auth.employee.id} initialCabinetId={searchParams.get("cabinet") ?? undefined} />;
}

/** A status as a word; the API's own value stays out of the heading. */
export function projectStatusLabel(status: string | undefined, t: (key: string) => string): string {
  if (status === "paused") return t("pages.projectStatusPaused");
  if (status === "completed") return t("pages.projectStatusCompleted");
  if (status === "archived") return t("pages.projectStatusArchived");
  return t("pages.projectStatusActive");
}

export function ProjectOverviewPage({ project, settings = false }: { project: Project; settings?: boolean }) {
  const auth = useAuth();
  const { t } = useI18n();
  const controls = useMemo(() => createDefaultProjectControlsService(() => auth.tokens?.access_token), [auth.tokens?.access_token]);
  const projectLinks = {
    runtime: `/projects/${encodeURIComponent(project.id)}/runtime`,
    chat: `/projects/${encodeURIComponent(project.id)}/chat`,
    files: `/projects/${encodeURIComponent(project.id)}/files`,
    models: `/projects/${encodeURIComponent(project.id)}/models`,
  };
  if (settings) return <ProjectControlsPanel projectId={project.id} service={controls} initialProject={project} links={projectLinks} />;
  return <div className="employee-project-overview">
    <PageHeading eyebrow={t("pages.project")} title={project.name} detail={projectStatusLabel(project.status, t)} />
    <div className="employee-project-links" aria-label={t("pages.project")}>
      <Link to={`/projects/${project.id}/runtime`}>{t("workspace.navRuntime")}</Link><Link to={`/projects/${project.id}/chat`}>{t("workspace.navChat")}</Link>
      <Link to={`/projects/${project.id}/files`}>{t("workspace.navFiles")}</Link><Link to={`/projects/${project.id}/runs`}>{t("workspace.navRuns")}</Link>
      <Link to={`/projects/${project.id}/resources`}>{t("workspace.navResources")}</Link><Link to={`/projects/${project.id}/equipment`}>{t("workspace.navEquipment")}</Link>
      <Link to={`/projects/${project.id}/automations`}>{t("workspace.navAutomations")}</Link><Link to={`/projects/${project.id}/models`}>{t("workspace.navModels")}</Link>
      <Link to={`/projects/${project.id}/settings`}>{t("workspace.navSettings")}</Link>
    </div>
    <ProjectControlsPanel projectId={project.id} service={controls} initialProject={project} links={projectLinks} />
  </div>;
}

function tabFromLocation(pathname: string): RuntimeTab {
  if (pathname.endsWith("/runtime/chat") || pathname.endsWith("/chat")) return "chat";
  if (pathname.endsWith("/runtime/files") || pathname.endsWith("/files")) return "files";
  if (pathname.endsWith("/runtime/settings") || pathname.endsWith("/settings")) return "settings";
  return "overview";
}

export function ProjectRuntimePage({ project }: { project: Project }) {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const api = useApi();
  const service = useMemo(() => createProjectRuntimeService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const base = `/projects/${encodeURIComponent(project.id)}`;
  return <ProjectRuntime
    key={location.pathname}
    projectId={project.id}
    service={service}
    initialTab={tabFromLocation(location.pathname)}
    onNavigate={(tab, _projectId, sessionId) => {
      if (tab === "chat") navigate(`${base}/chat${sessionId ? `/${encodeURIComponent(sessionId)}` : ""}`);
      else if (tab === "files") navigate(`${base}/files`);
      else if (tab === "settings") navigate(`${base}/runtime/settings`);
      else navigate(base);
    }}
    onModelChange={async (_projectId, modelId) => {
      const sessionId = await createModelSession(api, project.id, modelId);
      navigate(`${base}/chat/${encodeURIComponent(sessionId)}?model=${encodeURIComponent(modelId)}`);
    }}
  />;
}

export function ProjectChatPage({ cabinetId }: { cabinetId: string }) {
  const { projectId = "", sessionId } = useParams();
  const auth = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const searchParams = new URLSearchParams(location.search);
  const modelId = searchParams.get("model") ?? undefined;
  const chat = useChatWorkspace({ projectId, cabinetId, initialSessionId: sessionId, accessToken: auth.tokens?.access_token, modelOptions: modelId ? ["Auto", modelId] : ["Auto"] });
  const { state, actions } = chat;
  const routeSession = sessionId ?? undefined;
  const selectSession = actions.selectSession;
  const setModel = actions.setModel;
  const startNewChat = actions.startNewChat;
  const selectSessionRef = useRef(selectSession);
  const setModelRef = useRef(setModel);
  const previousRouteSession = useRef(routeSession);
  const routeChangePending = useRef(false);
  selectSessionRef.current = selectSession;
  setModelRef.current = setModel;

  useEffect(() => {
    if (previousRouteSession.current === routeSession) return;
    previousRouteSession.current = routeSession;
    if (state.selectedSessionId === routeSession) return;
    routeChangePending.current = true;
    void selectSessionRef.current(routeSession);
  }, [routeSession]);
  useEffect(() => {
    if (modelId && state.model !== modelId) setModelRef.current(modelId);
  }, [modelId, state.model]);
  useEffect(() => {
    if (routeChangePending.current) {
      if (state.selectedSessionId === routeSession) routeChangePending.current = false;
      return;
    }
    if (state.selectedSessionId === routeSession) return;
    previousRouteSession.current = state.selectedSessionId;
    navigate(`/projects/${encodeURIComponent(projectId)}/chat${state.selectedSessionId ? `/${encodeURIComponent(state.selectedSessionId)}` : ""}${location.search}`, { replace: true });
  }, [location.search, navigate, projectId, routeSession, state.selectedSessionId]);


  return <ChatWorkspace controller={chat} onNewChat={() => { startNewChat(projectId); navigate(`/projects/${encodeURIComponent(projectId)}/chat${modelId ? `?model=${encodeURIComponent(modelId)}` : ""}`); }} />;
}

export function ProjectFilesPage() {
  const { projectId = "", ["*"]: pathSegments = "" } = useParams();
  const auth = useAuth();
  const [searchParams] = useSearchParams();
  const service = useMemo(() => createWorkspaceService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const initialPath = searchParams.get("path") ?? pathSegments;
  return <ProjectFilesWorkspace projectId={projectId} service={service} initialPath={initialPath} />;
}

export function RunsPage({ approvalsOnly = false }: { approvalsOnly?: boolean }) {
  const { t } = useI18n();
  return <section className={approvalsOnly ? "employee-approvals-page" : undefined}>
    {approvalsOnly && <PageHeading eyebrow={t("pages.project")} title={t("pages.approvalsTitle")} detail={t("pages.approvalsDetail")} />}
    <RunsCenterPage />
  </section>;
}

export function ProjectResourcesPage({ project }: { project: Project }) {
  const api = useApi();
  const auth = useAuth();
  const { t } = useI18n();
  const contentOpsService = useMemo(() => createContentOpsService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const [verifiedScope, setVerifiedScope] = useState<{ projectId: string; companyId: string }>();
  const [scopeLoading, setScopeLoading] = useState(true);
  const [scopeError, setScopeError] = useState<string>();
  const [scopeAttempt, setScopeAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setVerifiedScope(undefined);
    setScopeError(undefined);
    setScopeLoading(true);
    void api.getProject(project.id).then((resolvedProject) => {
      if (!current) return;
      if (resolvedProject.id !== project.id) throw new Error(t("pages.resourcesScopeChanged"));
      const companyId = resolvedProject.company_id?.trim();
      if (!companyId) throw new Error(t("pages.resourcesScopeMissingCompany"));
      setVerifiedScope({ projectId: resolvedProject.id, companyId });
    }).catch((cause: unknown) => {
      if (current) setScopeError(cause instanceof Error ? cause.message : t("pages.resourcesScopeUnverified"));
    }).finally(() => {
      if (current) setScopeLoading(false);
    });
    return () => { current = false; };
  }, [api, project.id, scopeAttempt, t]);

  const currentScope = verifiedScope?.projectId === project.id ? verifiedScope : undefined;
  // The panel's "what the backend does not yet support" aside used to sit here.
  // It was a note to the engineering team — five paragraphs of contract
  // vocabulary that a person opening their project's resources cannot act on.
  // The information lives in the repository, not on this screen.
  return <section>
    <PageHeading eyebrow={t("pages.resourcesEyebrow")} title={t("pages.resourcesTitle")} detail={t("pages.resourcesDetail", { name: project.name })} />
    <ProjectModuleResources projectId={project.id} accessToken={auth.tokens?.access_token} />
    <section aria-labelledby="project-assets-title">
      <header className="employee-page-heading"><p className="eyebrow">{t("pages.resourcesAssetsEyebrow")}</p><h2 id="project-assets-title">{t("pages.resourcesAssetsTitle")}</h2><p className="muted">{t("pages.resourcesAssetsDetail")}</p></header>
      {scopeLoading && <RouteFeedback title={t("pages.resourcesTitle")} detail={t("pages.resourcesScopeCheck")} />}
      {!scopeLoading && scopeError && <div className="employee-route-feedback" role="alert"><strong>{t("pages.resourcesScopeTitle")}</strong><span>{scopeError}</span><button type="button" onClick={() => setScopeAttempt((attempt) => attempt + 1)}>{t("pages.resourcesScopeRetry")}</button></div>}
      {!scopeLoading && currentScope && <ContentOpsPanel companyId={currentScope.companyId} service={contentOpsService} />}
    </section>
  </section>;
}

export function ContentOperationsPage({ companyId }: { companyId?: string }) {
  const auth = useAuth();
  const { t } = useI18n();
  const scope = useCompanyScope(companyId);
  const service = useMemo(() => createContentOpsService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  if (scope.loading) return <RouteFeedback title={t("pages.contentTitle")} detail={t("common.loading")} />;
  if (!scope.companyId) return <RouteFeedback title={t("pages.contentTitle")} detail={t("pages.contentUnavailableDetail")} />;
  return <ContentOpsPanel companyId={scope.companyId} service={service} />;
}

export function EquipmentPage() {
  const { t } = useI18n();
  return <section><PageHeading eyebrow={t("pages.project")} title={t("pages.equipmentTitle")} detail={t("pages.equipmentDetail")} /><EquipmentProcurementPage /></section>;
}

export function ProjectModelsPage({ projectId }: { projectId: string }) {
  const auth = useAuth();
  const api = useApi();
  const navigate = useNavigate();
  const service = useMemo(() => createModelsService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get("session") ?? undefined;
  return <ModelsPicker projectId={projectId} service={service} onModelChange={async (_id, modelId) => {
    const selectedSessionId = await createModelSession(api, projectId, modelId);
    navigate(`/projects/${encodeURIComponent(projectId)}/chat/${encodeURIComponent(selectedSessionId)}?model=${encodeURIComponent(modelId)}${sessionId ? `&from_session=${encodeURIComponent(sessionId)}` : ""}`);
  }} />;
}

export function CabinetSelectionPage() {
  const api = useApi();
  const { t, plural } = useI18n();
  const auth = useAuth();
  const [cabinets, setCabinets] = useState<Cabinet[]>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    let active = true;
    if (!auth.employee) {
      // Cabinets are an employee surface: the API answers `403 employee
      // required`. Saying that on screen told the reader nothing, so the page
      // explains the situation instead of relaying the error.
      setCabinets([]);
      setError(undefined);
      return;
    }
    void api.listCabinets().then((items) => { if (active) setCabinets(items); }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : t("pages.cabinetsFailed")); });
    return () => { active = false; };
  }, [api, t, auth.employee]);
  if (!auth.employee) {
    return <section>
      <PageHeading eyebrow={t("pages.cabinetsEyebrow")} title={t("pages.cabinetsTitle")} detail={t("pages.cabinetsDetail")} />
      <RouteFeedback
        action={<Link to={auth.isCompany ? "/company" : "/projects"}>{auth.isCompany ? t("nav.company") : t("nav.projects")}</Link>}
        detail={t("pages.cabinetsNoEmployeeDetail")}
        title={t("pages.cabinetsNoEmployee")}
      />
    </section>;
  }
  return <section><PageHeading eyebrow={t("pages.cabinetsEyebrow")} title={t("pages.cabinetsTitle")} detail={t("pages.cabinetsDetail")} />
    {error && <p role="alert" className="error">{error}</p>}
    {!cabinets && !error && <p role="status">{t("pages.cabinetsLoading")}</p>}
    <div className="employee-cabinet-list">{cabinets?.map((cabinet) => <Link key={cabinet.id} to={`/cabinets/${encodeURIComponent(cabinet.id)}`}><strong>{cabinet.name}</strong><span>{plural("pages.cabinetProjects", cabinet.projects_count ?? 0)}</span></Link>)}</div>
    {cabinets?.length === 0 && <p>{t("pages.cabinetsEmpty")}</p>}
  </section>;
}

export function CabinetPage() {
  const { cabinetId = "" } = useParams();
  const api = useApi();
  const auth = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const service = useMemo(() => createCabinetOverviewService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const [cabinet, setCabinet] = useState<Cabinet>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    let active = true;
    void api.getCabinet(cabinetId).then((item) => { if (active) setCabinet(item); }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : t("pages.cabinetUnavailable")); });
    return () => { active = false; };
  }, [api, cabinetId, t]);
  if (error) return <RouteFeedback title={t("pages.cabinetUnavailable")} detail={error} action={<Link to="/cabinets">{t("pages.cabinetList")}</Link>} />;
  if (!cabinet) return <p role="status">{t("common.loading")}</p>;
  function go(target: CabinetNavigationTarget) {
    if (target.type === "projects") navigate(`/projects?cabinet=${encodeURIComponent(cabinetId)}`);
    else if (target.type === "project") navigate(`/projects/${encodeURIComponent(target.projectId)}`);
    else if (target.type === "chat") navigate(`/projects/${encodeURIComponent(target.projectId)}/chat${target.sessionId ? `/${encodeURIComponent(target.sessionId)}` : ""}`);
    else navigate(`/projects/${encodeURIComponent(target.projectId)}/modules/${encodeURIComponent(target.moduleId)}/${encodeURIComponent(target.viewSlug)}`);
  }
  return <CabinetOverview cabinetId={cabinet.id} cabinetName={cabinet.name} service={service} onNavigate={go} />;
}

export function CompanyPage() {
  const auth = useAuth();
  const { t } = useI18n();
  const scope = useCompanyScope();
  const contour = useMemo(() => createContourService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const moduleService = useMemo(() => createModuleAdminService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const contentService = useMemo(() => createContentOpsService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  if (scope.loading) return <RouteFeedback title={t("nav.company")} detail={t("common.loading")} />;
  // A platform administrator has no company of their own. That is not a fault in
  // the account, so the screen offers the thing they were probably after rather
  // than reporting that a field is missing.
  if (!scope.companyId) {
    return <RouteFeedback
      action={auth.isPlatformAdmin ? <Link to="/admin">{t("nav.admin")}</Link> : undefined}
      detail={auth.isPlatformAdmin ? t("company.adminHasNoCompany") : t("company.noCompanyScope")}
      title={t("nav.company")}
    />;
  }
  return <Routes>
    <Route index element={<CompanyContour companyId={scope.companyId} service={contour} />} />
    <Route path="modules" element={<ModuleAdminPage mode="company" companyId={scope.companyId} service={moduleService} />} />
    <Route path="content" element={<ContentOpsPanel companyId={scope.companyId} service={contentService} />} />
    <Route path="*" element={<NotFoundPage />} />
  </Routes>;
}

export function ModuleBindingPage() {
  const { projectId = "", moduleId = "" } = useParams();
  const { tokens } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const service = useMemo(() => createModuleRuntimeService({ getAccessToken: () => tokens?.access_token }), [tokens?.access_token]);
  const routeSegments = location.pathname.split("/").filter(Boolean);
  const moduleIndex = routeSegments.lastIndexOf("modules");
  const viewSlug = moduleIndex >= 0 ? routeSegments[moduleIndex + 2] : undefined;
  return <ModuleRuntime projectId={projectId} moduleId={moduleId} service={service} initialViewSlug={viewSlug} onBack={() => navigate(`/projects/${encodeURIComponent(projectId)}`)} onOpenProject={(id) => navigate(`/projects/${encodeURIComponent(id)}`)} />;
}

export function NotFoundPage() {
  const location = useLocation();
  const { t } = useI18n();
  const auth = useAuth();
  return <section className="employee-not-found">
    <PageHeading eyebrow="404" title={t("nav.notFoundTitle")} detail={t("nav.notFoundDetail")} />
    <Link to={auth.isPlatformAdmin ? "/admin" : "/projects"}>{auth.isPlatformAdmin ? t("nav.admin") : t("nav.notFoundBack")}</Link>
    <code className="employee-not-found__path">{location.pathname}</code>
  </section>;
}