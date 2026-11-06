import { describe, expect, it, vi } from "vitest";
import { createCabinetOverviewService, CabinetOverviewRequestError } from "./service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const project = {
  id: "project-1",
  cabinet_id: "cabinet-1",
  name: "Signal Lab",
  status: "active",
  observed_state: "running",
};

function responseFor(input: RequestInfo | URL, init?: RequestInit): Response {
  const url = String(input);
  if (url.endsWith("/cabinets/cabinet-1/presence/heartbeat")) return jsonResponse({ ok: true, employee_id: "employee-1", cabinet_id: "cabinet-1" });
  if (url.endsWith("/cabinets") && !url.includes("/cabinets/cabinet-1")) return jsonResponse({ items: [{ id: "cabinet-1", name: "Operations", company_id: "company-1", company_name: "Northwind" }] });
  if (url.endsWith("/cabinets/cabinet-1")) return jsonResponse({ id: "cabinet-1", name: "Operations", company_id: "company-1", company_name: "Northwind" });
  if (url.endsWith("/cabinets/cabinet-1/projects")) return jsonResponse({ items: [project] });
  if (url.endsWith("/cabinets/cabinet-1/metrics")) return jsonResponse({ employees_total: 2, employees_online: 1, agent_tokens_used: 42, agent_requests: 3, last_activity_at: "2026-11-01T10:00:00Z" });
  if (url.endsWith("/cabinets/cabinet-1/me/selection")) return jsonResponse({ cabinet_id: "cabinet-1", project_id: "project-1", chat_session_id: "session-1" });
  if (url.endsWith("/cabinets/cabinet-1/chats/sidebar")) return jsonResponse({ projects: [{ project_id: "project-1", project_name: "Signal Lab", chats: [{ session_id: "session-1", project_id: "project-1" }] }], pinned: [], project_chats: [] });
  if (url.endsWith("/cabinets/cabinet-1/modules")) return jsonResponse({ items: [{ id: "module-1", name: "Equipment" }] });
  if (url.endsWith("/projects/project-1/runtime-modules")) return jsonResponse({ items: [{ id: "module-1", name: "Equipment", bind_kind: "global" }] });
  if (url.endsWith("/projects/project-1/runtime-modules/module-1/meta/documents/tabs")) return jsonResponse({ slug: "tabs", body: { items: [{ title: "Equipment", view_slug: "equipment_hub", nav: { contour: "employee", placement: "data" } }, { title: "Hidden", view_slug: "hidden", enabled: false, nav: { contour: "employee", placement: "data" } }, { title: "Admin", view_slug: "admin", nav: { contour: "admin", placement: "management" } }] } });
  return jsonResponse({ items: [] }, init?.method === "POST" ? 201 : 200);
}

describe("cabinet overview service", () => {
  it("reads cabinet, selection, sidebar, projects, metrics, presence, and module navigation routes", async () => {
    const requests: Array<{ url: string; method: string; authorization: string | null }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? "GET", authorization: new Headers(init?.headers).get("Authorization") });
      return responseFor(input, init);
    });
    const service = createCabinetOverviewService({ fetcher: fetcher as typeof fetch, getAccessToken: () => "access-1" });

    const snapshot = await service.load("cabinet-1");

    expect(snapshot.cabinet).toMatchObject({ id: "cabinet-1", company_name: "Northwind" });
    expect(snapshot.projects).toEqual([expect.objectContaining({ id: "project-1", status: "active" })]);
    expect(snapshot.selection).toMatchObject({ project_id: "project-1", chat_session_id: "session-1" });
    expect(snapshot.sidebar?.projects[0]?.chats).toEqual([{ project_id: "project-1", session_id: "session-1" }]);
    expect(snapshot.metrics).toMatchObject({ employees_online: 1, agent_tokens_used: 42 });
    expect(snapshot.presence).toMatchObject({ ok: true, cabinet_id: "cabinet-1" });
    expect(snapshot.moduleTabs?.["module-1"]).toEqual([expect.objectContaining({ view_slug: "equipment_hub" })]);
    expect(requests.some((request) => request.url.endsWith("/cabinets/cabinet-1/presence/heartbeat") && request.method === "POST")).toBe(true);
    expect(requests.every((request) => request.authorization === "Bearer access-1")).toBe(true);
    expect(snapshot.errors).toEqual({});
  });

  it("keeps healthy sections when an optional section is unavailable", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/cabinets/cabinet-1/metrics")) return jsonResponse({ code: "METRICS_UNAVAILABLE", detail: "metrics store is cold" }, 503);
      return responseFor(input, init);
    });
    const service = createCabinetOverviewService({ fetcher: fetcher as typeof fetch });

    const snapshot = await service.load("cabinet-1");

    expect(snapshot.cabinet).toBeDefined();
    expect(snapshot.projects).toHaveLength(1);
    expect(snapshot.metrics).toBeUndefined();
    expect(snapshot.errors.metrics).toMatchObject({ message: "metrics store is cold", name: "CabinetOverviewRequestError" });
  });

  it("reports malformed project payloads instead of inventing a project", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/cabinets/cabinet-1/projects")) return jsonResponse({ items: [{ id: "project-1", cabinet_id: "cabinet-1", name: "Missing status" }] });
      return responseFor(input, init);
    });
    const service = createCabinetOverviewService({ fetcher: fetcher as typeof fetch });

    const snapshot = await service.load("cabinet-1");

    expect(snapshot.projects).toBeUndefined();
    expect(snapshot.errors.projects).toBeInstanceOf(CabinetOverviewRequestError);
  });
});
