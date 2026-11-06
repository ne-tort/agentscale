import {
  normalizeWorkspacePath,
  WorkspaceServiceError,
  WORKSPACE_ROUTE_SUFFIX,
  type WorkspaceDownload,
  type WorkspaceListing,
  type WorkspaceMutationResult,
  type WorkspaceService,
  type WorkspaceServiceOptions,
  type WorkspaceTextPreview,
  type WorkspaceUpload,
  type WorkspaceWriteResult,
} from "./contracts";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function filenameFromContentDisposition(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const utf8 = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (utf8) {
    try {
      return decodeURIComponent(utf8.replace(/^"|"$/g, ""));
    } catch {
      return fallback;
    }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

function isAbortError(value: unknown): boolean {
  return isRecord(value) && value.name === "AbortError";
}

function transportError(cause: unknown): never {
  if (isAbortError(cause)) throw cause;
  throw new WorkspaceServiceError({
    code: "OFFLINE",
    title: "Workspace is offline",
    status: 0,
    detail: "The workspace service could not be reached.",
    cause,
  });
}

function errorFromPayload(response: Response, payload: unknown): WorkspaceServiceError {
  const data = isRecord(payload) ? payload : {};
  const code = stringValue(data.code) ?? `HTTP_${response.status}`;
  const title = stringValue(data.title) ?? (response.statusText || "Workspace request failed");
  const detail = stringValue(data.detail) ?? stringValue(data.message);
  return new WorkspaceServiceError({ code, title, status: response.status, detail });
}

export function workspaceRoute(projectId: string, operation: "entries" | "content" | "preview" | "move" | "copy" | "mkdir"): string {
  const project = encodeURIComponent(projectId);
  return `/projects/${project}${WORKSPACE_ROUTE_SUFFIX}/${operation}`;
}

async function readProblemPayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function requestInitWithAuth(options: WorkspaceServiceOptions, init: RequestInit = {}): RequestInit {
  const token = options.getAccessToken?.();
  return {
    ...init,
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  };
}

export function createWorkspaceService(options: WorkspaceServiceOptions = {}): WorkspaceService {
  const baseUrl = options.baseUrl ?? "/api/v1";
  const fetcher = options.fetcher ?? fetch;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, requestInitWithAuth(options, init));
    } catch (cause) {
      transportError(cause);
    }
    if (!response.ok) throw errorFromPayload(response, await readProblemPayload(response));
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  async function binaryRequest(path: string, init: RequestInit = {}): Promise<Response> {
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, requestInitWithAuth(options, init));
    } catch (cause) {
      transportError(cause);
    }
    if (!response.ok) throw errorFromPayload(response, await readProblemPayload(response));
    return response;
  }

  function pathQuery(path: string): string {
    return `?path=${encodeURIComponent(normalizeWorkspacePath(path))}`;
  }

  return {
    listEntries: async (projectId, path = "", requestOptions) =>
      request<WorkspaceListing>(`${workspaceRoute(projectId, "entries")}${pathQuery(path)}`, { signal: requestOptions?.signal }),
    previewText: async (projectId, path, requestOptions) =>
      request<WorkspaceTextPreview>(`${workspaceRoute(projectId, "preview")}${pathQuery(path)}`, { signal: requestOptions?.signal }),
    download: async (projectId, path, requestOptions): Promise<WorkspaceDownload> => {
      const normalizedPath = normalizeWorkspacePath(path);
      const response = await binaryRequest(`${workspaceRoute(projectId, "content")}${pathQuery(normalizedPath)}`, { signal: requestOptions?.signal });
      const blob = await response.blob();
      return {
        blob,
        filename: filenameFromContentDisposition(response.headers.get("Content-Disposition"), normalizedPath.split("/").pop() ?? "download"),
        path: response.headers.get("X-Workspace-Path") ?? normalizedPath,
        size: Number(response.headers.get("X-Workspace-Size") ?? blob.size),
      };
    },
    upload: async (projectId, path, body, requestOptions): Promise<WorkspaceWriteResult> =>
      request<WorkspaceWriteResult>(`${workspaceRoute(projectId, "content")}${pathQuery(path)}`, {
        method: "PUT",
        body,
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    createDirectory: async (projectId, path, requestOptions): Promise<WorkspaceMutationResult> =>
      request<WorkspaceMutationResult>(workspaceRoute(projectId, "mkdir"), {
        method: "POST",
        body: JSON.stringify({ path: normalizeWorkspacePath(path) }),
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/json" },
      }),
    deleteEntry: async (projectId, path, requestOptions): Promise<WorkspaceMutationResult> =>
      request<WorkspaceMutationResult>(`${workspaceRoute(projectId, "entries")}${pathQuery(path)}`, {
        method: "DELETE",
        signal: requestOptions?.signal,
      }),
    moveEntry: async (projectId, src, dst, requestOptions): Promise<WorkspaceMutationResult> =>
      request<WorkspaceMutationResult>(workspaceRoute(projectId, "move"), {
        method: "POST",
        body: JSON.stringify({ src: normalizeWorkspacePath(src), dst: normalizeWorkspacePath(dst) }),
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/json" },
      }),
    copyEntry: async (projectId, src, dst, requestOptions): Promise<WorkspaceMutationResult> =>
      request<WorkspaceMutationResult>(workspaceRoute(projectId, "copy"), {
        method: "POST",
        body: JSON.stringify({ src: normalizeWorkspacePath(src), dst: normalizeWorkspacePath(dst) }),
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/json" },
      }),
  };
}

export { WorkspaceServiceError } from "./contracts";
export type {
  WorkspaceDownload,
  WorkspaceListing,
  WorkspaceMutationResult,
  WorkspaceService,
  WorkspaceServiceOptions,
  WorkspaceTextPreview,
  WorkspaceUpload,
  WorkspaceWriteResult,
};
