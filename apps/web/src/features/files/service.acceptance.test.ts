import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createWorkspaceService,
  WorkspaceServiceError,
  workspaceRoute,
} from "./service";
import { normalizeWorkspacePath } from "./contracts";

const listing = {
  path: "docs",
  entries: [{ name: "readme.md", path: "docs/readme.md", kind: "file" as const, size: 4, modified_at: null }],
};

function responseJson(value: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

afterEach(() => vi.restoreAllMocks());

describe("Pod workspace service acceptance contract", () => {
  it("targets the projects.py workspace routes under the v1 API", async () => {
    const fetcher = vi.fn(async () => responseJson(listing));
    const service = createWorkspaceService({
      getAccessToken: () => "access-token",
      fetcher,
    });

    await service.listEntries("project/1", "/docs/./");

    expect(workspaceRoute("project/1", "entries")).toBe("/projects/project%2F1/container/workspace/entries");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project%2F1/container/workspace/entries?path=docs",
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: "Bearer access-token", Accept: "application/json" }) }),
    );
  });

  it("URL-encodes nested current-directory paths without changing their logical value", async () => {
    // Мок обязан объявить сигнатуру fetch: без параметров `mock.calls` — кортеж
    // длины 0, и проверка фактического URL не типизируется.
    const fetcher = vi.fn<typeof fetch>(async (_input: RequestInfo | URL, _init?: RequestInit) => responseJson({ path: "docs/space &+", entries: [] }));
    const service = createWorkspaceService({ fetcher });
    await service.listEntries("project-1", "docs/space &+");
    expect(String(fetcher.mock.calls[0]?.[0])).toBe(
      "/api/v1/projects/project-1/container/workspace/entries?path=docs%2Fspace%20%26%2B",
    );
  });

  it("maps list, preview, upload, delete, move, and copy to the live Pod API", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/preview")) return responseJson({ path: "notes.txt", name: "notes.txt", size: 5, truncated: false, content: "hello" });
      if (url.includes("/content") && init?.method === "PUT") return responseJson({ written: true, path: "notes.txt", size: 5 });
      if (url.endsWith("/entries?path=notes.txt")) return responseJson({ deleted: true, path: "notes.txt" });
      if (url.endsWith("/mkdir") && init?.method === "POST") return responseJson({ created: true, path: "docs/new" }, { status: 201 });
      if (url.endsWith("/move") && init?.method === "POST") return responseJson({ moved: true, src: "notes.txt", dst: "archive/notes.txt" });
      if (url.endsWith("/copy") && init?.method === "POST") return responseJson({ copied: true, src: "archive/notes.txt", dst: "notes-copy.txt" });
      return responseJson(listing);
    });
    const service = createWorkspaceService({ fetcher });

    await service.listEntries("project-1");
    await service.previewText("project-1", "notes.txt");
    await service.upload("project-1", "notes.txt", "hello");
    await service.createDirectory("project-1", "docs/new");
    await service.deleteEntry("project-1", "notes.txt");
    await service.moveEntry("project-1", "notes.txt", "archive/notes.txt");
    await service.copyEntry("project-1", "archive/notes.txt", "notes-copy.txt");

    expect(fetcher.mock.calls.map(([url, init]) => [String(url), init?.method ?? "GET"])).toEqual([
      ["/api/v1/projects/project-1/container/workspace/entries?path=", "GET"],
      ["/api/v1/projects/project-1/container/workspace/preview?path=notes.txt", "GET"],
      ["/api/v1/projects/project-1/container/workspace/content?path=notes.txt", "PUT"],
      ["/api/v1/projects/project-1/container/workspace/mkdir", "POST"],
      ["/api/v1/projects/project-1/container/workspace/entries?path=notes.txt", "DELETE"],
      ["/api/v1/projects/project-1/container/workspace/move", "POST"],
      ["/api/v1/projects/project-1/container/workspace/copy", "POST"],
    ]);
    expect(fetcher.mock.calls[2]?.[1]?.body).toBe("hello");
    expect(fetcher.mock.calls[3]?.[1]?.body).toBe(JSON.stringify({ path: "docs/new" }));
    expect(fetcher.mock.calls[3]?.[1]?.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(fetcher.mock.calls[5]?.[1]?.body).toBe(JSON.stringify({ src: "notes.txt", dst: "archive/notes.txt" }));
  });

  it("reads download headers and preserves the server filename/path contract", async () => {
    const fetcher = vi.fn(async () => new Response("binary", {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": 'attachment; filename="notes.txt"',
        "X-Workspace-Path": "notes.txt",
        "X-Workspace-Size": "6",
      },
    }));
    const service = createWorkspaceService({ fetcher });

    const result = await service.download("project-1", "./notes.txt");

    expect(result.filename).toBe("notes.txt");
    expect(result.path).toBe("notes.txt");
    expect(result.size).toBe(6);
    expect(await result.blob.text()).toBe("binary");
  });

  it("surfaces Pod availability and offline failures without choosing another storage backend", async () => {
    const podUnavailable = createWorkspaceService({
      fetcher: vi.fn(async () => responseJson({ code: "POD_NOT_RUNNING", title: "Conflict", detail: "observed_state=stopped" }, { status: 409 })),
    });
    await expect(podUnavailable.listEntries("project-1")).rejects.toMatchObject({ code: "POD_NOT_RUNNING", status: 409 });

    const offline = createWorkspaceService({ fetcher: vi.fn(async () => { throw new TypeError("network down"); }) });
    await expect(offline.listEntries("project-1")).rejects.toMatchObject({ code: "OFFLINE", status: 0 });

    const service = createWorkspaceService({ fetcher: vi.fn(async () => responseJson(listing)) });
    expect(service.createDirectory).toBeTypeOf("function");
  });

  it("rejects traversal before making a request", async () => {
    const fetcher = vi.fn(async () => responseJson(listing));
    const service = createWorkspaceService({ fetcher });

    expect(() => normalizeWorkspacePath("docs/../secrets")).toThrow("cannot escape");
    await expect(service.listEntries("project-1", "../secrets")).rejects.toThrow("cannot escape");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps the typed service error stable for binary preview failures", async () => {
    const service = createWorkspaceService({
      fetcher: vi.fn(async () => responseJson({ code: "UNSUPPORTED_MEDIA", title: "Unsupported Media Type", detail: "not a text file" }, { status: 415 })),
    });

    let error: unknown;
    try {
      await service.previewText("project-1", "blob.bin");
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(WorkspaceServiceError);
    expect(error).toMatchObject({ code: "UNSUPPORTED_MEDIA", status: 415 });
  });
});
