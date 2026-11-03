import { useEffect, useState, type ReactNode } from "react";
import { Link, matchPath, Navigate, Outlet, Route, Routes, useLocation, useOutletContext, useParams } from "react-router-dom";
import { useApi, useAuth, useWorkContext } from "./context";
import { getSafeNextPath } from "./auth/redirect";
import type { Project, ProjectRuntimeModule } from "../api/types";
import { createWorkspaceRouteContract, WorkspaceShell } from "./workspace-shell";
import {
  AdminPage,
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
import { AutomationsPage } from "../features/automations";

const APP_FRAME_STYLES = `
.employee-app-frame{min-height:100vh;background:#f3f7f5;color:#17232b}
.employee-app-header{align-items:center;background:#102b2c;color:#edf5f2;display:grid;gap:18px;grid-template-columns:auto minmax(0,1fr) auto;min-height:68px;padding:10px 24px}
.employee-app-brand{color:#fff;font-size:16px;font-weight:800;white-space:nowrap}
.employee-app-navigation{align-items:center;display:flex;gap:5px;min-width:0;overflow-x:auto;scrollbar-width:thin;white-space:nowrap}
.employee-app-navigation a{border-bottom:2px solid transparent;color:#c2d5cf;font-size:13px;padding:12px 10px}
.employee-app-navigation a:hover,.employee-app-navigation a:focus-visible{border-bottom-color:#80c6ae;color:#fff}
.employee-app-account{align-items:center;display:flex;gap:12px;font-size:12px;white-space:nowrap}
.employee-app-account button{background:transparent;border:1px solid #6c8a81;border-radius:4px;color:#fff;padding:7px 10px}
.employee-app-content{min-height:calc(100vh - 68px)}
.employee-page-heading{margin:0 0 22px}
.employee-page-heading h1{font-size:32px;line-height:1.15}
.employee-page-heading>.muted{margin:9px 0 0}
.employee-project-links{border-bottom:1px solid #d6e0dc;display:flex;gap:6px;margin:0 0 24px;overflow-x:auto;white-space:nowrap}
.employee-project-links a{border-bottom:2px solid transparent;color:#405852;font-size:13px;font-weight:650;padding:11px 10px}
.employee-project-links a:hover,.employee-project-links a:focus-visible{border-bottom-color:#19776c;color:#145d55}
.employee-route-feedback,.employee-route-state{align-items:flex-start;background:#fff;border-left:3px solid #be7b35;display:grid;gap:8px;margin:20px 0;padding:18px}
.employee-route-feedback a,.employee-route-state a{color:#126b63;font-weight:700}
.employee-cabinet-list{display:grid;max-width:900px}
.employee-cabinet-list a{align-items:center;border-bottom:1px solid #d7e1dd;display:flex;gap:16px;justify-content:space-between;padding:15px 4px}
.employee-cabinet-list a span{color:#62736e;font-size:12px}
.employee-not-found{padding:20px 0}
.employee-not-found>a{color:#126b63;font-weight:700}
.employee-auth-state{padding:40px;text-align:center}
.employee-blocker-note{border-left:3px solid #ad5d4c;color:#724138;margin:18px 0;padding:9px 13px}
@media(max-width:900px){.employee-app-header{gap:8px 14px;grid-template-columns:auto minmax(0,1fr)}.employee-app-navigation{grid-column:1/-1;grid-row:2}.employee-app-account{grid-column:2;grid-row:1;justify-self:end}.employee-app-content{min-height:calc(100vh - 108px)}}
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
  const isProjectWorkspace = matchPath("/projects/:projectId", location.pathname) !== null
    || matchPath("/projects/:projectId/*", location.pathname) !== null;
  return <div className="employee-app-frame">
    <style>{APP_FRAME_STYLES}</style>
    {!isProjectWorkspace && <header className="employee-app-header">
      <Link to="/projects" className="employee-app-brand">agentscale</Link>
      <nav className="employee-app-navigation" aria-label="Application navigation">
        <Link to="/projects">Projects</Link><Link to="/cabinets">Cabinets</Link><Link to="/company">Company</Link><Link to="/company/modules">Module bindings</Link><Link to="/company/content">Content</Link><Link to="/admin/modules">Module catalog</Link><Link to="/admin">Admin</Link>
      </nav>
      <div className="employee-app-account"><span>{auth.employee?.display_name ?? "Employee"}</span><button type="button" onClick={auth.signOut}>Sign out</button></div>
    </header>}
    <div className="employee-app-content"><Outlet /></div>
  </div>;
}

function ProjectLayout() {
  const { projectId = "" } = useParams();
  const api = useApi();
  const auth = useAuth();
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
      if (loaded.id !== projectId) throw new Error("The project response did not match this URL.");
      const loadedCabinet = await api.getCabinet(loaded.cabinet_id);
      if (loadedCabinet.id !== loaded.cabinet_id) throw new Error("The cabinet response did not match this project.");
      if (!current) return;
      setProjectState(loaded);
      setLoadedProjectId(loaded.id);
      setCabinet({ id: loadedCabinet.id, name: loadedCabinet.name, ...(typeof loadedCabinet.description === "string" ? { description: loadedCabinet.description } : {}) });
      setProject(loaded);
    }).catch((cause: unknown) => {
      if (current) setError({ projectId, detail: cause instanceof Error ? cause.message : "Не удалось загрузить проект." });
    });
    return () => { current = false; };
  }, [api, projectId, setProject, loadAttempt]);

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
        error: cause instanceof Error ? cause.message : "Не удалось проверить доступность модуля.",
      });
    });
    return () => { current = false; };
  }, [api, cabinet, loadedProjectId, moduleAttempt, project, projectId, requestedModuleId]);

  const currentProject = loadedProjectId === projectId ? project : undefined;
  if (!currentProject || !cabinet) {
    const currentError = error?.projectId === projectId ? error.detail : undefined;
    if (!currentError) return <main role="status" className="employee-route-state">Загружаем проект…</main>;
    return <section className="employee-route-state" role="alert"><h1>Проект недоступен</h1><p>{currentError}</p><button type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Повторить</button> <Link to="/projects">Вернуться к проектам</Link><button type="button" onClick={auth.signOut}>Sign out</button></section>;
  }

  if (requestedModuleId) {
    const currentModuleState = moduleState?.projectId === projectId && moduleState.moduleId === requestedModuleId ? moduleState : undefined;
    const currentVerifiedModule = verifiedModule?.projectId === projectId && verifiedModule.moduleId === requestedModuleId ? verifiedModule.module : undefined;
    if (!currentVerifiedModule) {
      if (currentModuleState?.status === "error") return <section className="employee-route-state" role="alert"><h1>Модуль недоступен</h1><p>{currentModuleState.error}</p><button type="button" onClick={() => setModuleAttempt((attempt) => attempt + 1)}>Повторить проверку</button> <Link to={`/projects/${encodeURIComponent(projectId)}`}>К проекту</Link><button type="button" onClick={auth.signOut}>Sign out</button></section>;
      if (currentModuleState?.status === "missing") return <section className="employee-route-state" role="alert"><h1>Модуль не подключен к проекту</h1><p>Модуль {requestedModuleId} отсутствует в списке модулей, доступных этому проекту.</p><Link to={`/projects/${encodeURIComponent(projectId)}`}>К проекту</Link><button type="button" onClick={auth.signOut}>Sign out</button></section>;
      return <main role="status" className="employee-route-state">Проверяем доступность модуля…</main>;
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
      displayName: auth.employee?.display_name ?? auth.employee?.email ?? "Employee",
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
        <Route index element={<Navigate to="/projects" replace />} />
        <Route path="projects" element={<ProjectsPage />} />
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
        <Route path="admin/*" element={<AdminPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Route>
  </Routes>;
}