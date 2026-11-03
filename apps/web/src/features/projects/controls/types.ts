import type { ProjectLifecycle, ProjectSummary } from "../../../api/types";

export type ProjectControlAction = "launch" | "pause" | "resume" | "reload" | "sync" | "complete";
export type ProjectControlLinkKey = "runtime" | "chat" | "files" | "models";

export interface ProjectControlsRuntime {
  observed_state?: string | null;
  desired_state?: string | null;
  last_error?: string | null;
  runtime_ref?: string | null;
  [key: string]: unknown;
}

export type ProjectControlsProject = ProjectSummary & {
  runtime?: ProjectControlsRuntime | null;
  launch_phase?: string | null;
};

export type ProjectControlsPatch = Partial<Pick<ProjectSummary, "name" | "about" | "budget_tokens">>;
export type ProjectControlsActionResponse = ProjectControlsProject | { project_id: string; [key: string]: unknown };

export interface ProjectControlsService {
  getProject(projectId: string, options?: { signal?: AbortSignal }): Promise<ProjectControlsProject>;
  patchProject(projectId: string, patch: ProjectControlsPatch): Promise<ProjectControlsProject>;
  runAction(projectId: string, action: ProjectControlAction): Promise<ProjectControlsActionResponse>;
}

export interface ProjectControlsServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export interface ProjectControlsProps {
  projectId: string;
  service: ProjectControlsService;
  initialProject?: ProjectControlsProject;
  links?: Partial<Record<ProjectControlLinkKey, string>>;
  onProjectChange?: (project: ProjectControlsProject) => void;
}

export type ProjectControlFeedback =
  | { tone: "pending" | "success" | "warning" | "error"; message: string };

export type ProjectControlLifecycle = Extract<ProjectLifecycle, "draft" | "active" | "paused" | "error" | "completed" | "deleted">;
