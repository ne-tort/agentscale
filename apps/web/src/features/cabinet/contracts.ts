import type { Cabinet, ProjectSummary } from "../../api/types";

export type CabinetOverviewSection = "cabinets" | "cabinet" | "projects" | "metrics" | "presence" | "selection" | "sidebar" | "modules";

export interface CabinetProjectSelection {
  project_id?: string | null;
  chat_session_id?: string | null;
}

export interface CabinetSidebarChat {
  session_id: string;
  project_id: string;
}

export interface CabinetChatSidebar {
  projects: Array<{ project_id: string; chats: CabinetSidebarChat[] }>;
  pinned: CabinetSidebarChat[];
}

export interface CabinetPresenceHeartbeat {
  ok?: boolean;
  employee_id?: string;
  cabinet_id?: string;
}

export interface CabinetModule {
  id: string;
  name: string;
  status?: string;
  bind_kind?: string;
}

export interface CabinetOverviewSnapshot {
  cabinets?: Cabinet[];
  cabinet?: Cabinet;
  projects?: ProjectSummary[];
  metrics?: Record<string, unknown>;
  presence?: CabinetPresenceHeartbeat;
  selection?: CabinetProjectSelection | null;
  sidebar?: CabinetChatSidebar;
  cabinetModules?: CabinetModule[];
  modules?: CabinetModule[];
  moduleTabs?: Record<string, unknown[]>;
  errors: Partial<Record<CabinetOverviewSection, Error>>;
}

export interface CabinetOverviewService {
  load(cabinetId: string, options?: { signal?: AbortSignal }): Promise<CabinetOverviewSnapshot>;
}

export interface CabinetOverviewServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export type CabinetNavigationTarget =
  | { type: "projects" }
  | { type: "project"; projectId: string }
  | { type: "chat"; projectId: string; sessionId?: string }
  | { type: "management"; projectId: string; moduleId: string; viewSlug: string; title: string }
  | { type: "data"; projectId: string; moduleId: string; viewSlug: string; title: string };

export type { Cabinet, ProjectSummary };
