import type {
  ProjectControlAction,
  ProjectControlsActionResponse,
  ProjectControlsPatch,
  ProjectControlsProject,
  ProjectControlsService,
} from "./types";

export interface ProjectMutationReadback {
  project?: ProjectControlsProject;
  readbackVerified: boolean;
  mutationAccepted: boolean;
  targetReached?: boolean;
  readbackError?: unknown;
}

function actionResultMatches(action: ProjectControlAction, project: ProjectControlsProject): boolean {
  switch (action) {
    case "launch": return project.status === "active";
    case "pause": return project.status === "paused";
    case "resume": return project.status === "active"
      || ((project.status === "paused" || project.status === "completed")
        && (project.runtime?.desired_state === "running"
          || ["suspended", "paused", "preparing", "pulling", "hydrating", "provisioning", "starting", "running"].includes(project.runtime?.observed_state ?? project.observed_state ?? "")
          || project.launch_phase === "resuming"));
    case "reload": return project.status === "active" || project.status === "error";
    case "complete": return project.status === "completed";
    case "sync": return true;
  }
}

function actionTargetReached(action: ProjectControlAction, project: ProjectControlsProject): boolean {
  switch (action) {
    case "launch": return project.status === "active";
    case "pause": return project.status === "paused";
    case "resume": return project.status === "active";
    case "reload": return project.status === "active";
    case "complete": return project.status === "completed";
    case "sync": return true;
  }
}

function routeActionMatches(action: ProjectControlAction, project: ProjectControlsProject): boolean {
  if (action === "resume" && (project.status === "paused" || project.status === "completed")) return true;
  return actionResultMatches(action, project);
}

function projectFromActionResponse(value: ProjectControlsActionResponse): ProjectControlsProject | undefined {
  if ("id" in value && typeof value.id === "string" && "name" in value && typeof value.name === "string" && "status" in value) {
    return value as ProjectControlsProject;
  }
  return undefined;
}

export async function mutateProjectWithReadback(
  mutate: () => Promise<ProjectControlsActionResponse>,
  read: () => Promise<ProjectControlsProject>,
  onMutationAccepted?: (project: ProjectControlsProject) => void,
): Promise<ProjectMutationReadback> {
  const mutationResponse = await mutate();
  const mutationProject = projectFromActionResponse(mutationResponse);
  if (mutationProject) onMutationAccepted?.(mutationProject);
  try {
    return { project: await read(), readbackVerified: true, mutationAccepted: true };
  } catch (readbackError) {
    return { ...(mutationProject ? { project: mutationProject } : {}), readbackVerified: false, mutationAccepted: true, readbackError };
  }
}

export function patchProjectWithReadback(
  service: ProjectControlsService,
  projectId: string,
  patch: ProjectControlsPatch,
  onMutationAccepted?: (project: ProjectControlsProject) => void,
): Promise<ProjectMutationReadback> {
  return mutateProjectWithReadback(
    () => service.patchProject(projectId, patch),
    async () => {
      const project = await service.getProject(projectId);
      if (project.id !== projectId) throw new Error("Повторное чтение вернуло другой проект.");
      return project;
    },
    onMutationAccepted,
  );
}

export function runProjectActionWithReadback(
  service: ProjectControlsService,
  projectId: string,
  action: ProjectControlAction,
  onMutationAccepted?: (project: ProjectControlsProject) => void,
): Promise<ProjectMutationReadback> {
  return service.runAction(projectId, action).then(async (response) => {
    const actionProject = projectFromActionResponse(response);
    if (actionProject?.id === projectId && actionResultMatches(action, actionProject)) onMutationAccepted?.(actionProject);
    let readbackProject: ProjectControlsProject | undefined;
    try {
      const project = await service.getProject(projectId);
      if (project.id !== projectId) throw new Error("Повторное чтение вернуло другой проект.");
      readbackProject = project;
      if (action === "sync") return { project, readbackVerified: true, mutationAccepted: true, targetReached: true };
      if (!actionProject || actionProject.id !== projectId || !routeActionMatches(action, actionProject)) {
        throw new Error("Ответ lifecycle route не подтвердил ожидаемый проект и статус.");
      }
      if (project.status !== actionProject.status || !actionResultMatches(action, project)) {
        throw new Error("Повторное чтение проекта не совпало со статусом lifecycle route.");
      }
      return { project, readbackVerified: true, mutationAccepted: true, targetReached: actionTargetReached(action, project) };
    } catch (readbackError) {
      return {
        ...(readbackProject ? { project: readbackProject } : actionProject?.id === projectId ? { project: actionProject } : {}),
        readbackVerified: false,
        mutationAccepted: true,
        readbackError,
      };
    }
  });
}
