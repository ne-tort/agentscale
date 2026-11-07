import { useEffect, useState, type ReactNode } from "react";
import { Link, matchPath, Navigate, Outlet, Route, Routes, useLocation, useOutletContext, useParams } from "react-router-dom";
import { useApi, useAuth, useWorkContext } from "./context";
import { getSafeNextPath } from "./auth/redirect";
import type { Project, ProjectRuntimeModule } from "../api/types";
import { createWorkspaceRouteContract, WorkspaceShell } from "./workspace-shell";
import {
  CabinetPage,
  CabinetSelectionPage,
  CompanyPage,
  ContentOperationsPage,
  EquipmentPage,
  LoginPage,
  ModuleBindingPage,
  NotFoundPage,
  ProjectChatPage,
  ProjectFilesPage,
  ProjectModelsPage,
  ProjectOverviewPage,
  ProjectRuntimePage,
  ProjectsPage,
  ProjectResourcesPage,
  RunsPage,
} from "../routes/pages";
import {
  AdminArea,
  CompaniesPage,
  CompanyDetailPage,
  DeletedPage,
  KeysPage,
  ModulesPage,
} from "../features/admin";
import { useI18n } from "../i18n/useT";
import { AutomationsPage } from "../features/automations";
import { DynamicModulePage, DynamicModulesPage } from "../features/dynamic-modules";

const APP_FRAME_STYLES = `
.employee-app-frame{min-height:100vh;background:var(--bg);color:var(--text)}
.employee-app-header{align-items:center;background:#102b2c;color:#edf5f2;display:grid;gap:18px;grid-template-columns:auto minmax(0,1fr) auto;min-height:68px;padding:10px 24px}
.employee-app-brand{color:#fff;font-size:16px;font-weight:800;white-space:nowrap}
.employee-app-navigation{align-items:center;display:flex;gap:5px;min-width:0;overflow-x:auto;scrollbar-width:thin;white-space:nowrap}
.employee-app-navigation a{border-bottom:2px solid transparent;color:#c2d5cf;font-size:13px;padding:12px 10px}
.employee-app-navigation a:hover,.employee-app-navigation a:focus-visible{border-bottom-color:#80c6ae;color:#fff}
.employee-app-account{align-items:center;display:flex;gap:12px;font-size:12px;white-space:nowrap}
.employee-app-account button{background:transparent;border:1px solid #6c8a81;border-radius:4px;color:#fff;padding:7px 10px}
.employee-app-content{min-height:calc(100vh - 68px);margin:0 auto;max-width:1160px;padding:36px 32px 80px;width:100%}
.employee-app-content--wide{max-width:none;padding:0}
.employee-page-heading{margin:0 0 22px}
.employee-page-heading h1{font-size:32px;line-height:1.15}
.employee-page-heading>.muted{margin:9px 0 0}
.employee-project-links{border-bottom:1px solid var(--border);display:flex;gap:6px;margin:0 0 24px;overflow-x:auto;white-space:nowrap}
.employee-project-links a{border-bottom:2px solid transparent;color:var(--text-muted);font-size:13px;font-weight:650;padding:11px 10px}
.employee-project-links a:hover,.employee-project-links a:focus-visible{border-bottom-color:var(--accent);color:var(--accent)}
.employee-route-feedback,.employee-route-state{align-items:flex-start;background:var(--surface);border-left:3px solid var(--warning-border);display:grid;gap:8px;margin:20px 0;padding:18px}
.employee-route-feedback a,.employee-route-state a{color:var(--accent);font-weight:700}
.employee-cabinet-list{display:grid;max-width:900px}
.employee-cabinet-list a{align-items:center;border-bottom:1px solid var(--border);display:flex;gap:16px;justify-content:space-between;padding:15px 4px}
.employee-cabinet-list a span{color:var(--text-muted);font-size:12px}
.employee-not-found{padding:20px 0}
.employee-not-found>a{color:var(--accent);font-weight:700}
.employee-not-found__path{color:var(--text-muted);display:block;font-size:12px;margin-top:12px;overflow-wrap:anywhere}
.employee-auth-state{padding:40px;text-align:center}
.employee-blocker-note{border-left:3px solid #ad5d4c;color:#724138;margin:18px 0;padding:9px 13px}
@media(max-width:900px){.employee-app-header{gap:8px 14px;grid-template-columns:auto minmax(0,1fr)}.employee-app-navigation{grid-column:1/-1;grid-row:2}.employee-app-account{grid-column:2;grid-row:1;justify-self:end}.employee-app-content{min-height:calc(100vh - 108px);padding:24px 16px 60px}}
@media(max-width:520px){.employee-app-header{padding:8px 12px}.employee-app-account span{display:none}.employee-app-navigation{gap:0}.employee-app-navigation a{font-size:12px;padding:10px 8px}.employee-project-links a{font-size:12px;padding:10px 8px}}
`;

