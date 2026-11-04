import { describe, expect, it, vi } from "vitest";
import { createProjectRuntimeService, ProjectRuntimeRequestError } from "./service";
import type { ProjectRuntimeSnapshot } from "./types";

function jsonResponse(body: unknown, status = 200, statusText?: string): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { "Content-Type": "application/problem+json" },
  });
}

const project = {
  id: "project-42",
  cabinet_id: "cabinet/9",
  name: "Runtime Lab",
  status: "active",
  agent_provider: "codex",
  resolved_ai_key_id: "key-7",
};

const runningContainer = {
  project_id: "project-42",
  status: "running",
  observed_state: "running",
  runtime: { observed_state: "running" },
};

function snapshotFetch(input: RequestInfo | URL): Response {
  const url = String(input);
  if (url === "/api/v1/projects/project-42") return jsonResponse(project);
  if (url === "/api/v1/projects/project-42/container") return jsonResponse(runningContainer);
  if (url === "/api/v1/projects/project-42/container/metrics?window=1h") {
    return jsonResponse({ project_id: "project-42", latest: null, series: [], window: "1h", stale: true });
  }
  if (url === "/api/v1/projects/project-42/container/workspace/entries?path=") {
    return jsonResponse({ path: "", entries: [] });
  }
  if (url === "/api/v1/projects/project-42/attachments") return jsonResponse({ items: [] });
  if (url === "/api/v1/projects/project-42/ai-keys/available") {
    return jsonResponse({ items: [{ id: "key-7", name: "Codex key", provider: "codex", api_kind: "openai_api", status: "active", has_secret: true }] });
  }
  if (url === "/api/v1/projects/project-42/models/live") {
    return jsonResponse({ models: [{ id: "gpt-5" }], default_model: "gpt-5", source: "live" });
  }
  if (url === "/api/v1/cabinets/cabinet%2F9/chats/sidebar") {
    return jsonResponse({ cabinet_id: "cabinet/9", projects: [], project_chats: [] });
  }
  throw new Error(`unexpected request: ${url}`);
}

