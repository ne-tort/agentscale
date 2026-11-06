export type WorkspaceEntryKind = "file" | "dir";

export interface WorkspaceEntry {
  name: string;
  path: string;
  kind: WorkspaceEntryKind;
  size: number | null;
  modified_at: string | null;
}

export interface WorkspaceListing {
  path: string;
  entries: WorkspaceEntry[];
}

export interface WorkspaceTextPreview {
  path: string;
  name: string;
  size: number | null;
  truncated: boolean;
  content: string;
}

export interface WorkspaceWriteResult {
  written: boolean;
  path: string;
  size: number;
}

export interface WorkspaceMutationResult {
  created?: boolean;
  deleted?: boolean;
  moved?: boolean;
  copied?: boolean;
  path?: string;
  src?: string;
  dst?: string;
}

export interface WorkspaceDownload {
  blob: Blob;
  filename: string;
  path: string;
  size: number;
}

export type WorkspaceUpload = Blob | string;

export type WorkspaceFailureKind =
  | "offline"
  | "pod-unavailable"
  | "paused"
  | "forbidden"
  | "not-found"
  | "unsupported"
  | "error";

export interface WorkspaceErrorInit {
  code: string;
  title: string;
  status: number;
  detail?: string;
  cause?: unknown;
}

export class WorkspaceServiceError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail?: string;

  constructor(init: WorkspaceErrorInit) {
    super(init.detail ?? init.title);
    this.name = "WorkspaceServiceError";
    this.code = init.code;
    this.status = init.status;
    this.detail = init.detail;
    if (init.cause !== undefined) this.cause = init.cause;
  }
}

export interface WorkspaceService {
  listEntries(projectId: string, path?: string, options?: { signal?: AbortSignal }): Promise<WorkspaceListing>;
  previewText(projectId: string, path: string, options?: { signal?: AbortSignal }): Promise<WorkspaceTextPreview>;
  download(projectId: string, path: string, options?: { signal?: AbortSignal }): Promise<WorkspaceDownload>;
  upload(projectId: string, path: string, body: WorkspaceUpload, options?: { signal?: AbortSignal }): Promise<WorkspaceWriteResult>;
  createDirectory(projectId: string, path: string, options?: { signal?: AbortSignal }): Promise<WorkspaceMutationResult>;
  deleteEntry(projectId: string, path: string, options?: { signal?: AbortSignal }): Promise<WorkspaceMutationResult>;
  moveEntry(projectId: string, src: string, dst: string, options?: { signal?: AbortSignal }): Promise<WorkspaceMutationResult>;
  copyEntry(projectId: string, src: string, dst: string, options?: { signal?: AbortSignal }): Promise<WorkspaceMutationResult>;
}

export interface WorkspaceServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export const WORKSPACE_ROUTE_SUFFIX = "/container/workspace" as const;

export function normalizeWorkspacePath(path: string): string {
  const normalized = path.replaceAll("\\", "/");
  const parts: string[] = [];
  for (const part of normalized.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") throw new Error("Workspace paths cannot escape the project workspace");
    parts.push(part);
  }
  return parts.join("/");
}

export function joinWorkspacePath(parent: string, name: string): string {
  const child = normalizeWorkspacePath(name);
  if (!child) throw new Error("A workspace name is required");
  return normalizeWorkspacePath(parent ? `${parent}/${child}` : child);
}

export function parentWorkspacePath(path: string): string {
  const normalized = normalizeWorkspacePath(path);
  const separator = normalized.lastIndexOf("/");
  return separator < 0 ? "" : normalized.slice(0, separator);
}

export function workspaceEntryLabel(entry: WorkspaceEntry): string {
  return entry.kind === "dir" ? `${entry.name}/` : entry.name;
}
