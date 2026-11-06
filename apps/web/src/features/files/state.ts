import { WorkspaceServiceError, type WorkspaceFailureKind } from "./contracts";

export type FilesAvailability = "loading" | "ready" | "empty" | "error" | "offline" | "paused" | "pod-unavailable";

export function classifyWorkspaceError(error: unknown): WorkspaceFailureKind {
  if (!(error instanceof WorkspaceServiceError)) return "error";
  if (error.code === "OFFLINE" || error.status === 0) return "offline";
  if (error.code === "PROJECT_PAUSED") return "paused";
  if (error.code === "POD_NOT_RUNNING") return "pod-unavailable";
  if (error.code === "FORBIDDEN" || error.status === 401 || error.status === 403) return "forbidden";
  if (error.code === "WORKSPACE_NOT_FOUND" || error.status === 404) return "not-found";
  if (error.code === "UNSUPPORTED_MEDIA" || error.status === 415) return "unsupported";
  return "error";
}

export function workspaceErrorMessage(error: unknown): string {
  if (error instanceof WorkspaceServiceError) {
    if (error.code === "POD_NOT_RUNNING") return "The Pod is not running. Start the project Pod to access its files.";
    if (error.code === "PROJECT_PAUSED") return "This project is paused. Resume the project to access its files.";
    if (error.code === "OFFLINE") return "The workspace service is offline. Check your connection and try again.";
    if (error.code === "UNSUPPORTED_MEDIA") return "This file is binary and cannot be previewed as text.";
    if (error.detail) return error.detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return "The workspace request failed. Try again.";
}

export function sortWorkspaceEntries<T extends { kind: "file" | "dir"; name: string }>(entries: readonly T[]): T[] {
  return [...entries].sort((left, right) => {
    if (left.kind !== right.kind) return left.kind === "dir" ? -1 : 1;
    return left.name.localeCompare(right.name, undefined, { sensitivity: "base", numeric: true });
  });
}

export function isWorkspaceWriteFailure(error: unknown): boolean {
  const kind = classifyWorkspaceError(error);
  return kind === "offline" || kind === "paused" || kind === "pod-unavailable" || kind === "forbidden";
}
