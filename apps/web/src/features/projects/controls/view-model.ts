import type { ProjectControlsProject } from "./types";
import type { ProjectControlAction, ProjectControlLinkKey } from "./types";

export const PROJECT_CONTROL_ACTIONS: readonly ProjectControlAction[] = [
  "launch", "pause", "resume", "reload", "sync", "complete",
];

export const projectControlActionLabels: Record<ProjectControlAction, string> = {
  launch: "Запустить",
  pause: "Приостановить",
  resume: "Возобновить",
  reload: "Перезапустить",
  sync: "Синхронизировать workspace",
  complete: "Завершить",
};

export const projectControlStatusLabels: Record<string, string> = {
  draft: "Черновик",
  active: "Активен",
  paused: "Приостановлен",
  error: "Ошибка",
  completed: "Завершён",
  deleted: "Удалён",
};

const statusCapabilities: Record<string, readonly ProjectControlAction[]> = {
  draft: ["launch"],
  active: ["pause", "reload", "sync", "complete"],
  paused: ["resume", "sync", "complete"],
  error: ["reload", "complete"],
  completed: ["resume"],
  deleted: [],
};

const runtimeCapabilities: Record<string, readonly ProjectControlAction[]> = {
  absent: ["launch", "complete"],
  preparing: [],
  pulling: [],
  hydrating: [],
  provisioning: [],
  starting: ["pause"],
  running: ["pause", "reload", "sync", "complete"],
  paused: ["resume", "sync", "complete"],
  pausing: [],
  suspended: ["resume"],
  completed: ["resume"],
  failed: ["reload", "sync", "complete"],
  error: ["reload", "sync", "complete"],
  degraded: ["pause", "reload", "sync", "complete"],
  unknown: ["launch", "reload"],
};


export interface ProjectControlCapability {
  action: ProjectControlAction;
  enabled: boolean;
  reason?: string;
}

export function lifecycleCapabilities(project: Pick<ProjectControlsProject, "status" | "observed_state" | "runtime">): ProjectControlCapability[] {
  const runtimeState = project.runtime?.observed_state ?? project.observed_state;
  const statusAllowed = statusCapabilities[project.status] ?? [];
  const knownRuntimeActions = runtimeState ? runtimeCapabilities[runtimeState] : undefined;
  const allowed = project.status === "deleted"
    ? []
    : !knownRuntimeActions && (project.status === "paused" || project.status === "completed")
      ? statusAllowed.filter((action) => action !== "resume")
      : knownRuntimeActions
    ? statusAllowed.filter((action) => knownRuntimeActions.includes(action))
    : statusAllowed;
  const stateLabel = runtimeState || projectControlStatusLabels[project.status] || project.status;
  return PROJECT_CONTROL_ACTIONS.map((action) => ({
    action,
    enabled: allowed.includes(action),
    ...(!allowed.includes(action) ? { reason: `Действие «${projectControlActionLabels[action]}» недоступно при состоянии «${stateLabel}».` } : {}),
  }));
}

export function projectControlsLinks(projectId: string, overrides: Partial<Record<ProjectControlLinkKey, string>> = {}): Record<ProjectControlLinkKey, string> {
  const projectPath = `/projects/${encodeURIComponent(projectId)}`;
  return {
    runtime: overrides.runtime ?? `${projectPath}/runtime`,
    chat: overrides.chat ?? `${projectPath}/chat`,
    files: overrides.files ?? `${projectPath}/files`,
    models: overrides.models ?? `${projectPath}/models`,
  };
}

export function projectBudgetInputValue(budgetTokens?: number | null): string {
  return budgetTokens == null ? "" : String(budgetTokens);
}

export function formatProjectUpdatedAt(value?: string | null, locale?: string): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Не указано";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
