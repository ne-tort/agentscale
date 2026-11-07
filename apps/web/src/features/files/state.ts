import type { Translate } from "../../i18n/catalogue";
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

/**
 * The sentence a user reads for a failed workspace call.
 *
 * Takes the translator rather than importing copy: the same failure is rendered
 * on the screen the user is looking at, so it has to follow the active language.
 * A server-supplied `detail` is passed through as-is — it is the server's own
 * wording, not ours to rewrite.
 */
export function workspaceErrorMessage(error: unknown, t: Translate): string {
  if (error instanceof WorkspaceServiceError) {
    if (error.code === "POD_NOT_RUNNING") return t("files.errorPodNotRunning");
    if (error.code === "PROJECT_PAUSED") return t("files.errorProjectPaused");
    if (error.code === "OFFLINE") return t("files.errorOffline");
    if (error.code === "UNSUPPORTED_MEDIA") return t("files.errorBinaryPreview");
    if (error.detail) return error.detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return t("files.errorGeneric");
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
