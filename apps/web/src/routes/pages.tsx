import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useApi, useAuth } from "../app/context";
import type { ApiClient, Cabinet, Project } from "../api/types";
import { ProjectsOverview, createProjectOverviewService } from "../features/projects/overview";
import { ProjectControlsPanel, createDefaultProjectControlsService } from "../features/projects/controls";
import { ProjectRuntime, createProjectRuntimeService, type RuntimeTab } from "../features/project-runtime";
import { ChatWorkspace, useChatWorkspace } from "../features/chat";
import { ProjectFilesWorkspace, createWorkspaceService } from "../features/files";
import { CabinetOverview, createCabinetOverviewService, type CabinetNavigationTarget } from "../features/cabinet";
import { RESOURCE_CONTRACT_BLOCKERS } from "../features/resources";
import { ContentOpsPanel, createContentOpsService } from "../features/resources/content-ops";
import { RunsCenterPage } from "../features/runs";
import { EquipmentProcurementPage } from "../features/equipment";
import { ModelsPicker, createModelsService } from "../features/models";
import { CompanyContour, AdminContour, createContourService } from "../features/contours";
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
  const [searchParams] = useSearchParams();
  const { tokens } = auth;
  const service = useMemo(() => createProjectOverviewService({ getAccessToken: () => tokens?.access_token }), [tokens?.access_token]);
  if (!auth.employee) return <RouteFeedback title="Employee identity unavailable" detail="Project selection requires a confirmed employee identity." />;
  return <ProjectsOverview service={service} employeeId={auth.employee.id} initialCabinetId={searchParams.get("cabinet") ?? undefined} />;
}

export function ProjectOverviewPage({ project, settings = false }: { project: Project; settings?: boolean }) {
  const auth = useAuth();
  const controls = useMemo(() => createDefaultProjectControlsService(() => auth.tokens?.access_token), [auth.tokens?.access_token]);
  const projectLinks = {
    runtime: `/projects/${encodeURIComponent(project.id)}/runtime`,
    chat: `/projects/${encodeURIComponent(project.id)}/chat`,
    files: `/projects/${encodeURIComponent(project.id)}/files`,
    models: `/projects/${encodeURIComponent(project.id)}/models`,
  };
  if (settings) return <ProjectControlsPanel projectId={project.id} service={controls} initialProject={project} links={projectLinks} />;
  return <div className="employee-project-overview">
    <PageHeading eyebrow="Project" title={project.name} detail={`Project ${project.id} · ${project.status}`} />
    <div className="employee-project-links" aria-label="Project workflows">
      <Link to={`/projects/${project.id}/runtime`}>Runtime</Link><Link to={`/projects/${project.id}/chat`}>Chat</Link>
      <Link to={`/projects/${project.id}/files`}>Files</Link><Link to={`/projects/${project.id}/runs`}>Runs and approvals</Link>
      <Link to={`/projects/${project.id}/resources`}>Resources</Link><Link to={`/projects/${project.id}/equipment`}>Equipment</Link>
      <Link to={`/projects/${project.id}/automations`}>Automations</Link><Link to={`/projects/${project.id}/models`}>Models</Link>
      <Link to={`/projects/${project.id}/settings`}>Project settings</Link>
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
  return <section className={approvalsOnly ? "employee-approvals-page" : undefined}>
    {approvalsOnly && <PageHeading eyebrow="Project" title="Approvals" detail="Pending decisions reported by the session API." />}
    <RunsCenterPage />
  </section>;
}

export function ProjectResourcesPage({ project }: { project: Project }) {
  const api = useApi();
  const auth = useAuth();
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
      if (resolvedProject.id !== project.id) throw new Error("The Project API returned a different project; company scope could not be verified.");
      const companyId = resolvedProject.company_id?.trim();
      if (!companyId) throw new Error("The Project API did not return a company_id; content assets were not requested.");
      setVerifiedScope({ projectId: resolvedProject.id, companyId });
    }).catch((cause: unknown) => {
      if (current) setScopeError(cause instanceof Error ? cause.message : "Project company scope could not be verified.");
    }).finally(() => {
      if (current) setScopeLoading(false);
    });
    return () => { current = false; };
  }, [api, project.id, scopeAttempt]);

  const currentScope = verifiedScope?.projectId === project.id ? verifiedScope : undefined;
  return <section>
    <PageHeading eyebrow="Project resources" title="Resources" detail={`Bound module data and company assets for ${project.name} · project ${project.id}.`} />
    <ProjectModuleResources projectId={project.id} accessToken={auth.tokens?.access_token} />
    <aside className="resources-blockers" aria-label="Profile and migration resource blockers">
      <div className="resources-blockers__header"><span className="resources-alert-icon" aria-hidden="true">!</span><div><p className="resources-eyebrow">Not connected</p><h2>Profile and migration blockers</h2></div><span className="resources-blockers__count">{RESOURCE_CONTRACT_BLOCKERS.length}</span></div>
      <div className="resources-blockers__list">{RESOURCE_CONTRACT_BLOCKERS.map((blocker) => <div className="resources-blocker" key={blocker.code}><div><strong>{blocker.title}</strong><span>{blocker.detail}</span></div><code>{blocker.code} · {blocker.owner}</code></div>)}</div>
    </aside>
    <section aria-labelledby="project-assets-title">
      <header className="employee-page-heading"><p className="eyebrow">Company-scoped content</p><h2 id="project-assets-title">Assets</h2><p className="muted">Authenticated company assets from the Content API. Asset scope is verified through the Project API.</p></header>
      {scopeLoading && <RouteFeedback title="Company context" detail="Verifying this project and its company scope through the Project API." />}
      {!scopeLoading && scopeError && <div className="employee-route-feedback" role="alert"><strong>Project company scope unavailable</strong><span>{scopeError}</span><button type="button" onClick={() => setScopeAttempt((attempt) => attempt + 1)}>Retry project scope</button></div>}
      {!scopeLoading && currentScope && <ContentOpsPanel companyId={currentScope.companyId} service={contentOpsService} />}
    </section>
  </section>;
}

