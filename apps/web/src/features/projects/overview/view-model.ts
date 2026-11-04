import type { ProjectLifecycle, ProjectSummary } from "../../../api/types";
import type { ProjectOverviewAction } from "./service";

export type ProjectStatusFilter = "all" | ProjectLifecycle;

export const projectStatusLabels: Record<ProjectLifecycle, string> = {
  draft: "Черновик",
  active: "Активен",
  paused: "Приостановлен",
  error: "Ошибка",
  completed: "Завершён",
  deleted: "Удалён",
};

const observedStateLabels: Record<string, string> = {
  absent: "Среда не создана",
  preparing: "Подготовка",
  pulling: "Загрузка образа",
  hydrating: "Синхронизация файлов",
  provisioning: "Создание среды",
  starting: "Запуск",
  running: "Среда запущена",
  paused: "Среда приостановлена",
  pausing: "Приостановка",
  suspended: "Среда остановлена",
  failed: "Ошибка среды",
  degraded: "Работает с ограничениями",
  unknown: "Состояние неизвестно",
};

export function observedStateLabel(value?: string | null): string | undefined {
  if (!value) return undefined;
  return observedStateLabels[value] ?? value;
}

const lifecycleCapabilities: Record<ProjectLifecycle, readonly ProjectOverviewAction[]> = {
  draft: ["launch"],
  active: ["pause", "reload", "complete"],
  paused: ["resume", "complete"],
  error: ["reload", "complete"],
  completed: ["resume"],
  deleted: [],
};

const observedCapabilities: Record<string, readonly ProjectOverviewAction[]> = {
  absent: ["launch", "complete"],
  preparing: [],
  pulling: [],
  hydrating: [],
  provisioning: [],
  starting: ["pause"],
  running: ["pause", "reload", "complete"],
  paused: ["resume", "complete"],
  pausing: [],
  suspended: ["resume", "complete"],
  failed: ["reload", "complete"],
  error: ["reload", "complete"],
  degraded: ["pause", "reload", "complete"],
  unknown: ["reload"],
};

export function projectActions(status: ProjectLifecycle, observedState?: string | null): ProjectOverviewAction[] {
  const available = lifecycleCapabilities[status];
  if (!observedState || !(observedState in observedCapabilities)) {
    return status === "paused" || status === "completed"
      ? available.filter((action) => action !== "resume")
      : [...available];
  }
  return available.filter((action) => observedCapabilities[observedState].includes(action));
}

export function lifecycleResultMatches(
  action: ProjectOverviewAction,
  project: Pick<ProjectSummary, "status" | "observed_state"> & { launch_phase?: unknown; runtime?: { desired_state?: unknown; observed_state?: unknown } | null },
): boolean {
  const runtimeState = (project.runtime?.observed_state as string | undefined) ?? project.observed_state;
  const resumeInProgress = (project.status === "paused" || project.status === "completed") && (
    project.runtime?.desired_state === "running"
    || ["paused", "suspended", "preparing", "pulling", "hydrating", "provisioning", "starting", "running"].includes(runtimeState ?? "")
    || project.launch_phase === "resuming"
  );
  switch (action) {
    case "launch": return project.status === "active";
    case "pause": return project.status === "paused";
    case "resume": return project.status === "active" || resumeInProgress;
    case "reload": return project.status === "active" || project.status === "error";
    case "complete": return project.status === "completed";
  }
}

export function lifecycleTargetReached(action: ProjectOverviewAction, status: ProjectLifecycle): boolean {
  switch (action) {
    case "launch": return status === "active";
    case "pause": return status === "paused";
    case "resume": return status === "active";
    case "reload": return status === "active";
    case "complete": return status === "completed";
  }
}

export const projectActionLabels: Record<ProjectOverviewAction, string> = {
  launch: "Запустить",
  pause: "Приостановить",
  resume: "Возобновить",
  reload: "Перезапустить",
  complete: "Завершить",
};

export function projectOverviewHref(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export function projectWorkspaceLinks(projectId: string): { runtime: string; chat: string; files: string } {
  const projectPath = projectOverviewHref(projectId);
  return {
    runtime: `${projectPath}/runtime`,
    chat: `${projectPath}/chat`,
    files: `${projectPath}/files`,
  };
}

export function projectStatusTone(status: ProjectLifecycle): "positive" | "warning" | "danger" | "muted" | "neutral" {
  if (status === "active") return "positive";
  if (status === "error") return "danger";
  if (status === "paused") return "warning";
  if (status === "completed") return "muted";
  return "neutral";
}

export function filterProjects(
  projects: readonly ProjectSummary[],
  query: string,
  status: ProjectStatusFilter,
  favoritesOnly: boolean,
  favorites: ReadonlySet<string>,
): ProjectSummary[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return projects.filter((project) => {
    if (status !== "all" && project.status !== status) return false;
    if (favoritesOnly && !favorites.has(project.id)) return false;
    if (!normalizedQuery) return true;
    return `${project.name}\n${project.about ?? ""}\n${project.id}`.toLocaleLowerCase().includes(normalizedQuery);
  });
}

export function favoriteStorageKey(employeeId?: string): string {
  return `agentscale.projects.favorites:${employeeId || "anonymous"}`;
}

export function readFavorites(storage: Pick<Storage, "getItem"> | undefined, key: string): Set<string> {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(key) ?? "[]");
    return Array.isArray(parsed) ? new Set(parsed.filter((item): item is string => typeof item === "string")) : new Set();
  } catch {
    return new Set();
  }
}

export function persistFavorites(storage: Pick<Storage, "setItem"> | undefined, key: string, favorites: ReadonlySet<string>): void {
  try {
    storage?.setItem(key, JSON.stringify([...favorites]));
  } catch {
    // The current UI state remains usable when browser storage is unavailable.
  }
}
