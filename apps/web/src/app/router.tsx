import { Navigate, Route, Routes, Link, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./context";
import { LoginPage, ProjectsPage, ProjectPage, ChatPage } from "../routes/pages";

function RequireAuth() {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  return isAuthenticated ? <Outlet /> : <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
}

function Outlet() {
  return <Routes><Route path="/projects" element={<ProjectsPage />} /><Route path="/projects/:projectId" element={<ProjectPage />} /><Route path="/projects/:projectId/chat/:sessionId" element={<ChatPage />} /><Route path="/projects/:projectId/chat" element={<ChatPage />} /></Routes>;
}

function AppShell() {
  const { isAuthenticated, employee, signOut } = useAuth();
  const params = useParams();
  return <div className="app-shell">
    <header className="topbar"><Link className="brand" to="/projects">agentscale</Link><span className="crumb">{params.projectId ? `Project ${params.projectId}` : "Workspace"}</span>{isAuthenticated && <div className="account"><span>{employee?.display_name ?? "Signed in"}</span><button onClick={signOut}>Sign out</button></div>}</header>
    <main className="shell-content"><Outlet /></main>
  </div>;
}

export function AppRouter() {
  return <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<RequireAuth />}><Route path="*" element={<AppShell />} /></Route>
    <Route path="*" element={<Navigate to="/projects" replace />} />
  </Routes>;
}
