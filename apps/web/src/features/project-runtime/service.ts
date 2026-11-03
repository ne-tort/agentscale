import type {
  ChatSidebarResponse,
  ProjectAttachment,
  ProjectContainer,
  ProjectKey,
  ProjectLiveModel,
  ProjectMetricsResponse,
  ProjectModelsResponse,
  ProjectPatch,
  ProjectRuntimeProject,
  ProjectRuntimeAgentSession,
  ProjectRuntimeService,
  ProjectRuntimeSnapshot,
  RuntimeAction,
  WorkspaceEntriesResponse,
  WorkspaceDownload,
  WorkspacePreviewResponse,
} from "./types";

export interface ProjectRuntimeServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetchImpl?: typeof fetch;
}

export interface ProjectRuntimeApiErrorShape {
  code?: string;
  title?: string;
  detail?: string;
  status?: number;
  trace_id?: string;
}

export class ProjectRuntimeRequestError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly traceId?: string;
  readonly retryable: boolean;

  constructor(message: string, details: ProjectRuntimeApiErrorShape = {}) {
    super(message);
    this.name = "ProjectRuntimeRequestError";
    this.status = details.status;
    this.code = details.code;
    this.traceId = details.trace_id;
    this.retryable = details.status === undefined || details.status >= 500 || details.status === 408 || details.status === 429;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asProject(value: unknown, expectedId?: string, expectedCabinetId?: string): ProjectRuntimeProject {
  if (!isRecord(value)
    || typeof value.id !== "string"
    || typeof value.name !== "string"
    || typeof value.status !== "string"
    || typeof value.cabinet_id !== "string") {
    throw new ProjectRuntimeRequestError("Сервер вернул неполные данные проекта.");
  }
  if (expectedId !== undefined && value.id !== expectedId) {
    throw new ProjectRuntimeRequestError("Сервер вернул данные другого проекта.", { code: "PROJECT_ID_MISMATCH" });
  }
  if (expectedCabinetId !== undefined && value.cabinet_id !== expectedCabinetId) {
    throw new ProjectRuntimeRequestError("Созданный проект не принадлежит указанному кабинету.", { code: "PROJECT_CABINET_MISMATCH" });
  }
  return value as unknown as ProjectRuntimeProject;
}

function asContainer(value: unknown, expectedProjectId: string): ProjectContainer {
  if (!isRecord(value) || value.project_id !== expectedProjectId) {
    throw new ProjectRuntimeRequestError("Сервер вернул состояние контейнера другого проекта.", { code: "PROJECT_ID_MISMATCH" });
  }
  return value as unknown as ProjectContainer;
}

function asMetrics(value: unknown, expectedProjectId: string): ProjectMetricsResponse {
  if (!isRecord(value) || value.project_id !== expectedProjectId) {
    throw new ProjectRuntimeRequestError("Сервер вернул метрики другого проекта.", { code: "PROJECT_ID_MISMATCH" });
  }
  return value as unknown as ProjectMetricsResponse;
}

function asItems<T>(value: unknown, resource: string): T[] {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new ProjectRuntimeRequestError(`Сервер вернул некорректный список ${resource}.`);
  }
  return value.items as T[];
}

function asModels(value: unknown): ProjectModelsResponse {
  if (!isRecord(value) || !Array.isArray(value.models)) {
    throw new ProjectRuntimeRequestError("Сервер вернул некорректный список моделей.");
  }
  return value as unknown as ProjectModelsResponse;
}

function isWorkspaceRunning(project: ProjectRuntimeProject, container: ProjectContainer): boolean {
  return project.status === "active" && (container.observed_state === "running"
    || container.runtime?.observed_state === "running"
    || project.runtime?.observed_state === "running");
}

function requestError(response: Response, body: unknown): ProjectRuntimeRequestError {
  const problem = isRecord(body) ? body : {};
  return new ProjectRuntimeRequestError(
    typeof problem.detail === "string"
      ? problem.detail
      : typeof problem.title === "string"
        ? problem.title
        : response.statusText || `Запрос завершился с кодом ${response.status}.`,
    {
      status: response.status,
      title: typeof problem.title === "string" ? problem.title : response.statusText,
      code: typeof problem.code === "string" ? problem.code : undefined,
      trace_id: typeof problem.trace_id === "string" ? problem.trace_id : undefined,
    },
  );
}

