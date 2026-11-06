import type { Cabinet, Id, ProjectLifecycle, ProjectSummary } from "../../../api/types";

export type ProjectOverviewAction = "launch" | "pause" | "resume" | "reload" | "complete";

export interface ProjectOverviewService {
  listCabinets(signal?: AbortSignal): Promise<Cabinet[]>;
  listProjects(cabinetId: Id, signal?: AbortSignal): Promise<ProjectSummary[]>;
  getProject(projectId: Id, signal?: AbortSignal): Promise<ProjectSummary>;
  getSelection(cabinetId: Id, signal?: AbortSignal): Promise<Id | undefined>;
  createProject(cabinetId: Id, name: string): Promise<ProjectSummary>;
  selectProject(cabinetId: Id, projectId: Id | null): Promise<Id | undefined>;
  lifecycle(projectId: Id, action: ProjectOverviewAction): Promise<ProjectSummary>;
}

export interface ProjectOverviewServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetchImpl?: typeof fetch;
}

export class ProjectOverviewRequestError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly traceId?: string;

  constructor(message: string, details: { status?: number; code?: string; trace_id?: string } = {}) {
    super(message);
    this.name = "ProjectOverviewRequestError";
    this.status = details.status;
    this.code = details.code;
    this.traceId = details.trace_id;
  }
}

const lifecycleValues = new Set<ProjectLifecycle>(["draft", "active", "paused", "error", "completed"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function mapCabinet(value: unknown): Cabinet {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.name !== "string") {
    throw new ProjectOverviewRequestError("Сервер вернул неполные данные кабинета.");
  }
  return { id: value.id, name: value.name };
}

function projectForCabinet(cabinetId: Id, raw: unknown): ProjectSummary {
  const project = mapProject(raw);
  if (project.cabinet_id !== cabinetId) {
    throw new ProjectOverviewRequestError("Сервер вернул проект из другого кабинета.");
  }
  return project;
}

function uniqueProjects(projects: ProjectSummary[]): ProjectSummary[] {
  const byId = new Map<Id, ProjectSummary>();
  for (const project of projects) {
    const previous = byId.get(project.id);
    const previousTime = Date.parse(previous?.updated_at ?? "") || 0;
    const nextTime = Date.parse(project.updated_at ?? "") || 0;
    if (!previous || nextTime >= previousTime) byId.set(project.id, project);
  }
  return [...byId.values()];
}

export function mapProject(raw: unknown): ProjectSummary {
  if (!isRecord(raw) || typeof raw.id !== "string" || typeof raw.cabinet_id !== "string" || typeof raw.name !== "string") {
    throw new ProjectOverviewRequestError("Сервер вернул неполные данные проекта.");
  }
  if (typeof raw.status !== "string" || !lifecycleValues.has(raw.status as ProjectLifecycle)) {
    throw new ProjectOverviewRequestError("Сервер вернул неизвестный статус проекта.");
  }
  return {
    ...raw,
    id: raw.id,
    cabinet_id: raw.cabinet_id,
    name: raw.name,
    status: raw.status as ProjectLifecycle,
    about: typeof raw.about === "string" ? raw.about : undefined,
    selected: Boolean(raw.selected),
    new_chat_enabled: Boolean(raw.new_chat_enabled),
    observed_state: typeof raw.observed_state === "string" ? raw.observed_state : undefined,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at : undefined,
  };
}

export function createProjectOverviewService(options: ProjectOverviewServiceOptions = {}): ProjectOverviewService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetchImpl = options.fetchImpl ?? fetch;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (error) {
      throw new ProjectOverviewRequestError(error instanceof Error ? `Не удалось связаться с API: ${error.message}` : "Не удалось связаться с API.");
    }
    if (!response.ok) {
      let details: { status?: number; code?: string; title?: string; detail?: string; trace_id?: string } = {
        status: response.status,
        title: response.statusText,
      };
      try {
        const payload: unknown = await response.json();
        if (isRecord(payload)) details = {
          status: response.status,
          title: typeof payload.title === "string" ? payload.title : response.statusText,
          detail: typeof payload.detail === "string" ? payload.detail : undefined,
          code: typeof payload.code === "string" ? payload.code : undefined,
          trace_id: typeof payload.trace_id === "string" ? payload.trace_id : undefined,
        };
      } catch {
        // Keep the HTTP fallback when the server did not return JSON.
      }
      throw new ProjectOverviewRequestError(details.detail ?? details.title ?? `Запрос завершился с кодом ${response.status}.`, details);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  }

  async function listProjects(cabinetId: Id, signal?: AbortSignal): Promise<ProjectSummary[]> {
    const result = await request<{ items?: unknown[] }>(`/cabinets/${encodeURIComponent(cabinetId)}/projects`, { signal });
    if (!Array.isArray(result.items)) throw new ProjectOverviewRequestError("Сервер вернул некорректный список проектов.");
    return uniqueProjects(result.items.map((item) => projectForCabinet(cabinetId, item)));
  }

  async function getProject(projectId: Id, signal?: AbortSignal): Promise<ProjectSummary> {
    const result = mapProject(await request<unknown>(`/projects/${encodeURIComponent(projectId)}`, { signal }));
    if (result.id !== projectId) throw new ProjectOverviewRequestError("Сервер вернул другой проект.");
    return result;
  }

  async function getSelection(cabinetId: Id, signal?: AbortSignal): Promise<Id | undefined> {
    const result = await request<{ project_id?: unknown }>(`/cabinets/${encodeURIComponent(cabinetId)}/me/selection`, { signal });
    if (result.project_id === undefined || result.project_id === null) return undefined;
    if (typeof result.project_id !== "string") throw new ProjectOverviewRequestError("Сервер вернул некорректный выбранный проект.");
    return result.project_id;
  }

  return {
    async listCabinets(signal) {
      const result = await request<{ items?: unknown[] }>("/cabinets", { signal });
      if (!Array.isArray(result.items)) throw new ProjectOverviewRequestError("Сервер вернул некорректный список кабинетов.");
      return result.items.map(mapCabinet);
    },
    listProjects,
    getProject,
    getSelection,
    async createProject(cabinetId, name) {
      const normalizedName = name.trim();
      return projectForCabinet(cabinetId, await request<unknown>(`/cabinets/${encodeURIComponent(cabinetId)}/projects`, {
        method: "POST",
        body: JSON.stringify({ name: normalizedName }),
      }));
    },
    async selectProject(cabinetId, projectId) {
      await request<unknown>(`/cabinets/${encodeURIComponent(cabinetId)}/me/selection`, {
        method: "PUT",
        body: JSON.stringify({ project_id: projectId }),
      });
      const selected = await getSelection(cabinetId);
      const expected = projectId ?? undefined;
      if (selected !== expected) throw new ProjectOverviewRequestError("Повторное чтение не подтвердило выбранный проект.");
      return selected;
    },
    async lifecycle(projectId, action) {
      const project = mapProject(await request<unknown>(`/projects/${encodeURIComponent(projectId)}/${action}`, { method: "POST" }));
      if (project.id !== projectId) throw new ProjectOverviewRequestError("Сервер вернул другой проект после lifecycle-действия.");
      return project;
    },
  };
}

export const projectLifecycleLabels: Record<ProjectLifecycle, string> = {
  draft: "Черновик", active: "Активен", paused: "Приостановлен", error: "Ошибка", completed: "Завершён", deleted: "Удалён",
};
