import { describe, expect, it, vi } from "vitest";
import { createModuleAdminService } from "./service";

function response(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("module admin service API contract", () => {
  it("uses separate admin and company catalogs and preserves API-returned source and rights", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response({ items: [] }))
      .mockResolvedValueOnce(response({ items: [{ id: "mod_platform", name: "Shared" }] }))
      .mockResolvedValueOnce(response([{ id: "mod_platform", source: "platform_assigned", writable: false, bind_kind: "global", child_may_edit: false }]));
    const service = createModuleAdminService({ fetcher });
    await expect(service.listAdminModules()).resolves.toEqual([{ id: "mod_platform", name: "Shared" }]);
    await expect(service.listCompanyModules("company/1")).resolves.toEqual([
      { id: "mod_platform", source: "platform_assigned", writable: false, bind_kind: "global", child_may_edit: false },
    ]);
    expect(String(fetcher.mock.calls[0][0])).toBe("/api/v1/admin/modules");
    expect(String(fetcher.mock.calls[1][0])).toBe("/api/v1/companies/company%2F1/modules");
  });

  it("maps confirmed metadata, company grant, cabinet/project binding and installation reads", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response({ items: [] }));
    const service = createModuleAdminService({ fetcher });
    await service.listMetaDocuments("admin", "mod/1");
    await service.listCabinetBindings("company", "mod/1", "co/1");
    await service.listProjectBindings("company", "mod/1", "co/1");
    await service.listCabinetInstallations("cab/1");
    await service.listCabinetModuleData("cab/1", "mod/1", "templates");
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/admin/modules/mod%2F1/meta/documents",
      "/api/v1/companies/co%2F1/modules/mod%2F1/cabinets",
      "/api/v1/companies/co%2F1/modules/mod%2F1/projects",
      "/api/v1/cabinets/cab%2F1/modules",
      "/api/v1/cabinets/cab%2F1/modules/mod%2F1/data/templates",
    ]);
  });

  it("writes company grants and cabinet binding lists through the confirmed PATCH routes", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response({ id: "mod-1" }));
    const service = createModuleAdminService({ fetcher });
    await service.patchAdminModule("mod-1", { company_ids: ["co-1"] });
    await service.patchAdminModule("mod-1", { cabinet_ids: ["cab-1"] });
    await service.patchCompanyModule("co-1", "mod-1", { cabinet_ids: ["cab-2"] });
    expect(fetcher.mock.calls.map(([input, init]) => [String(input), init?.method, init?.body])).toEqual([
      ["/api/v1/admin/modules/mod-1", "PATCH", JSON.stringify({ company_ids: ["co-1"] })],
      ["/api/v1/admin/modules/mod-1", "PATCH", JSON.stringify({ cabinet_ids: ["cab-1"] })],
      ["/api/v1/companies/co-1/modules/mod-1", "PATCH", JSON.stringify({ cabinet_ids: ["cab-2"] })],
    ]);
  });

  it("preserves HTTP status and server details for rejected access", async () => {
    const service = createModuleAdminService({ fetcher: vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response({ detail: "not allowed" }, 403)) });
    await expect(service.getAdminModule("mod-1")).rejects.toMatchObject({ status: 403, message: "not allowed" });
  });
});