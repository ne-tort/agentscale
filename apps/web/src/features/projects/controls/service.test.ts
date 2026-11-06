import { describe, expect, it, vi } from "vitest";
import { createProjectControlsService, ProjectControlsRequestError } from "./service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const project = {
  id: "project/42",
  cabinet_id: "cabinet-9",
  name: "Runtime Lab",
  status: "active",
  about: "Research",
  budget_tokens: 12000,
};

describe("project controls service", () => {
  it("uses the confirmed project GET and PATCH contract and carries the bearer token", async () => {
    const requests: Array<{ url: string; method: string; body?: string; authorization?: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      requests.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? init.body : undefined,
        authorization: headers.get("Authorization") ?? undefined,
      });
      return jsonResponse(project);
    });
    const service = createProjectControlsService({ fetcher, getAccessToken: () => "access-1" });

    expect(await service.getProject("project/42")).toMatchObject({ id: "project/42", status: "active" });
    await service.patchProject("project/42", { name: "New name", about: null, budget_tokens: 0 });

    expect(requests.map(({ url, method }) => [url, method])).toEqual([
      ["/api/v1/projects/project%2F42", "GET"],
      ["/api/v1/projects/project%2F42", "PATCH"],
    ]);
    expect(requests.every((request) => request.authorization === "Bearer access-1")).toBe(true);
    expect(JSON.parse(requests[1].body ?? "{}")).toEqual({ name: "New name", about: null, budget_tokens: 0 });
  });

  it("maps each supported lifecycle action to its confirmed POST route", async () => {
    const requests: Array<[string, string]> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push([String(input), init?.method ?? "GET"]);
      return jsonResponse(project);
    });
    const service = createProjectControlsService({ fetcher });

    for (const action of ["launch", "pause", "resume", "reload", "sync", "complete"] as const) {
      await service.runAction("project/42", action);
    }

    expect(requests).toEqual([
      ["/api/v1/projects/project%2F42/launch", "POST"],
      ["/api/v1/projects/project%2F42/pause", "POST"],
      ["/api/v1/projects/project%2F42/resume", "POST"],
      ["/api/v1/projects/project%2F42/reload", "POST"],
      ["/api/v1/projects/project%2F42/sync", "POST"],
      ["/api/v1/projects/project%2F42/complete", "POST"],
    ]);
  });

  it("accepts the sync materialization result and leaves authoritative project state to readback", async () => {
    const fetcher = vi.fn(async () => jsonResponse({
      project_id: "project/42",
      workspace_root: "/workspace/project-42",
      mcp_config_path: "/workspace/project-42/.mcp.json",
      status: "synced",
      package_names: [],
      sandbox_packages: [],
      agents_source: "default",
      hydrate_generation: 3,
    }));
    const service = createProjectControlsService({ fetcher });

    expect(await service.runAction("project/42", "sync")).toMatchObject({
      project_id: "project/42",
      hydrate_generation: 3,
    });
  });

  it("rejects non-sync action responses that are not project records", async () => {
    const service = createProjectControlsService({
      fetcher: vi.fn(async () => jsonResponse({ project_id: "project/42", status: "synced" })),
    });

    await expect(service.runAction("project/42", "launch")).rejects.toMatchObject({ code: "INVALID_PROJECT_RESPONSE" });
  });

  it("rejects a project response for a different ID", async () => {
    const service = createProjectControlsService({ fetcher: vi.fn(async () => jsonResponse({ ...project, id: "another-project" })) });

    await expect(service.getProject("project/42")).rejects.toMatchObject({ code: "PROJECT_ID_MISMATCH" });
  });

  it("preserves backend problem details and rejects malformed successful project payloads", async () => {
    const failed = createProjectControlsService({
      fetcher: vi.fn(async () => jsonResponse({ code: "NO_AI_KEY", title: "Launch blocked", detail: "Choose a runtime key.", trace_id: "trace-9" }, 422)),
    });
    await expect(failed.runAction("project/42", "launch")).rejects.toMatchObject({
      name: "ProjectControlsRequestError",
      message: "Choose a runtime key.",
      code: "NO_AI_KEY",
      status: 422,
      traceId: "trace-9",
    });

    const malformed = createProjectControlsService({ fetcher: vi.fn(async () => jsonResponse({ status: "accepted" }, 202)) });
    await expect(malformed.getProject("project/42")).rejects.toBeInstanceOf(ProjectControlsRequestError);
  });
});
