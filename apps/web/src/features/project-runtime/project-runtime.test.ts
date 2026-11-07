import { describe, expect, it, vi } from "vitest";
import { createTranslator } from "../../i18n/catalogue";
import {
  createProjectRuntimeService,
  ProjectRuntimeRequestError,
} from "./service";
import { phaseOf, readableError } from "./ProjectRuntime";
import type { ProjectRuntimeSnapshot } from "./types";

const t = createTranslator("ru");

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const project = {
  id: "project-42",
  cabinet_id: "cabinet-9",
  name: "Runtime Lab",
  status: "active",
  agent_provider: "codex",
  resolved_ai_key_id: "key-7",
  budget_tokens: 10000,
};

const container = {
  project_id: "project-42",
  status: "running",
  observed_state: "running",
  runtime: {
    observed_state: "running",
    desired_state: "running",
    metrics_available: true,
    metrics: { cpu_millicores: 140, memory_bytes: 104857600, timestamp: "2026-11-02T10:00:00Z" },
  },
};

describe("project runtime service", () => {
  it("uses the real project, container, metrics, workspace, AI and chat endpoints", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/api/v1/projects/project-42")) return jsonResponse(project);
      if (url.endsWith("/api/v1/projects/project-42/container")) return jsonResponse(container);
      if (url.includes("/container/metrics")) return jsonResponse({ project_id: "project-42", latest: container.runtime.metrics, metrics_available: true, stale: false });
      if (url.includes("/container/workspace/entries")) return jsonResponse({ path: "", entries: [{ name: "README.md", path: "README.md", kind: "file", size: 12 }] });
      if (url.endsWith("/attachments")) return jsonResponse({ items: [{ id: "att-1", filename: "brief.md", size_bytes: 12 }] });
      if (url.endsWith("/ai-keys/available")) return jsonResponse({ items: [{ id: "key-7", name: "Team key", provider: "codex", status: "active" }] });
      if (url.endsWith("/models/live")) return jsonResponse({ models: [{ id: "gpt-5", label: "GPT-5" }], default_model: "gpt-5", source: "live" });
      if (url.endsWith("/cabinets/cabinet-9/chats/sidebar")) return jsonResponse({ cabinet_id: "cabinet-9", projects: [], project_chats: [] });
      throw new Error(`unexpected endpoint: ${url}`);
    });

    const service = createProjectRuntimeService({ fetchImpl });
    const snapshot = await service.load("project-42");

    expect(snapshot.project).toMatchObject({ id: "project-42", resolved_ai_key_id: "key-7" });
    expect(snapshot.container?.runtime?.observed_state).toBe("running");
    expect(snapshot.files?.entries[0].path).toBe("README.md");
    expect(snapshot.attachments).toMatchObject([{ id: "att-1", filename: "brief.md" }]);
    expect(snapshot.keys[0].id).toBe("key-7");
    expect(snapshot.models.default_model).toBe("gpt-5");
    expect(calls.map((call) => call.url)).toEqual(expect.arrayContaining([
      "/api/v1/projects/project-42",
      "/api/v1/projects/project-42/container",
      "/api/v1/projects/project-42/container/metrics?window=1h",
      "/api/v1/projects/project-42/container/workspace/entries?path=",
      "/api/v1/projects/project-42/attachments",
      "/api/v1/projects/project-42/ai-keys/available",
      "/api/v1/projects/project-42/models/live",
      "/api/v1/cabinets/cabinet-9/chats/sidebar",
    ]));
  });

  it("maps lifecycle operations to the backend action endpoints and preserves PATCH fields", async () => {
    const requests: Array<{ url: string; method: string; body?: string }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : undefined });
      if (init?.method === "PATCH") return jsonResponse({ ...project, ...JSON.parse(String(init.body)) });
      return jsonResponse(project);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    await service.runAction("project-42", "launch");
    await service.runAction("project-42", "pause");
    await service.runAction("project-42", "resume");
    await service.runAction("project-42", "reload");
    await service.runAction("project-42", "sync");
    await service.runAction("project-42", "complete");
    const updated = await service.patchProject("project-42", {
      name: "Runtime Lab 2",
      about: "Updated",
      budget_tokens: 2000,
      agent_provider: "claude_code",
      resolved_ai_key_id: "key-8",
    });

    expect(requests.filter((request) => request.method === "POST").map((request) => request.url)).toEqual([
      "/api/v1/projects/project-42/launch",
      "/api/v1/projects/project-42/pause",
      "/api/v1/projects/project-42/resume",
      "/api/v1/projects/project-42/reload",
      "/api/v1/projects/project-42/sync",
      "/api/v1/projects/project-42/complete",
    ]);
    expect(updated).toMatchObject({ name: "Runtime Lab 2", resolved_ai_key_id: "key-8" });
    expect(requests.at(-1)).toMatchObject({ method: "PATCH", url: "/api/v1/projects/project-42" });
    expect(JSON.parse(requests.at(-1)?.body ?? "{}")).toEqual({
      name: "Runtime Lab 2",
      about: "Updated",
      budget_tokens: 2000,
      agent_provider: "claude_code",
      resolved_ai_key_id: "key-8",
    });
  });

  it("launches a draft and reads the backend project and runtime state back", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    const launchedProject = {
      ...project,
      status: "active",
      runtime: { observed_state: "running", desired_state: "running" },
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url === "/api/v1/projects/project-42" && method === "GET") {
        return jsonResponse(calls.filter((call) => call.url === url && call.method === "GET").length === 1
          ? { ...project, status: "draft" }
          : launchedProject);
      }
      if (url === "/api/v1/projects/project-42/launch" && method === "POST") return jsonResponse(launchedProject);
      if (url === "/api/v1/projects/project-42/container") return jsonResponse(container);
      if (url.includes("/container/metrics")) return jsonResponse({ project_id: "project-42", latest: null, stale: true });
      if (url.includes("/container/workspace/entries")) return jsonResponse({ path: "", entries: [] });
      if (url.endsWith("/attachments") || url.endsWith("/ai-keys/available")) return jsonResponse({ items: [] });
      if (url.endsWith("/models/live")) return jsonResponse({ models: [], default_model: null, source: "live" });
      if (url.endsWith("/chats/sidebar")) return jsonResponse({ cabinet_id: "cabinet-9", projects: [], project_chats: [] });
      throw new Error(`unexpected endpoint: ${method} ${url}`);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    expect((await service.getProject("project-42")).status).toBe("draft");
    await service.runAction("project-42", "launch");
    const snapshot = await service.load("project-42");

    expect(snapshot.project).toMatchObject({ status: "active", runtime: { observed_state: "running" } });
    expect(snapshot.container?.project_id).toBe("project-42");
    expect(calls).toContainEqual({ url: "/api/v1/projects/project-42/launch", method: "POST" });
    expect(calls.filter((call) => call.url === "/api/v1/projects/project-42" && call.method === "GET")).toHaveLength(2);
  });

  it("lists, previews and downloads workspace files using the project workspace routes", async () => {
    const path = "docs/one file&plan.md";
    const calls: string[] = [];
    const bytes = new Uint8Array([0, 1, 2, 255]);
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/workspace/entries?")) {
        return jsonResponse({ path: "docs", entries: [{ name: "one file&plan.md", path, kind: "file", size: 4 }] });
      }
      if (url.includes("/workspace/preview?")) {
        return jsonResponse({ path, name: "one file&plan.md", size: 4, truncated: false, content: "plan" });
      }
      if (url.includes("/workspace/content?")) return new Response(bytes, { status: 200 });
      throw new Error(`unexpected endpoint: ${url}`);
    });
    const service = createProjectRuntimeService({ fetchImpl });

    const entries = await service.listWorkspace("project-42", "docs");
    const preview = await service.previewWorkspace("project-42", path);
    const download = await service.downloadWorkspace("project-42", path);

    expect(entries.entries[0]).toMatchObject({ path, kind: "file" });
    expect(preview).toMatchObject({ path, content: "plan", truncated: false });
    expect(Array.from(new Uint8Array(await download.arrayBuffer()))).toEqual(Array.from(bytes));
    expect(calls).toEqual([
      "/api/v1/projects/project-42/container/workspace/entries?path=docs",
      "/api/v1/projects/project-42/container/workspace/preview?path=docs%2Fone+file%26plan.md",
      "/api/v1/projects/project-42/container/workspace/content?path=docs%2Fone+file%26plan.md",
    ]);
  });

  it("surfaces project read errors and keeps optional loading failures non-fatal", async () => {
    const failedRead = createProjectRuntimeService({
      fetchImpl: vi.fn(async () => jsonResponse({ code: "PROJECT_NOT_FOUND", title: "Not found", detail: "project missing" }, 404)),
    });
    await expect(failedRead.load("missing-project")).rejects.toMatchObject({
      message: "project missing",
      code: "PROJECT_NOT_FOUND",
      status: 404,
      retryable: false,
    });

    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/projects/project-42") return jsonResponse(project);
      return jsonResponse({ code: "UPSTREAM_UNAVAILABLE", detail: "temporarily unavailable" }, 503);
    });
    const snapshot = await createProjectRuntimeService({ fetchImpl }).load("project-42");

    expect(snapshot.project.id).toBe("project-42");
    expect(snapshot.container).toBeNull();
    expect(snapshot.metrics).toBeNull();
    expect(snapshot.files).toBeNull();
    expect(snapshot.attachments).toEqual([]);
    expect(snapshot.keys).toEqual([]);
    expect(snapshot.models).toEqual({ models: [], default_model: null, source: "unavailable" });
    expect(snapshot.chats).toBeNull();
  });

  it("keeps backend problem details and retry semantics", async () => {
    const service = createProjectRuntimeService({
      fetchImpl: vi.fn(async () => jsonResponse({ code: "POD_LAUNCH_FAILED", title: "Pod Launch Failed", detail: "image pull denied", trace_id: "trace-7" }, 502)),
    });

    await expect(service.runAction("project-42", "launch")).rejects.toMatchObject({
      message: "image pull denied",
      code: "POD_LAUNCH_FAILED",
      status: 502,
      traceId: "trace-7",
      retryable: true,
    });
  });

  it("sends the authenticated access token on project runtime requests", async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(project));
    const service = createProjectRuntimeService({ fetchImpl, getAccessToken: () => "access-token" });

    await service.getProject("project-42");

    expect(fetchImpl.mock.calls[0][1]?.headers).toMatchObject({ Authorization: "Bearer access-token" });
  });
});

describe("project runtime view model", () => {
  const base: ProjectRuntimeSnapshot = {
    project: { id: "project-42", name: "Runtime Lab", status: "active" },
    container: null,
    metrics: null,
    files: null,
    attachments: [],
    keys: [],
    models: { models: [] },
    chats: null,
  };

  it("prefers observed backend state and maps lifecycle fallbacks", () => {
    expect(phaseOf({ ...base, project: { ...base.project, status: "active" } })).toBe("unknown");
    expect(phaseOf({ ...base, project: { ...base.project, status: "paused" } })).toBe("paused");
    expect(phaseOf({ ...base, project: { ...base.project, status: "completed" } })).toBe("completed");
    expect(phaseOf({ ...base, project: { ...base.project, status: "error" } })).toBe("error");
    expect(phaseOf({ ...base, container: { project_id: "project-42", observed_state: "hydrating" } })).toBe("hydrating");
  });

  it("formats typed request errors without hiding the trace id", () => {
    const error = new ProjectRuntimeRequestError("image pull denied", { code: "POD_LAUNCH_FAILED", trace_id: "trace-7", status: 502 });
    expect(readableError(t, error)).toContain("image pull denied");
    expect(readableError(t, error)).toContain("trace-7");
  });
});
