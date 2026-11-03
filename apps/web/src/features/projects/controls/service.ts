import type {
  ProjectControlAction,
  ProjectControlsActionResponse,
  ProjectControlsPatch,
  ProjectControlsProject,
  ProjectControlsService,
  ProjectControlsServiceOptions,
} from "./types";

export interface ProjectControlsErrorDetails {
  status?: number;
  code?: string;
  title?: string;
  trace_id?: string;
}

export class ProjectControlsRequestError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly traceId?: string;

  constructor(message: string, details: ProjectControlsErrorDetails = {}) {
    super(message);
    this.name = "ProjectControlsRequestError";
    this.status = details.status;
    this.code = details.code;
    this.traceId = details.trace_id;
  }
}

const projectStatuses = new Set(["draft", "active", "paused", "error", "completed", "deleted"]);
const lifecyclePaths: Record<ProjectControlAction, string> = {
  launch: "launch",
  pause: "pause",
  resume: "resume",
  reload: "reload",
  sync: "sync",
  complete: "complete",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

export function mapProjectControlsProject(value: unknown): ProjectControlsProject {
  if (!isRecord(value)
    || typeof value.id !== "string"
    || typeof value.cabinet_id !== "string"
    || typeof value.name !== "string"
    || typeof value.status !== "string"
    || !projectStatuses.has(value.status)) {
    throw new ProjectControlsRequestError("Сервер вернул неполные или неизвестные данные проекта.", { code: "INVALID_PROJECT_RESPONSE" });
  }
  return value as unknown as ProjectControlsProject;
}

function projectForRequest(projectId: string, value: unknown): ProjectControlsProject {
  const project = mapProjectControlsProject(value);
  if (project.id !== projectId) {
    throw new ProjectControlsRequestError("Сервер вернул другой проект.", { code: "PROJECT_ID_MISMATCH" });
  }
  return project;
}

export function createProjectControlsService(options: ProjectControlsServiceOptions = {}): ProjectControlsService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (cause) {
      throw new ProjectControlsRequestError(
        cause instanceof Error ? `Не удалось связаться с API: ${cause.message}` : "Не удалось связаться с API.",
        { code: "NETWORK_ERROR", status: 0 },
      );
    }

    if (!response.ok) {
      let details: ProjectControlsErrorDetails = { status: response.status, title: response.statusText };
      try {
        const payload: unknown = await response.json();
        if (isRecord(payload)) {
          details = {
            status: response.status,
            title: typeof payload.title === "string" ? payload.title : response.statusText,
            code: typeof payload.code === "string" ? payload.code : undefined,
            trace_id: typeof payload.trace_id === "string" ? payload.trace_id : undefined,
          };
          const message = typeof payload.detail === "string" ? payload.detail : details.title ?? `API вернул HTTP ${response.status}.`;
          throw new ProjectControlsRequestError(message, details);
        }
      } catch (error) {
        if (error instanceof ProjectControlsRequestError) throw error;
      }
      throw new ProjectControlsRequestError(details.title ?? `API вернул HTTP ${response.status}.`, details);
    }

    if (response.status === 204) return undefined as T;
    try {
      return await response.json() as T;
    } catch {
      throw new ProjectControlsRequestError("Сервер вернул некорректный JSON.", { status: response.status, code: "INVALID_JSON" });
    }
  }

  function projectPath(projectId: string): string {
    return `/projects/${encode(projectId)}`;
  }

  return {
    async getProject(projectId, requestOptions) {
      const result = await request<unknown>(projectPath(projectId), { signal: requestOptions?.signal });
      return projectForRequest(projectId, result);
    },
    async patchProject(projectId, patch: ProjectControlsPatch) {
      const result = await request<unknown>(projectPath(projectId), {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      return projectForRequest(projectId, result);
    },
    async runAction(projectId, action) {
      const result = await request<unknown>(`${projectPath(projectId)}/${lifecyclePaths[action]}`, { method: "POST" });
      if (action === "sync" && isRecord(result) && result.project_id === projectId && !("id" in result)) {
        return result as unknown as ProjectControlsActionResponse;
      }
      return projectForRequest(projectId, result);
    },
  };
}

export function createDefaultProjectControlsService(getAccessToken?: () => string | undefined): ProjectControlsService {
  return createProjectControlsService({ getAccessToken });
}
