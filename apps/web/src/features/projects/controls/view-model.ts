import type { ProjectControlsProject } from "./types";
import type { ProjectControlAction, ProjectControlLinkKey } from "./types";
import { createTranslator, type Translate } from "../../../i18n/catalogue";

export const PROJECT_CONTROL_ACTIONS: readonly ProjectControlAction[] = [
  "launch", "pause", "resume", "reload", "sync", "complete",
];

const actionKeys: Record<ProjectControlAction, string> = {
  launch: "projects.actionLaunch",
  pause: "projects.actionPause",
  resume: "projects.actionResume",
  reload: "projects.actionReload",
  sync: "projects.actionSync",
  complete: "projects.actionComplete",
};

/**
 * Backend states, in the words a person uses.
 *
 * Both the project status and the runtime state land here: they share most of
 * their vocabulary (`paused`, `completed`, `error`), and a screen that said
 * "suspended" next to "Приостановлен" was describing one situation twice in two
 * languages.
 */
const stateKeys: Record<string, string> = {
  draft: "projects.stateDraft",
  active: "projects.stateActive",
  paused: "projects.statePaused",
  pausing: "projects.statePausing",
  absent: "projects.stateAbsent",
  preparing: "projects.statePreparing",
  pulling: "projects.statePulling",
  hydrating: "projects.stateHydrating",
  provisioning: "projects.stateProvisioning",
  starting: "projects.stateStarting",
  running: "projects.stateRunning",
  suspended: "projects.stateSuspended",
  completed: "projects.stateCompleted",
  failed: "projects.stateFailed",
  error: "projects.stateError",
  deleted: "projects.stateDeleted",
  degraded: "projects.stateDegraded",
  unknown: "projects.stateUnknown",
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

/** Russian, so a host that does not pass a translator still gets words, not keys. */
const defaultTranslate = createTranslator("ru");

/** `"suspend"` → «На паузе»; an unfamiliar value stays visible in "Дополнительно" instead. */
export function projectStateLabel(t: Translate, state?: string | null): string | undefined {
  if (!state) return undefined;
  const key = stateKeys[state];
  return key ? t(key) : undefined;
}

export function projectControlActionLabel(t: Translate, action: ProjectControlAction): string {
  return t(actionKeys[action]);
}

export function lifecycleCapabilities(
  project: Pick<ProjectControlsProject, "status" | "observed_state" | "runtime">,
  t: Translate = defaultTranslate,
): ProjectControlCapability[] {
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
  const stateLabel = projectStateLabel(t, runtimeState)
    ?? projectStateLabel(t, project.status)
    ?? t("projects.stateUnrecognised");
  return PROJECT_CONTROL_ACTIONS.map((action) => ({
    action,
    enabled: allowed.includes(action),
    ...(!allowed.includes(action) ? { reason: t("projects.actionUnavailable", { action: projectControlActionLabel(t, action), state: stateLabel }) } : {}),
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

/** `undefined` when nothing valid is known, so the caller can say so in its own words. */
export function formatProjectUpdatedAt(value?: string | null, locale?: string): string | undefined {
  if (!value || !Number.isFinite(Date.parse(value))) return undefined;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
