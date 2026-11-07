import { createContext, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { createApiClient } from "../api/client";
import type { ApiClient, Cabinet, Employee, Id, Project, SessionTokens } from "../api/types";
import { AuthLifecycle, type AuthSnapshot, type AuthStatus } from "./auth/lifecycle";
import { getSafeNextPath } from "./auth/redirect";
import { SettingsProvider } from "../preferences/SettingsProvider";

interface AuthContextValue {
  employee?: Employee;
  cabinets: Cabinet[];
  tokens?: SessionTokens;
  status: AuthStatus;
  isLoading: boolean;
  isAuthenticated: boolean;
  error?: Error;
  signIn(tokens: SessionTokens, employee?: Employee, cabinets?: Cabinet[]): void;
  signOut(): void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const ApiContext = createContext<ApiClient | undefined>(undefined);

export interface WorkContextValue {
  cabinetId?: Id;
  projectId?: Id;
  sessionId?: Id;
  project?: Project;
  setProject(project: Project): void;
}

const WorkContext = createContext<WorkContextValue | undefined>(undefined);

function browserStorage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

export function AppProviders({ children }: PropsWithChildren) {
  const location = useLocation();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project>();
  const tokenRef = useRef<SessionTokens | undefined>(undefined);
  const api = useMemo(() => createApiClient({ getAccessToken: () => tokenRef.current?.access_token }), []);
  const lifecycle = useMemo(() => new AuthLifecycle(api, browserStorage()), [api]);
  const [authSnapshot, setAuthSnapshot] = useState<AuthSnapshot>(() => lifecycle.getSnapshot());
  const pendingReturnTo = useRef<string | undefined>(undefined);

  useEffect(() => {
    const unsubscribe = lifecycle.subscribe((snapshot) => {
      tokenRef.current = snapshot.tokens;
      setAuthSnapshot(snapshot);
    });
    void lifecycle.recover();
    return unsubscribe;
  }, [lifecycle]);

  useEffect(() => {
    if (authSnapshot.status !== "authenticated") return;
    const destination = pendingReturnTo.current
      ?? (location.pathname === "/login" ? getSafeNextPath(location.search) ?? "/projects" : undefined);
    if (!destination) return;
    pendingReturnTo.current = undefined;
    navigate(destination, { replace: true });
  }, [authSnapshot.status, location.pathname, location.search, navigate]);

  const work = useMemo<WorkContextValue>(() => ({
    project,
    projectId: project?.id,
    cabinetId: project?.cabinet_id,
    setProject,
  }), [project]);

  const auth = useMemo<AuthContextValue>(() => ({
    employee: authSnapshot.employee,
    cabinets: authSnapshot.cabinets,
    tokens: authSnapshot.tokens,
    status: authSnapshot.status,
    isLoading: authSnapshot.status === "loading",
    isAuthenticated: authSnapshot.status === "authenticated",
    error: authSnapshot.error,
    signIn: (tokens, employee, cabinets) => {
      pendingReturnTo.current = getSafeNextPath(location.search);
      void lifecycle.signIn(tokens, employee, cabinets);
    },
    signOut: () => {
      pendingReturnTo.current = undefined;
      setProject(undefined);
      void lifecycle.signOut();
    },
  }), [authSnapshot, lifecycle, location.search]);

  return <SettingsProvider><ApiContext.Provider value={api}><AuthContext.Provider value={auth}><WorkContext.Provider value={work}>{children}</WorkContext.Provider></AuthContext.Provider></ApiContext.Provider></SettingsProvider>;
}

export function useApi(): ApiClient {
  const value = useContext(ApiContext);
  if (!value) throw new Error("useApi must be used inside AppProviders");
  return value;
}
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AppProviders");
  return value;
}
export function useWorkContext(): WorkContextValue {
  const value = useContext(WorkContext);
  if (!value) throw new Error("useWorkContext must be used inside AppProviders");
  return value;
}
