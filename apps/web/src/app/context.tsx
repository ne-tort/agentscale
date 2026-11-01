import { createContext, useContext, useMemo, useState, type PropsWithChildren } from "react";
import { createApiClient } from "../api/client";
import type { ApiClient, Cabinet, Employee, Id, Project, SessionTokens } from "../api/types";

interface AuthContextValue {
  employee?: Employee;
  cabinets: Cabinet[];
  tokens?: SessionTokens;
  isAuthenticated: boolean;
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

export function AppProviders({ children }: PropsWithChildren) {
  const [tokens, setTokens] = useState<SessionTokens>();
  const [employee, setEmployee] = useState<Employee>();
  const [cabinets, setCabinets] = useState<Cabinet[]>([]);
  const [project, setProject] = useState<Project>();
  const api = useMemo(() => createApiClient({ getAccessToken: () => tokens?.access_token }), [tokens]);
  const work = useMemo<WorkContextValue>(() => ({ project, projectId: project?.id, cabinetId: project?.cabinet_id, setProject }), [project]);
  const auth = useMemo<AuthContextValue>(() => ({
    employee, cabinets, tokens, isAuthenticated: Boolean(tokens?.access_token),
    signIn: (nextTokens, nextEmployee, nextCabinets) => { setTokens(nextTokens); setEmployee(nextEmployee); setCabinets(nextCabinets ?? []); },
    signOut: () => { setTokens(undefined); setEmployee(undefined); setCabinets([]); setProject(undefined); },
  }), [cabinets, employee, project, tokens]);
  return <ApiContext.Provider value={api}><AuthContext.Provider value={auth}><WorkContext.Provider value={work}>{children}</WorkContext.Provider></AuthContext.Provider></ApiContext.Provider>;
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
