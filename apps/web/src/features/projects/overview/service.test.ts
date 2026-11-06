import { describe, expect, it, vi } from "vitest";
import { createProjectOverviewService } from "./service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const project = { id: "p-1", cabinet_id: "cab-1", name: "Market study", status: "draft", updated_at: "2026-10-30T12:00:00Z" };

describe("project overview service", () => {
  it("uses employee cabinet, project, and selection endpoints with the authenticated token", async () => {
    const requests: Array<{ url: string; method: string; body?: string; authorization?: string }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      const authorization = headers.get("Authorization") ?? undefined;
      requests.push({ url: String(input), method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : undefined, authorization });
      if (String(input).endsWith("/cabinets")) return jsonResponse({ items: [{ id: "cab-1", name: "Operations" }] });
      if (init?.method === "POST" && String(input).endsWith("/cabinets/cab-1/projects")) return jsonResponse(project, 201);
      if (String(input).endsWith("/cabinets/cab-1/projects")) return jsonResponse({ items: [project] });
      if (String(input).endsWith("/projects/p-1")) return jsonResponse(project);
      if (init?.method === "POST" && String(input).endsWith("/projects")) return jsonResponse(project, 201);
      if (init?.method === "POST") return jsonResponse({ status: "accepted" }, 202);
      if (init?.method === "PUT") return jsonResponse({ project_id: "p-1" });
      return jsonResponse({ project_id: "p-1" });
    });
    const service = createProjectOverviewService({ fetchImpl, getAccessToken: () => "access-1" });

    expect(await service.listCabinets()).toEqual([{ id: "cab-1", name: "Operations" }]);
    expect(await service.listProjects("cab-1")).toMatchObject([{ id: "p-1", name: "Market study", status: "draft", selected: false }]);
    expect(await service.getProject("p-1")).toMatchObject({ id: "p-1", cabinet_id: "cab-1", status: "draft" });
    expect(await service.getSelection("cab-1")).toBe("p-1");
    await service.selectProject("cab-1", "p-1");

    expect(requests.map(({ url, method }) => [url, method])).toEqual([
      ["/api/v1/cabinets", "GET"],
      ["/api/v1/cabinets/cab-1/projects", "GET"],
      ["/api/v1/projects/p-1", "GET"],
      ["/api/v1/cabinets/cab-1/me/selection", "GET"],
      ["/api/v1/cabinets/cab-1/me/selection", "PUT"],
      ["/api/v1/cabinets/cab-1/me/selection", "GET"],
    ]);
    expect(requests.every((request) => request.authorization === "Bearer access-1")).toBe(true);
    expect(JSON.parse(requests.find((request) => request.method === "PUT")?.body ?? "{}")).toEqual({ project_id: "p-1" });
  });

  it("creates with a minimal cabinet payload and uses the confirmed project readback and lifecycle routes", async () => {
    const requests: Array<{ url: string; method: string; body?: string }> = [];
    const lifecycleStatuses: Record<string, string> = {
      launch: "active", pause: "paused", resume: "active", reload: "error", complete: "completed",
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : undefined });
      if (init?.method === "POST" && String(input) === "/api/v1/cabinets/cab-1/projects") return jsonResponse(project, 201);
      if (String(input) === "/api/v1/projects/p-1" && init?.method !== "POST") return jsonResponse(project);
      if (String(input) === "/api/v1/cabinets/cab-1/projects") return jsonResponse({ items: [project] });
      if (init?.method === "POST") {
        const action = String(input).split("/").at(-1) ?? "";
        return jsonResponse({ ...project, status: lifecycleStatuses[action] ?? "draft" });
      }
      return jsonResponse(project);
    });
    const service = createProjectOverviewService({ fetchImpl });

    const created = await service.createProject("cab-1", "  Market study  ");
    expect(await service.listProjects("cab-1")).toMatchObject([{ id: "p-1", name: "Market study" }]);
    expect(await service.getProject("p-1")).toMatchObject({ id: "p-1", name: "Market study" });
    await service.lifecycle(created.id, "launch");
    await service.lifecycle(created.id, "pause");
    await service.lifecycle(created.id, "resume");
    await service.lifecycle(created.id, "reload");
    await service.lifecycle(created.id, "complete");

    expect(created.status).toBe("draft");
    expect(JSON.parse(requests[0].body ?? "{}")).toEqual({ name: "Market study" });
    // Прежняя версия ожидания включала ещё одну пару "GET cabinets/CAB/projects" +
    // "GET projects/p-1" между строкой проекта и lifecycle-маршрутами. Этих запросов
    // здесь быть не должно: readback создания — это ровно два явных вызова из строк 65–66
    // (`listProjects` + `getProject`), и `createProject` сам ничего не перечитывает, иначе
    // успех проверялся бы ответом, который сервис сгенерировал сам, а не тем, что
    // действительно сохранил backend. Сверка readback с ожидаемым состоянием — обязанность
    // вызывающей стороны (`ProjectsOverview.createProject`), и она проверяется в
    // `ProjectsOverview.test.tsx`. Ожидание приведено к фактическому контракту: один
    // список, один GET, затем пять lifecycle-маршрутов.
    expect(requests.slice(1).map(({ url, method }) => [url, method])).toEqual([
      ["/api/v1/cabinets/cab-1/projects", "GET"],
      ["/api/v1/projects/p-1", "GET"],
      ["/api/v1/projects/p-1/launch", "POST"],
      ["/api/v1/projects/p-1/pause", "POST"],
      ["/api/v1/projects/p-1/resume", "POST"],
      ["/api/v1/projects/p-1/reload", "POST"],
      ["/api/v1/projects/p-1/complete", "POST"],
    ]);
  });

  it("rejects malformed create responses and exposes list/get readbacks used to detect mismatches", async () => {
    const service = createProjectOverviewService({ fetchImpl: vi.fn(async () => jsonResponse({ status: "accepted" }, 202)) });
    await expect(service.createProject("cab-1", "Market study")).rejects.toThrow("неполные данные проекта");

    const mismatch = createProjectOverviewService({ fetchImpl: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v1/cabinets/cab-1/projects" && init?.method === "POST") return jsonResponse(project, 201);
      if (String(input) === "/api/v1/cabinets/cab-1/projects") return jsonResponse({ items: [{ ...project, name: "Different" }] });
      if (String(input) === "/api/v1/projects/p-1") return jsonResponse({ ...project, name: "Different" });
      return jsonResponse(project, 201);
    }) });
    const created = await mismatch.createProject("cab-1", "Market study");
    await expect(mismatch.listProjects(created.cabinet_id)).resolves.toMatchObject([{ name: "Different" }]);
    await expect(mismatch.getProject(created.id)).resolves.toMatchObject({ name: "Different" });
  });

  it("deduplicates list rows using the freshest response and rejects wrong-cabinet rows", async () => {
    const service = createProjectOverviewService({ fetchImpl: vi.fn(async () => jsonResponse({ items: [
      { ...project, name: "Old", updated_at: "2026-10-29T12:00:00Z" },
      { ...project, name: "Latest", updated_at: "2026-10-30T12:00:00Z" },
    ] })) });
    await expect(service.listProjects("cab-1")).resolves.toMatchObject([{ id: "p-1", name: "Latest" }]);
    const wrongCabinet = createProjectOverviewService({ fetchImpl: vi.fn(async () => jsonResponse({ items: [{ ...project, cabinet_id: "cab-2" }] })) });
    await expect(wrongCabinet.listProjects("cab-1")).rejects.toThrow("другого кабинета");
  });

  it("keeps backend problem details and rejects unknown lifecycle values", async () => {
    const failed = createProjectOverviewService({ fetchImpl: vi.fn(async () => jsonResponse({ code: "NO_AI_KEY", title: "Launch blocked", detail: "Select a runtime key first." }, 422)) });
    await expect(failed.lifecycle("p-1", "launch")).rejects.toMatchObject({ name: "ProjectOverviewRequestError", status: 422, code: "NO_AI_KEY" });
    await expect(failed.lifecycle("p-1", "launch")).rejects.toThrow("Select a runtime key first.");

    const invalid = createProjectOverviewService({ fetchImpl: vi.fn(async () => jsonResponse({ items: [{ ...project, status: "deleted" }] })) });
    await expect(invalid.listProjects("cab-1")).rejects.toThrow("неизвестный статус проекта");
  });
});