describe("ProjectRuntime service contract", () => {
  it("loads required project, container, metrics, key/model, chat and live workspace routes with auth", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => snapshotFetch(input));
    const service = createProjectRuntimeService({ fetchImpl, getAccessToken: () => "access-token" });

    const snapshot = await service.load("project-42");

    expect(snapshot.project).toMatchObject({ id: "project-42", agent_provider: "codex", resolved_ai_key_id: "key-7" });
    expect(snapshot.container?.project_id).toBe("project-42");
    expect(snapshot.metrics?.project_id).toBe("project-42");
    expect(snapshot.files?.entries).toEqual([]);
    expect(snapshot.keys).toMatchObject([{ id: "key-7", provider: "codex", status: "active", has_secret: true }]);
    expect(snapshot.models.default_model).toBe("gpt-5");
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual(expect.arrayContaining([
      "/api/v1/projects/project-42",
      "/api/v1/projects/project-42/container",
      "/api/v1/projects/project-42/container/metrics?window=1h",
      "/api/v1/projects/project-42/container/workspace/entries?path=",
      "/api/v1/projects/project-42/ai-keys/available",
      "/api/v1/projects/project-42/models/live",
      "/api/v1/cabinets/cabinet%2F9/chats/sidebar",
    ]));
    expect(fetchImpl.mock.calls.every(([, init]) => new Headers(init?.headers).get("Authorization") === "Bearer access-token")).toBe(true);
  });

  it("encodes project IDs and workspace query values without changing the backend routes", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/container/workspace/entries?path=folder+a%26b")) return jsonResponse({ path: "folder a&b", entries: [] });
      if (url.endsWith("/container/workspace/preview?path=folder%2Fa%26b.md")) return jsonResponse({ path: "folder/a&b.md", name: "a&b.md", content: "text" });
      if (url.endsWith("/projects/project%2F42")) return jsonResponse({ ...project, id: "project/42" });
      throw new Error(`unexpected request: ${url}`);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    await service.getProject("project/42");
    await service.listWorkspace("project/42", "folder a&b");
    await service.previewWorkspace("project/42", "folder/a&b.md");

    expect(calls).toEqual([
      "/api/v1/projects/project%2F42",
      "/api/v1/projects/project%2F42/container/workspace/entries?path=folder+a%26b",
      "/api/v1/projects/project%2F42/container/workspace/preview?path=folder%2Fa%26b.md",
    ]);
  });

  it("preserves binary bytes and workspace response headers on the downloaded Blob", async () => {
    const bytes = new Uint8Array([0, 1, 2, 255]);
    let requestInit: RequestInit | undefined;
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestInit = init;
      return new Response(bytes, {
        status: 200,
        headers: {
          "Content-Type": "application/octet-stream",
          "Content-Disposition": 'attachment; filename="plan.bin"',
          "X-Workspace-Path": "docs/plan.bin",
          "X-Workspace-Size": "4",
        },
      });
    });
    const service = createProjectRuntimeService({ fetchImpl, getAccessToken: () => "download-token" });

    const download = await service.downloadWorkspace("project-42", "docs/plan.bin");

    expect(Array.from(new Uint8Array(await download.arrayBuffer()))).toEqual(Array.from(bytes));
    expect(download.type).toBe("application/octet-stream");
    expect(download.contentDisposition).toBe('attachment; filename="plan.bin"');
    expect(download.workspacePath).toBe("docs/plan.bin");
    expect(download.workspaceSize).toBe("4");
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/v1/projects/project-42/container/workspace/content?path=docs%2Fplan.bin");
    expect(requestInit?.headers).toMatchObject({ Accept: "application/octet-stream", Authorization: "Bearer download-token" });
  });

  it("posts cabinet-scoped project creation payloads and rejects a response for another cabinet", async () => {
    const requests: Array<{ url: string; method: string; body?: string; headers?: HeadersInit }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? "GET", body: init?.body as string | undefined, headers: init?.headers });
      // The requested cabinet is cabinet-9; answering with cabinet-other must not be accepted.
      return jsonResponse({ ...project, cabinet_id: "cabinet-other", id: "created-project" }, 201);
    });
    const service = createProjectRuntimeService({ fetchImpl, getAccessToken: () => "create-token" });

    await expect(service.createProject("cabinet-9", "New runtime", "codex")).rejects.toMatchObject({ code: "PROJECT_CABINET_MISMATCH" });
    expect(requests).toEqual([{
      url: "/api/v1/cabinets/cabinet-9/projects",
      method: "POST",
      body: JSON.stringify({ name: "New runtime", agent_provider: "codex" }),
      headers: expect.objectContaining({ Authorization: "Bearer create-token", "Content-Type": "application/json" }),
    }]);
  });

  it("returns a created project only after confirming the requested cabinet in the create response", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({
      id: "created-project",
      cabinet_id: "cabinet-9",
      name: "New runtime",
      status: "draft",
      agent_provider: null,
    }, 201));
    const service = createProjectRuntimeService({ fetchImpl });

    await expect(service.createProject("cabinet-9", "New runtime")).resolves.toMatchObject({
      id: "created-project",
      cabinet_id: "cabinet-9",
      status: "draft",
    });
  });

  it("patches provider/key selection and confirms the saved fields by reading the project back", async () => {
    let savedProject = { ...project };
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        savedProject = { ...savedProject, ...body };
      }
      return jsonResponse(savedProject);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    const saved = await service.patchProject("project-42", {
      agent_provider: "claude_code",
      resolved_ai_key_id: "key-8",
    });
    expect(saved).toMatchObject({ agent_provider: "claude_code", resolved_ai_key_id: "key-8" });
    const readback = await service.getProject("project-42");
    expect(readback).toMatchObject({ agent_provider: "claude_code", resolved_ai_key_id: "key-8" });
    expect(fetchImpl.mock.calls[0]).toMatchObject([
      "/api/v1/projects/project-42",
      {
        method: "PATCH",
        body: JSON.stringify({ agent_provider: "claude_code", resolved_ai_key_id: "key-8" }),
      },
    ]);
  });

  it("uses the confirmed launch, pause, resume, sync, reload and complete routes", async () => {
    const calls: Array<{ url: string; method: string; authorization?: string }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        authorization: (init?.headers as Record<string, string> | undefined)?.Authorization,
      });
      return jsonResponse({ ...project, project_id: "project-42" });
    });
    const service = createProjectRuntimeService({ fetchImpl, getAccessToken: () => "lifecycle-token" });

    for (const action of ["launch", "pause", "resume", "sync", "reload", "complete"] as const) {
      await service.runAction("project-42", action);
    }

    expect(calls).toEqual([
      { url: "/api/v1/projects/project-42/launch", method: "POST", authorization: "Bearer lifecycle-token" },
      { url: "/api/v1/projects/project-42/pause", method: "POST", authorization: "Bearer lifecycle-token" },
      { url: "/api/v1/projects/project-42/resume", method: "POST", authorization: "Bearer lifecycle-token" },
      { url: "/api/v1/projects/project-42/sync", method: "POST", authorization: "Bearer lifecycle-token" },
      { url: "/api/v1/projects/project-42/reload", method: "POST", authorization: "Bearer lifecycle-token" },
      { url: "/api/v1/projects/project-42/complete", method: "POST", authorization: "Bearer lifecycle-token" },
    ]);
  });

  it("allows callers to read the project back after a successful lifecycle response", async () => {
    let launched = false;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/projects/project-42/launch" && init?.method === "POST") {
        launched = true;
        return jsonResponse({ ...project, status: "active" });
      }
      if (url === "/api/v1/projects/project-42") {
        return jsonResponse({ ...project, status: launched ? "active" : "draft" });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    await service.runAction("project-42", "launch");
    const readback = await service.getProject("project-42");

    expect(readback.status).toBe("active");
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/projects/project-42/launch",
      "/api/v1/projects/project-42",
    ]);
  });

  it("creates a model-specific agent session on the confirmed project route and validates its response identity", async () => {
    let init: RequestInit | undefined;
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, requestInit?: RequestInit) => {
      init = requestInit;
      return jsonResponse({ id: "session-7", project_id: "project-42", model: "gpt-5", status: "active" }, 201);
    });
    const service = createProjectRuntimeService({ fetchImpl, getAccessToken: () => "model-token" });

    await expect(service.createModelSession("project-42", "gpt-5")).resolves.toMatchObject({
      id: "session-7",
      project_id: "project-42",
      model: "gpt-5",
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/v1/projects/project-42/agent/sessions");
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ model: "gpt-5" }),
      headers: { Authorization: "Bearer model-token", "Content-Type": "application/json" },
    });

    const mismatched = createProjectRuntimeService({
      fetchImpl: vi.fn(async () => jsonResponse({ id: "session-8", project_id: "project-other", model: "gpt-5" }, 201)),
    });
    await expect(mismatched.createModelSession("project-42", "gpt-5")).rejects.toMatchObject({ code: "PROJECT_ID_MISMATCH" });
  });

  it("rejects mismatched project read and action results", async () => {
    const service = createProjectRuntimeService({
      fetchImpl: vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/launch")
        ? jsonResponse({ project_id: "project-other", ok: true })
        : jsonResponse({ ...project, id: "project-other" })),
    });

    await expect(service.getProject("project-42")).rejects.toMatchObject({ code: "PROJECT_ID_MISMATCH" });
    await expect(service.runAction("project-42", "launch")).rejects.toMatchObject({ code: "PROJECT_ID_MISMATCH" });
  });

  it("aborts the snapshot when a project-scoped read is rejected by authorization", async () => {
    const guardedRoutes = [
      "/api/v1/projects/project-42/container",
      "/api/v1/projects/project-42/container/metrics?window=1h",
      "/api/v1/projects/project-42/attachments",
      "/api/v1/projects/project-42/ai-keys/available",
      "/api/v1/cabinets/cabinet%2F9/chats/sidebar",
      "/api/v1/projects/project-42/container/workspace/entries?path=",
    ];

    for (const guardedRoute of guardedRoutes) {
      const fetchImpl = vi.fn(async (input: RequestInfo | URL) => String(input) === guardedRoute
        ? jsonResponse({ code: "FORBIDDEN", detail: "not allowed for this principal" }, 403)
        : snapshotFetch(input));
      const service = createProjectRuntimeService({ fetchImpl });

      await expect(service.load("project-42")).rejects.toMatchObject({
        message: "not allowed for this principal",
        code: "FORBIDDEN",
        status: 403,
        retryable: false,
      });
    }
  });

  // A transient (retryable) failure of one section must not discard the whole snapshot: the
  // consumers render every section as nullable and retry on their own. Only identity,
  // contract-shape and authorization failures abort `load` (covered by the tests above and
  // below). Listing every section separately keeps the degraded value of each one pinned.
  it("degrades a transient failure of each optional section instead of discarding the project snapshot", async () => {
    const cases: Array<{ route: string; degraded: (snapshot: ProjectRuntimeSnapshot) => unknown; expected: unknown }> = [
      {
        route: "/api/v1/projects/project-42/container",
        degraded: (snapshot) => snapshot.container,
        expected: null,
      },
      {
        route: "/api/v1/projects/project-42/container/metrics?window=1h",
        degraded: (snapshot) => snapshot.metrics,
        expected: null,
      },
      {
        route: "/api/v1/projects/project-42/attachments",
        degraded: (snapshot) => snapshot.attachments,
        expected: [],
      },
      {
        route: "/api/v1/projects/project-42/ai-keys/available",
        degraded: (snapshot) => snapshot.keys,
        expected: [],
      },
      {
        route: "/api/v1/cabinets/cabinet%2F9/chats/sidebar",
        degraded: (snapshot) => snapshot.chats,
        expected: null,
      },
      {
        route: "/api/v1/projects/project-42/container/workspace/entries?path=",
        degraded: (snapshot) => snapshot.files,
        expected: null,
      },
    ];

    for (const { route, degraded, expected } of cases) {
      const fetchImpl = vi.fn(async (input: RequestInfo | URL) => String(input) === route
        ? jsonResponse({ code: "UPSTREAM_UNAVAILABLE", detail: "section read failed" }, 503)
        : snapshotFetch(input));
      const service = createProjectRuntimeService({ fetchImpl });

      const snapshot = await service.load("project-42");

      expect(snapshot.project.id).toBe("project-42");
      expect(snapshot.project.cabinet_id).toBe("cabinet/9");
      expect(degraded(snapshot)).toEqual(expected);
    }

    // Losing the container observation makes the runtime phase unknown, so the live model
    // list cannot be requested at all: it is reported as unavailable rather than "not running".
    const containerDown = createProjectRuntimeService({
      fetchImpl: vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/v1/projects/project-42/container"
        ? jsonResponse({ code: "UPSTREAM_UNAVAILABLE", detail: "section read failed" }, 503)
        : snapshotFetch(input)),
    });
    const degradedSnapshot = await containerDown.load("project-42");
    expect(degradedSnapshot.models).toEqual({ models: [], default_model: null, source: "unavailable" });
  });

  it("keeps a foreign chat sidebar fatal even when that read is otherwise optional", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/v1/cabinets/cabinet%2F9/chats/sidebar"
      ? jsonResponse({ cabinet_id: "cabinet-other", projects: [], project_chats: [] })
      : snapshotFetch(input));
    const service = createProjectRuntimeService({ fetchImpl });

    await expect(service.load("project-42")).rejects.toMatchObject({ code: "PROJECT_CABINET_MISMATCH" });
  });

  it("does not query live workspace or models for a paused project even if stale runtime data says running", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url === "/api/v1/projects/project-42") return jsonResponse({ ...project, status: "paused" });
      return snapshotFetch(input);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    const snapshot = await service.load("project-42");

    expect(snapshot.files).toBeNull();
    expect(snapshot.models).toEqual({ models: [], default_model: null, source: "not_running" });
    expect(calls).not.toContain("/api/v1/projects/project-42/container/workspace/entries?path=");
    expect(calls).not.toContain("/api/v1/projects/project-42/models/live");
  });

  it("accepts the backend catalog response when no AI key is selected", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/v1/projects/project-42"
      ? jsonResponse({ ...project, resolved_ai_key_id: null })
      : String(input) === "/api/v1/projects/project-42/models/live"
        ? jsonResponse({ models: [], default_model: null, source: "catalog" })
        : snapshotFetch(input));
    const service = createProjectRuntimeService({ fetchImpl });

    const snapshot = await service.load("project-42");

    expect(snapshot.models).toEqual({ models: [], default_model: null, source: "catalog" });
  });

  it("surfaces live-model endpoint failures when a selected key and running runtime require that read", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/v1/projects/project-42/models/live"
      ? jsonResponse({ code: "MODELS_UNAVAILABLE", detail: "live models unavailable" }, 503)
      : snapshotFetch(input));
    const service = createProjectRuntimeService({ fetchImpl });

    await expect(service.load("project-42")).rejects.toMatchObject({
      code: "MODELS_UNAVAILABLE",
      message: "live models unavailable",
      status: 503,
    });
  });

  it("retains structured errors, gives non-JSON errors an HTTP fallback, and wraps unknown network failures", async () => {
    const structured = createProjectRuntimeService({
      fetchImpl: vi.fn(async () => jsonResponse({ code: "POD_LAUNCH_FAILED", title: "Launch failed", detail: "image pull denied", trace_id: "trace-7" }, 502)),
    });
    await expect(structured.runAction("project-42", "launch")).rejects.toMatchObject({
      message: "image pull denied",
      code: "POD_LAUNCH_FAILED",
      status: 502,
      traceId: "trace-7",
      retryable: true,
    });

    const unknownHttp = createProjectRuntimeService({
      fetchImpl: vi.fn(async () => new Response("not problem json", { status: 502, statusText: "Bad Gateway" })),
    });
    await expect(unknownHttp.getProject("project-42")).rejects.toMatchObject({ message: "Bad Gateway", status: 502 });

    const network = createProjectRuntimeService({ fetchImpl: vi.fn(async () => { throw new Error("connection reset"); }) });
    await expect(network.getProject("project-42")).rejects.toMatchObject({
      name: "ProjectRuntimeRequestError",
      message: "Не удалось связаться с API: connection reset",
      retryable: true,
    });
  });

  it("keeps structured HTTP errors for binary workspace downloads", async () => {
    const service = createProjectRuntimeService({
      fetchImpl: vi.fn(async () => jsonResponse({ code: "POD_NOT_RUNNING", detail: "workspace requires a running Pod" }, 409)),
    });

    await expect(service.downloadWorkspace("project-42", "README.md")).rejects.toMatchObject({
      code: "POD_NOT_RUNNING",
      message: "workspace requires a running Pod",
      status: 409,
      retryable: false,
    });
  });

  it("rejects unsupported service actions before making an API request", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(project));
    const service = createProjectRuntimeService({ fetchImpl });

    await expect(service.runAction("project-42", "reset" as never)).rejects.toBeInstanceOf(ProjectRuntimeRequestError);
    await expect(service.runAction("project-42", "reset" as never)).rejects.toMatchObject({ code: "UNSUPPORTED_PROJECT_ACTION" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});