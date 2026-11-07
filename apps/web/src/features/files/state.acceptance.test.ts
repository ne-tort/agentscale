import { describe, expect, it } from "vitest";
import { createTranslator } from "../../i18n/catalogue";
import { WorkspaceServiceError, normalizeWorkspacePath, joinWorkspacePath, parentWorkspacePath } from "./contracts";
import { classifyWorkspaceError, sortWorkspaceEntries, workspaceErrorMessage } from "./state";

// The failure sentences come from the catalogue, so the tests ask for them the way a
// component does — through a translator for a pinned locale.
const en = createTranslator("en");
const ru = createTranslator("ru");

describe("Pod workspace file manager acceptance states", () => {
  it("normalizes only paths inside the project workspace", () => {
    expect(normalizeWorkspacePath("/docs\\guide/./readme.md")).toBe("docs/guide/readme.md");
    expect(joinWorkspacePath("docs", "new.md")).toBe("docs/new.md");
    expect(parentWorkspacePath("docs/guide/readme.md")).toBe("docs/guide");
    expect(parentWorkspacePath("readme.md")).toBe("");
    expect(() => normalizeWorkspacePath("../../outside")).toThrow();
  });

  it("keeps folders ahead of files and sorts names deterministically", () => {
    const sorted = sortWorkspaceEntries([
      { name: "z.txt", kind: "file" as const },
      { name: "10", kind: "dir" as const },
      { name: "2", kind: "dir" as const },
      { name: "a.txt", kind: "file" as const },
    ]);
    expect(sorted.map((entry) => `${entry.kind}:${entry.name}`)).toEqual(["dir:2", "dir:10", "file:a.txt", "file:z.txt"]);
  });

  it("distinguishes loading/empty/error availability causes for user-facing UI", () => {
    expect(classifyWorkspaceError(new WorkspaceServiceError({ code: "POD_NOT_RUNNING", title: "Conflict", status: 409 }))).toBe("pod-unavailable");
    expect(classifyWorkspaceError(new WorkspaceServiceError({ code: "PROJECT_PAUSED", title: "Conflict", status: 409 }))).toBe("paused");
    expect(classifyWorkspaceError(new WorkspaceServiceError({ code: "WORKSPACE_PERMISSION_DENIED", title: "Forbidden", status: 403 }))).toBe("forbidden");
    expect(classifyWorkspaceError(new WorkspaceServiceError({ code: "WORKSPACE_ALREADY_EXISTS", title: "Conflict", status: 409 }))).toBe("error");
    expect(classifyWorkspaceError(new WorkspaceServiceError({ code: "OFFLINE", title: "Offline", status: 0 }))).toBe("offline");
    expect(classifyWorkspaceError(new WorkspaceServiceError({ code: "UNSUPPORTED_MEDIA", title: "Unsupported", status: 415 }))).toBe("unsupported");
    expect(workspaceErrorMessage(new WorkspaceServiceError({ code: "POD_NOT_RUNNING", title: "Conflict", status: 409 }), en)).toContain("Pod is not running");
    expect(workspaceErrorMessage(new WorkspaceServiceError({ code: "PROJECT_PAUSED", title: "Conflict", status: 409 }), en)).toContain("paused");
  });

  it("fails closed for mutations while the Pod is unavailable", () => {
    const failure = new WorkspaceServiceError({ code: "POD_NOT_RUNNING", title: "Conflict", status: 409 });
    expect(classifyWorkspaceError(failure)).toBe("pod-unavailable");
    expect(workspaceErrorMessage(failure, en)).toBe("The Pod is not running. Start the project Pod to access its files.");
    // The same failure is worded for a Russian reader rather than leaking the code.
    expect(workspaceErrorMessage(failure, ru)).toBe("Pod не запущен. Запустите Pod проекта, чтобы открыть файлы.");
  });
});
