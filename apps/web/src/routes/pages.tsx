import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApi, useAuth } from "../app/context";
import type { Project } from "../api/types";

function Placeholder({ eyebrow, title, detail, action }: { eyebrow: string; title: string; detail: string; action?: ReactNode }) {
  return <section className="placeholder"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="muted">{detail}</p>{action}</section>;
}

export function LoginPage() {
  const api = useApi(); const auth = useAuth(); const navigate = useNavigate();
  const [username, setUsername] = useState(""); const [password, setPassword] = useState(""); const [error, setError] = useState<string>(); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(undefined); try { const tokens = await api.login(username, password); auth.signIn(tokens); navigate("/projects"); } catch (err) { setError(err instanceof Error ? err.message : "Unable to sign in"); } finally { setBusy(false); } }
  return <main className="login-page"><div className="login-panel"><p className="eyebrow">Agent workspace platform</p><h1>Sign in to agentscale</h1><p className="muted">Access your projects, pods, files, and agent sessions.</p><form onSubmit={submit} className="stack"><label>Email or username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required /></label><label>Password<input value={password} onChange={(e) => setPassword(e.target.value)} type="password" autoComplete="current-password" required /></label>{error && <p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button></form></div></main>;
}

export function ProjectsPage() {
  const { projectId } = useParams();
  return <section><div className="page-heading"><div><p className="eyebrow">Workspace</p><h1>Projects</h1></div><button className="primary" disabled title="Project creation will be connected to the API">New project</button></div><div className="empty-state"><strong>No projects loaded yet</strong><span>The project list boundary is ready for API integration.</span><Link to="/projects/example">Open placeholder project</Link></div></section>;
}

export function ProjectPage() {
  const { projectId } = useParams();
  return <section><div className="page-heading"><div><p className="eyebrow">Project</p><h1>{projectId}</h1></div><span className="status">Lifecycle pending</span></div><div className="workspace-grid"><Placeholder eyebrow="Runtime" title="Project workspace" detail="Pod lifecycle, files, modules, and settings will attach to this URL-stable surface." action={<Link className="primary-link" to={`/projects/${projectId}/chat`}>Open chat</Link>} /><Placeholder eyebrow="Agent" title="Session-ready" detail="A project is the durable boundary for agent work. Chat sessions are explicit child routes." /></div></section>;
}

export function ChatPage() {
  const { projectId, sessionId } = useParams();
  return <section className="chat-placeholder"><div className="page-heading"><div><p className="eyebrow">Project / Chat</p><h1>{sessionId ? `Session ${sessionId}` : "New conversation"}</h1></div><span className="status">Transcript pending</span></div><div className="chat-surface"><p className="muted">Chat transcript, append-only stream events, tools, and approvals will render here.</p><div className="composer"><textarea placeholder="Message your project agent..." disabled /><button className="primary" disabled>Send</button></div></div><Link to={`/projects/${projectId}`}>Back to project</Link></section>;
}

type ProjectCardProps = { project: Project };
export function ProjectCard({ project }: ProjectCardProps) { return <Link className="project-card" to={`/projects/${project.id}`}><strong>{project.name}</strong><span>{project.status}</span></Link>; }