export const EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS = [
  "runtime/*", "chat", "chat/:sessionId", "files/*", "runs", "approvals", "resources/*",
  "content", "equipment", "automations", "models", "settings", "modules/:moduleId/*",
] as const;

function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();
  const next = getSafeNextPath(`?next=${encodeURIComponent(`${location.pathname}${location.search}${location.hash}`)}`);
  if (auth.isLoading) return <main role="status" className="employee-auth-state">Restoring your session…</main>;
  return auth.isAuthenticated ? <Outlet /> : <Navigate to={`/login${next ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;
}

function AppFrame() {
  const auth = useAuth();
  const location = useLocation();
  const { t } = useI18n();
  const isProjectWorkspace = matchPath("/projects/:projectId", location.pathname) !== null
    || matchPath("/projects/:projectId/*", location.pathname) !== null;
  // The bar shows what this account can actually do. A platform administrator
  // has no company and no cabinets, and a company account has no employee, so
  // both used to be offered links to screens that answered "unavailable".
  const links = auth.isPlatformAdmin
    ? [{ to: "/admin", label: t("nav.admin") }]
    : auth.isCompany
      ? [{ to: "/company", label: t("nav.company") }]
      : [
          { to: "/projects", label: t("nav.projects") },
          { to: "/cabinets", label: t("nav.cabinets") },
          { to: "/company", label: t("nav.company") },
        ];
  return <div className="employee-app-frame">
    <style>{APP_FRAME_STYLES}</style>
    {!isProjectWorkspace && <header className="employee-app-header">
      <Link to={homePathFor(auth.isPlatformAdmin, auth.isCompany)} className="employee-app-brand">agentscale</Link>
      <nav aria-label={t("nav.menu")} className="employee-app-navigation">
        {links.map((link) => <Link key={link.to} to={link.to}>{link.label}</Link>)}
      </nav>
      <div className="employee-app-account">
        <span>{auth.isPlatformAdmin ? t("nav.accountAdmin") : auth.isCompany ? t("nav.accountCompany") : auth.employee?.display_name ?? t("nav.accountEmployee")}</span>
        <button type="button" onClick={auth.signOut}>{t("nav.signOut")}</button>
      </div>
    </header>}
    <div className={`employee-app-content${isWidePage(location.pathname) ? " employee-app-content--wide" : ""}`}>
      <Outlet />
    </div>
  </div>;
}

/**
 * Screens that own their own measure, and would end up double-padded by the
 * shell: the administrator's area caps itself, and the notification desk and the
 * module runtime fill the viewport by design.
 */
function isWidePage(pathname: string): boolean {
  return pathname === "/admin" || pathname.startsWith("/admin/") || pathname.startsWith("/projects/");
}

/** Where an account lands, by the contour it signed in with. */
function homePathFor(isPlatformAdmin: boolean, isCompany: boolean): string {
  if (isPlatformAdmin) return "/admin";
  if (isCompany) return "/company";
  return "/projects";
}

function HomeRedirect() {
  const auth = useAuth();
  return <Navigate replace to={homePathFor(auth.isPlatformAdmin, auth.isCompany)} />;
}

function ProjectsGate() {
  const auth = useAuth();
  // An administrator has no projects; a company account has no employee. Both
  // are sent to where they actually work instead of to a list that errors.
  if (auth.isPlatformAdmin) return <Navigate replace to="/admin" />;
  if (auth.isCompany) return <Navigate replace to="/company" />;
  return <ProjectsPage />;
}

function ProjectLayout() {
  const { projectId = "" } = useParams();
  const api = useApi();
  const auth = useAuth();
  const { t } = useI18n();
  const { setProject } = useWorkContext();
  const location = useLocation();
  const [project, setProjectState] = useState<Project>();
  const [loadedProjectId, setLoadedProjectId] = useState<string>();
  const [cabinet, setCabinet] = useState<{ id: string; name: string; description?: string }>();
  const [error, setError] = useState<{ projectId: string; detail: string }>();
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [verifiedModule, setVerifiedModule] = useState<{ projectId: string; moduleId: string; module: ProjectRuntimeModule }>();
  const [moduleState, setModuleState] = useState<{ projectId: string; moduleId: string; status: "loading" | "missing" | "error"; error?: string }>();
  const [moduleAttempt, setModuleAttempt] = useState(0);

  const moduleRoute = matchPath("/projects/:projectId/modules/:moduleId/*", location.pathname);
  const requestedModuleId = moduleRoute?.params.projectId === projectId ? moduleRoute.params.moduleId : undefined;

  useEffect(() => {
    let current = true;
    setProjectState(undefined);
    setLoadedProjectId(undefined);
    setCabinet(undefined);
    setError(undefined);
    setVerifiedModule(undefined);
    void api.getProject(projectId).then(async (loaded) => {
      // The thrown message is what the reader sees, so it comes from the unit.
      if (loaded.id !== projectId) throw new Error(t("workspace.projectMismatch"));
      const loadedCabinet = await api.getCabinet(loaded.cabinet_id);
      if (loadedCabinet.id !== loaded.cabinet_id) throw new Error(t("workspace.cabinetMismatch"));
      if (!current) return;
      setProjectState(loaded);
      setLoadedProjectId(loaded.id);
      setCabinet({ id: loadedCabinet.id, name: loadedCabinet.name, ...(typeof loadedCabinet.description === "string" ? { description: loadedCabinet.description } : {}) });
      setProject(loaded);
    }).catch((cause: unknown) => {
      if (current) setError({ projectId, detail: cause instanceof Error ? cause.message : t("workspace.projectLoadFailed") });
    });
    return () => { current = false; };
  }, [api, projectId, setProject, loadAttempt, t]);

  useEffect(() => {
    if (!requestedModuleId || loadedProjectId !== projectId || !project || !cabinet) {
      setModuleState(undefined);
      setVerifiedModule(undefined);
      return;
    }
    let current = true;
    setVerifiedModule(undefined);
    setModuleState({ projectId, moduleId: requestedModuleId, status: "loading" });
    void api.listProjectRuntimeModules(projectId).then(({ items }) => {
      if (!current) return;
      const confirmed = items.find((item) => item.id === requestedModuleId
        && (item.module_id === undefined || item.module_id === requestedModuleId));
      if (!confirmed) {
        setModuleState({ projectId, moduleId: requestedModuleId, status: "missing" });
        return;
      }
      setVerifiedModule({ projectId, moduleId: requestedModuleId, module: confirmed });
      setModuleState(undefined);
    }).catch((cause: unknown) => {
      if (current) setModuleState({
        projectId,
        moduleId: requestedModuleId,
        status: "error",
        error: cause instanceof Error ? cause.message : t("workspace.moduleCheckFailed"),
      });
    });
    return () => { current = false; };
  }, [api, cabinet, loadedProjectId, moduleAttempt, project, projectId, requestedModuleId, t]);

  const currentProject = loadedProjectId === projectId ? project : undefined;
  if (!currentProject || !cabinet) {
    const currentError = error?.projectId === projectId ? error.detail : undefined;
    if (!currentError) return <main role="status" className="employee-route-state">{t("workspace.loadingProject")}</main>;
    return <section className="employee-route-state" role="alert">
      <h1>{t("workspace.projectUnavailable")}</h1>
      <p>{currentError}</p>
      <button type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>{t("common.retry")}</button>{" "}
      <Link to="/projects">{t("workspace.backToProjects")}</Link>
      <button type="button" onClick={auth.signOut}>{t("workspace.signOut")}</button>
    </section>;
  }

  if (requestedModuleId) {
    const currentModuleState = moduleState?.projectId === projectId && moduleState.moduleId === requestedModuleId ? moduleState : undefined;
    const currentVerifiedModule = verifiedModule?.projectId === projectId && verifiedModule.moduleId === requestedModuleId ? verifiedModule.module : undefined;
    if (!currentVerifiedModule) {
      if (currentModuleState?.status === "error") return <section className="employee-route-state" role="alert">
        <h1>{t("workspace.moduleUnavailable")}</h1>
        <p>{currentModuleState.error}</p>
        <button type="button" onClick={() => setModuleAttempt((attempt) => attempt + 1)}>{t("workspace.retryModuleCheck")}</button>{" "}
        <Link to={`/projects/${encodeURIComponent(projectId)}`}>{t("workspace.backToProject")}</Link>
        <button type="button" onClick={auth.signOut}>{t("workspace.signOut")}</button>
      </section>;
      if (currentModuleState?.status === "missing") return <section className="employee-route-state" role="alert">
        <h1>{t("workspace.moduleNotConnected")}</h1>
        <p>{t("workspace.moduleNotConnectedDetail", { module: requestedModuleId })}</p>
        <Link to={`/projects/${encodeURIComponent(projectId)}`}>{t("workspace.backToProject")}</Link>
        <button type="button" onClick={auth.signOut}>{t("workspace.signOut")}</button>
      </section>;
      return <main role="status" className="employee-route-state">{t("workspace.checkingModule")}</main>;
    }
  }

  const confirmedModule = requestedModuleId ? verifiedModule?.module : undefined;
  const routeContract = createWorkspaceRouteContract({
    projectId: currentProject.id,
    ...(confirmedModule ? { moduleId: requestedModuleId, moduleLabel: confirmedModule.name ?? requestedModuleId } : {}),
  });
  return <WorkspaceShell
    account={{
      id: auth.employee?.id,
      displayName: auth.employee?.display_name ?? auth.employee?.email ?? t("nav.accountEmployee"),
      email: auth.employee?.email,
    }}
    cabinet={cabinet}
    onSignOut={auth.signOut}
    project={{ id: currentProject.id, name: currentProject.name, status: currentProject.status, statusLabel: currentProject.observed_state ?? currentProject.status }}
    routeContract={routeContract}
  >
    <Outlet context={currentProject} />
  </WorkspaceShell>;
}

function useCurrentProject(): Project {
  return useOutletContext<Project>();
}

function ProjectHomeRoute() { const project = useCurrentProject(); return <ProjectRuntimePage project={project} />; }
function ProjectRuntimeRoute() { const project = useCurrentProject(); return <ProjectRuntimePage project={project} />; }
function ProjectChatRoute() {
  const project = useCurrentProject();
  const location = useLocation();
  const modelId = new URLSearchParams(location.search).get("model") ?? "";
  return <ProjectChatPage key={`${project.id}:${modelId}`} cabinetId={project.cabinet_id} />;
}
function ProjectResourcesRoute() { const project = useCurrentProject(); return <ProjectResourcesPage project={project} />; }
function ProjectSettingsRoute() { const project = useCurrentProject(); return <ProjectOverviewPage project={project} settings />; }
function ProjectAutomationsRoute() { const project = useCurrentProject(); return <AutomationsPage projectId={project.id} />; }
function ProjectModelsRoute() { const project = useCurrentProject(); return <ProjectModelsPage projectId={project.id} />; }
function ProjectContentRoute() { const project = useCurrentProject(); return <ContentOperationsPage companyId={project.company_id} />; }
function ProjectModuleRoute() { return <ModuleBindingPage />; }

export function AppRouter() {
  return <Routes>
    <Route path="login" element={<LoginPage />} />
    <Route element={<RequireAuth />}>
      <Route element={<AppFrame />}>
        <Route index element={<HomeRedirect />} />
        <Route path="projects" element={<ProjectsGate />} />
        <Route path="projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<ProjectHomeRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[0]} element={<ProjectRuntimeRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[1]} element={<ProjectChatRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[2]} element={<ProjectChatRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[3]} element={<ProjectFilesPage />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[4]} element={<RunsPage />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[5]} element={<RunsPage approvalsOnly />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[6]} element={<ProjectResourcesRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[7]} element={<ProjectContentRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[8]} element={<EquipmentPage />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[9]} element={<ProjectAutomationsRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[10]} element={<ProjectModelsRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[11]} element={<ProjectSettingsRoute />} />
          <Route path={EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS[12]} element={<ProjectModuleRoute />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
        <Route path="cabinets" element={<CabinetSelectionPage />} />
        <Route path="cabinets/:cabinetId" element={<CabinetPage />} />
        <Route path="company/*" element={<CompanyPage />} />
        {/* The administrator's area: companies first, then what hangs off them. */}
        <Route element={<AdminArea />} path="admin">
          <Route index element={<CompaniesPage />} />
          <Route path="companies/:companyId" element={<CompanyDetailPage />} />
          <Route path="keys" element={<KeysPage />} />
          <Route path="modules" element={<ModulesPage />} />
          <Route path="deleted" element={<DeletedPage />} />
        </Route>
        {/* Dynamic module registry screens (definitions → versions → install → grants). */}
        <Route path="dynamic-modules" element={<DynamicModulesPage />} />
        <Route path="dynamic-modules/:definitionId" element={<DynamicModulePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Route>
  </Routes>;
}