export function ContentOperationsPage({ companyId }: { companyId?: string }) {
  const auth = useAuth();
  const scope = useCompanyScope(companyId);
  const service = useMemo(() => createContentOpsService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  if (scope.loading) return <RouteFeedback title="Company context" detail="Resolving the authenticated company scope from the current-user API." />;
  if (!scope.companyId) return <RouteFeedback title="Company context unavailable" detail="The current-user API returned no company scope for content operations." />;
  return <ContentOpsPanel companyId={scope.companyId} service={service} />;
}

export function EquipmentPage() {
  return <section><PageHeading eyebrow="Project data" title="Equipment procurement" detail="This surface uses its declared local typed adapter; no live equipment backend is claimed." /><EquipmentProcurementPage /></section>;
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
  const [cabinets, setCabinets] = useState<Cabinet[]>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    let active = true;
    void api.listCabinets().then((items) => { if (active) setCabinets(items); }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Не удалось загрузить кабинеты."); });
    return () => { active = false; };
  }, [api]);
  return <section><PageHeading eyebrow="Employee workspace" title="Cabinets" detail="Choose a cabinet to open its projects and module navigation." />
    {error && <p role="alert" className="error">{error}</p>}
    {!cabinets && !error && <p role="status">Загрузка кабинетов…</p>}
    <div className="employee-cabinet-list">{cabinets?.map((cabinet) => <Link key={cabinet.id} to={`/cabinets/${encodeURIComponent(cabinet.id)}`}><strong>{cabinet.name}</strong><span>{cabinet.projects_count ?? "—"} projects</span></Link>)}</div>
    {cabinets?.length === 0 && <p>Для этого сотрудника нет доступных кабинетов.</p>}
  </section>;
}

export function CabinetPage() {
  const { cabinetId = "" } = useParams();
  const api = useApi();
  const auth = useAuth();
  const navigate = useNavigate();
  const service = useMemo(() => createCabinetOverviewService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const [cabinet, setCabinet] = useState<Cabinet>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    let active = true;
    void api.getCabinet(cabinetId).then((item) => { if (active) setCabinet(item); }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Cabinet is unavailable."); });
    return () => { active = false; };
  }, [api, cabinetId]);
  if (error) return <RouteFeedback title="Cabinet unavailable" detail={error} action={<Link to="/cabinets">Cabinet list</Link>} />;
  if (!cabinet) return <p role="status">Loading cabinet…</p>;
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
  const scope = useCompanyScope();
  const contour = useMemo(() => createContourService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const moduleService = useMemo(() => createModuleAdminService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const contentService = useMemo(() => createContentOpsService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  if (scope.loading) return <RouteFeedback title="Company context" detail="Resolving the authenticated company contour from the current-user API." />;
  if (!scope.companyId) return <RouteFeedback title="Company contour unavailable" detail="The current-user API returned no company scope for this account." />;
  return <Routes>
    <Route index element={<CompanyContour companyId={scope.companyId} service={contour} />} />
    <Route path="modules" element={<ModuleAdminPage mode="company" companyId={scope.companyId} service={moduleService} />} />
    <Route path="content" element={<ContentOpsPanel companyId={scope.companyId} service={contentService} />} />
    <Route path="*" element={<NotFoundPage />} />
  </Routes>;
}

export function AdminPage() {
  const auth = useAuth();
  const service = useMemo(() => createContourService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  const moduleService = useMemo(() => createModuleAdminService({ getAccessToken: () => auth.tokens?.access_token }), [auth.tokens?.access_token]);
  return <Routes>
    <Route index element={<AdminContour service={service} />} />
    <Route path="modules" element={<ModuleAdminPage mode="admin" service={moduleService} />} />
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
  return <section className="employee-not-found"><PageHeading eyebrow="404" title="Page not found" detail={`No workspace route matches ${location.pathname}.`} /><Link to="/projects">Back to projects</Link></section>;
}