async function readErrorBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function attachDownloadHeaders(blob: Blob, response: Response): WorkspaceDownload {
  Object.defineProperties(blob, {
    contentDisposition: { value: response.headers.get("Content-Disposition"), enumerable: true },
    workspacePath: { value: response.headers.get("X-Workspace-Path"), enumerable: true },
    workspaceSize: { value: response.headers.get("X-Workspace-Size"), enumerable: true },
  });
  return blob as WorkspaceDownload;
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

export function createProjectRuntimeService(options: ProjectRuntimeServiceOptions = {}): ProjectRuntimeService & {
  createProject(cabinetId: string, name: string, agentProvider?: string | null): Promise<ProjectRuntimeProject>;
  createModelSession(projectId: string, modelId: string): Promise<ProjectRuntimeAgentSession>;
} {
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
          ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (error) {
      throw new ProjectRuntimeRequestError(
        error instanceof Error ? `Не удалось связаться с API: ${error.message}` : "Не удалось связаться с API.",
      );
    }

    if (!response.ok) {
      throw requestError(response, await readErrorBody(response));
    }

    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new ProjectRuntimeRequestError("Сервер вернул некорректный JSON-ответ.", {
        status: response.status,
        code: "INVALID_API_RESPONSE",
      });
    }
  }

  function projectPath(projectId: string, suffix = ""): string {
    return `/projects/${encode(projectId)}${suffix}`;
  }

  async function getProject(projectId: string, requestOptions?: { signal?: AbortSignal }): Promise<ProjectRuntimeProject> {
    return asProject(await request<unknown>(projectPath(projectId), { signal: requestOptions?.signal }), projectId);
  }

  async function getContainer(projectId: string, requestOptions?: { signal?: AbortSignal }): Promise<ProjectContainer> {
    return asContainer(await request<unknown>(projectPath(projectId, "/container"), { signal: requestOptions?.signal }), projectId);
  }

  async function getMetrics(projectId: string, requestOptions?: { signal?: AbortSignal }): Promise<ProjectMetricsResponse> {
    const params = new URLSearchParams({ window: "1h" });
    return asMetrics(await request<unknown>(projectPath(projectId, `/container/metrics?${params}`), { signal: requestOptions?.signal }), projectId);
  }

  async function listWorkspace(projectId: string, path = "", requestOptions?: { signal?: AbortSignal }): Promise<WorkspaceEntriesResponse> {
    const params = new URLSearchParams({ path });
    const result: unknown = await request<unknown>(projectPath(projectId, `/container/workspace/entries?${params}`), { signal: requestOptions?.signal });
    if (!isRecord(result) || typeof result.path !== "string" || !Array.isArray(result.entries)) {
      throw new ProjectRuntimeRequestError("Сервер вернул некорректное содержимое workspace.");
    }
    return result as unknown as WorkspaceEntriesResponse;
  }

  async function previewWorkspace(projectId: string, path: string, requestOptions?: { signal?: AbortSignal }): Promise<WorkspacePreviewResponse> {
    const params = new URLSearchParams({ path });
    const result: unknown = await request<unknown>(projectPath(projectId, `/container/workspace/preview?${params}`), { signal: requestOptions?.signal });
    if (!isRecord(result) || typeof result.path !== "string" || typeof result.name !== "string" || typeof result.content !== "string") {
      throw new ProjectRuntimeRequestError("Сервер вернул некорректный preview workspace файла.");
    }
    return result as unknown as WorkspacePreviewResponse;
  }

  async function downloadWorkspace(projectId: string, path: string, requestOptions?: { signal?: AbortSignal }): Promise<WorkspaceDownload> {
    const token = options.getAccessToken?.();
    const params = new URLSearchParams({ path });
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}${projectPath(projectId, `/container/workspace/content?${params}`)}`, {
        signal: requestOptions?.signal,
        headers: { Accept: "application/octet-stream", ...(token ? { Authorization: *** ${token}` } : {}) },
      });
    } catch (error) {
      throw new ProjectRuntimeRequestError(error instanceof Error ? `Не удалось скачать файл: ${error.message}` : "Не удалось скачать файл.");
    }
    if (!response.ok) throw requestError(response, await readErrorBody(response));
    return attachDownloadHeaders(await response.blob(), response);
  }

  async function load(projectId: string, requestOptions?: { signal?: AbortSignal }): Promise<ProjectRuntimeSnapshot> {
    const project = await getProject(projectId, requestOptions);
    if (typeof project.cabinet_id !== "string" || !project.cabinet_id) {
      throw new ProjectRuntimeRequestError("Сервер не вернул cabinet_id проекта.");
    }
    const [container, metrics, attachmentsBody, keysBody, chats] = await Promise.all([
      getContainer(projectId, requestOptions),
      getMetrics(projectId, requestOptions),
      request<unknown>(projectPath(projectId, "/attachments"), { signal: requestOptions?.signal }),
      request<unknown>(projectPath(projectId, "/ai-keys/available"), { signal: requestOptions?.signal }),
      request<unknown>(`/cabinets/${encode(project.cabinet_id)}/chats/sidebar`, { signal: requestOptions?.signal }),
    ]);
    if (!isRecord(chats) || chats.cabinet_id !== project.cabinet_id) {
      throw new ProjectRuntimeRequestError("Сервер вернул список чатов другого кабинета.", { code: "PROJECT_CABINET_MISMATCH" });
    }
    const files = isWorkspaceRunning(project, container)
      ? await listWorkspace(projectId, "", requestOptions)
      : null;
    const models = project.resolved_ai_key_id && !isWorkspaceRunning(project, container)
      ? { models: [], default_model: null, source: "not_running" }
      : asModels(await request<unknown>(projectPath(projectId, "/models/live"), { signal: requestOptions?.signal }));
    return {
      project,
      container,
      metrics,
      files,
      attachments: asItems<ProjectAttachment>(attachmentsBody, "вложений"),
      keys: asItems<ProjectKey>(keysBody, "AI keys"),
      models,
      chats: chats as unknown as ChatSidebarResponse,
    };
  }

  async function createProject(cabinetId: string, name: string, agentProvider?: string | null): Promise<ProjectRuntimeProject> {
    const body = {
      name,
      ...(agentProvider === undefined ? {} : { agent_provider: agentProvider }),
    };
    return asProject(await request<unknown>(`/cabinets/${encode(cabinetId)}/projects`, {
      method: "POST",
      body: JSON.stringify(body),
    }), undefined, cabinetId);
  }

  async function patchProject(projectId: string, patch: ProjectPatch): Promise<ProjectRuntimeProject> {
    return asProject(await request<unknown>(projectPath(projectId), { method: "PATCH", body: JSON.stringify(patch) }), projectId);
  }

  async function createModelSession(projectId: string, modelId: string): Promise<ProjectRuntimeAgentSession> {
    const result: unknown = await request<unknown>(projectPath(projectId, "/agent/sessions"), {
      method: "POST",
      body: JSON.stringify({ model: modelId }),
    });
    if (!isRecord(result) || typeof result.id !== "string") {
      throw new ProjectRuntimeRequestError("Сервер вернул некорректную agent session.", { code: "INVALID_API_RESPONSE" });
    }
    if (result.project_id !== projectId) {
      throw new ProjectRuntimeRequestError("Сервер вернул agent session для другого проекта.", { code: "PROJECT_ID_MISMATCH" });
    }
    return result as unknown as ProjectRuntimeAgentSession;
  }

  async function runAction(projectId: string, action: RuntimeAction): Promise<ProjectRuntimeProject | Record<string, unknown>> {
    const endpoint: Partial<Record<RuntimeAction, string>> = {
      launch: "/launch",
      pause: "/pause",
      resume: "/resume",
      reload: "/reload",
      sync: "/sync",
      complete: "/complete",
    };
    const suffix = endpoint[action];
    if (!suffix) throw new ProjectRuntimeRequestError(`Действие проекта не поддерживается: ${String(action)}.`, { code: "UNSUPPORTED_PROJECT_ACTION" });
    const result = await request<unknown>(projectPath(projectId, suffix), { method: "POST" });
    if (!isRecord(result)) throw new ProjectRuntimeRequestError("Сервер вернул некорректный результат действия проекта.");
    const resultId = typeof result.id === "string" ? result.id : result.project_id;
    if (typeof resultId !== "string") {
      throw new ProjectRuntimeRequestError("Сервер не подтвердил, к какому проекту относится результат действия.", { code: "INVALID_API_RESPONSE" });
    }
    if (resultId !== projectId || (typeof result.id === "string" && result.id !== projectId) || (typeof result.project_id === "string" && result.project_id !== projectId)) {
      throw new ProjectRuntimeRequestError("Сервер вернул результат действия для другого проекта.", { code: "PROJECT_ID_MISMATCH" });
    }
    return result as ProjectRuntimeProject | Record<string, unknown>;
  }

  return {
    load,
    getProject,
    getContainer,
    getMetrics,
    listWorkspace,
    previewWorkspace,
    downloadWorkspace,
    listAttachments: async (projectId, requestOptions) => asItems<ProjectAttachment>(await request<unknown>(projectPath(projectId, "/attachments"), { signal: requestOptions?.signal }), "вложений"),
    listKeys: async (projectId, requestOptions) => asItems<ProjectKey>(await request<unknown>(projectPath(projectId, "/ai-keys/available"), { signal: requestOptions?.signal }), "AI keys"),
    listModels: async (projectId, requestOptions) => asModels(await request<unknown>(projectPath(projectId, "/models/live"), { signal: requestOptions?.signal })),
    listChats: async (cabinetId, requestOptions) => {
      const result: unknown = await request<unknown>(`/cabinets/${encode(cabinetId)}/chats/sidebar`, { signal: requestOptions?.signal });
      if (!isRecord(result) || result.cabinet_id !== cabinetId) throw new ProjectRuntimeRequestError("Сервер вернул список чатов другого кабинета.", { code: "PROJECT_CABINET_MISMATCH" });
      return result as unknown as ChatSidebarResponse;
    },
    createProject,
    createModelSession,
    patchProject,
    runAction,
  };
}

export function createDefaultProjectRuntimeService(): ReturnType<typeof createProjectRuntimeService> {
  return createProjectRuntimeService();
}

export type { ProjectLiveModel };
