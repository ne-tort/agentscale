import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, createApiClient } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("expanded central API contracts", () => {
  it("uses the verified schedule CRUD paths, JSON payloads, and 204 delete handling", async () => {
    const schedule = { id: "schedule-1", project_id: "project-1", name: "Hourly", cron: "0 * * * *", timezone: "UTC", payload: {}, enabled: true };
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [schedule] }))
      .mockResolvedValueOnce(jsonResponse(schedule, 201))
      .mockResolvedValueOnce(jsonResponse({ ...schedule, enabled: false }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = createApiClient({ fetcher });

    await expect(client.listProjectSchedules("project/1")).resolves.toEqual({ items: [schedule] });
    await client.createProjectSchedule("project/1", { name: "Hourly", cron: "0 * * * *", timezone: "UTC", payload: {}, enabled: true });
    await client.patchProjectSchedule("project/1", "schedule/1", { enabled: false });
    await expect(client.deleteProjectSchedule("project/1", "schedule/1")).resolves.toBeUndefined();

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/projects/project%2F1/schedules", undefined],
      ["/api/v1/projects/project%2F1/schedules", "POST"],
      ["/api/v1/projects/project%2F1/schedules/schedule%2F1", "PATCH"],
      ["/api/v1/projects/project%2F1/schedules/schedule%2F1", "DELETE"],
    ]);
    expect(fetcher.mock.calls[1][1]?.body).toBe(JSON.stringify({ name: "Hourly", cron: "0 * * * *", timezone: "UTC", payload: {}, enabled: true }));
    expect(fetcher.mock.calls[2][1]?.body).toBe(JSON.stringify({ enabled: false }));
    expect(fetcher.mock.calls[1][1]?.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  it("uses the external outgoing webhook routes and exact request fields", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: "wh-1", url: "https://example.test/hook", events: ["agent.completed"], project_id: "project-1", secret: "once" }, 201))
      .mockResolvedValueOnce(jsonResponse({ id: "wh-1", enabled: false }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: "delivery-1", status: "pending" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = createApiClient({ fetcher });

    await client.listOutgoingWebhooks("project/1");
    await client.createOutgoingWebhook({ url: "https://example.test/hook", events: ["agent.completed"], project_id: "project-1" });
    await client.setOutgoingWebhookEnabled("wh/1", false);
    await client.listWebhookDeliveries(25);
    await client.retryWebhookDelivery("delivery/1");
    await client.deleteOutgoingWebhook("wh/1");

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/external/webhooks?project_id=project%2F1", undefined],
      ["/api/v1/external/webhooks", "POST"],
      ["/api/v1/external/webhooks/wh%2F1", "PATCH"],
      ["/api/v1/external/webhook-deliveries?limit=25", undefined],
      ["/api/v1/external/webhook-deliveries/delivery%2F1/retry", "POST"],
      ["/api/v1/external/webhooks/wh%2F1", "DELETE"],
    ]);
    expect(fetcher.mock.calls[1][1]?.body).toBe(JSON.stringify({ url: "https://example.test/hook", events: ["agent.completed"], project_id: "project-1" }));
    expect(fetcher.mock.calls[2][1]?.body).toBe(JSON.stringify({ enabled: false }));
  });

  it("sends project module data and action requests with encoded ids and session context", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createApiClient({ fetcher });

    await client.listProjectRuntimeModules("project-1");
    await client.getProjectRuntimeModuleMeta("project-1", "mod/1", "tabs/main");
    await client.listProjectRuntimeModuleData("project-1", "mod/1", "stock/items", { page: 2, page_size: 25, search: "a b", in_stock_only: true, chats: "all" }, "session-1");
    await client.createProjectRuntimeModuleData("project-1", "mod/1", "stock", { name: "item" }, "session-1");
    await client.patchProjectRuntimeModuleData("project-1", "mod/1", "stock", "row/1", { count: 4 }, "session-1");
    await client.deleteProjectRuntimeModuleData("project-1", "mod/1", "stock", "row/1");
    await client.bindProjectModule("project-1", "mod/1", { bind_kind: "global", child_may_edit: false });
    await client.unbindProjectModule("project-1", "mod/1");
    await client.invokeCabinetModuleAction("cabinet-1", "mod/1", "action/1", { project_id: "project-1", row_id: "row-1", params: { force: true } }, "session-1");

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/projects/project-1/runtime-modules", undefined],
      ["/api/v1/projects/project-1/runtime-modules/mod%2F1/meta/documents/tabs%2Fmain", undefined],
      ["/api/v1/projects/project-1/runtime-modules/mod%2F1/data/stock%2Fitems?page=2&page_size=25&search=a+b&in_stock_only=true&chats=all", undefined],
      ["/api/v1/projects/project-1/runtime-modules/mod%2F1/data/stock", "POST"],
      ["/api/v1/projects/project-1/runtime-modules/mod%2F1/data/stock/row%2F1", "PATCH"],
      ["/api/v1/projects/project-1/runtime-modules/mod%2F1/data/stock/row%2F1", "DELETE"],
      ["/api/v1/projects/project-1/modules/mod%2F1/bind", "POST"],
      ["/api/v1/projects/project-1/modules/mod%2F1", "DELETE"],
      ["/api/v1/cabinets/cabinet-1/modules/mod%2F1/actions/action%2F1/invoke", "POST"],
    ]);
    expect(fetcher.mock.calls[3][1]?.body).toBe(JSON.stringify({ body: { name: "item" } }));
    expect(fetcher.mock.calls[4][1]?.body).toBe(JSON.stringify({ body: { count: 4 } }));
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({ "X-Prodavan-Session-Id": "session-1" });
    expect(JSON.parse(String(fetcher.mock.calls[8][1]?.body))).toEqual({ row_id: "row-1", project_id: "project-1", params: { force: true } });
  });

  it("routes project module action invocation and multipart content upload through the cabinet API", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ asset_id: "asset-1", version_id: "version-1", filename: "input.txt" }));
    const client = createApiClient({ getAccessToken: () => "token", fetcher });
    const file = new Blob(["input"], { type: "text/plain" });

    await client.invokeProjectModuleAction("cabinet-1", "project-1", "mod-1", "import", { row_id: "row-1", params: { mode: "replace" } }, "session-1");
    await client.uploadProjectModuleContent("cabinet-1", file, "input.txt");

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/actions/import/invoke", "POST"],
      ["/api/v1/cabinets/cabinet-1/content/upload", "POST"],
    ]);
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify({ project_id: "project-1", row_id: "row-1", params: { mode: "replace" } }));
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({ "X-Prodavan-Session-Id": "session-1", "Content-Type": "application/json" });
    const upload = fetcher.mock.calls[1][1]!;
    expect(upload.body).toBeInstanceOf(FormData);
    expect(upload.headers).not.toHaveProperty("Content-Type");
    expect(upload.headers).toMatchObject({ Authorization: "Bearer token" });
  });

  it("covers cabinet module data CRUD, secrets, and the existing project chat settings route", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createApiClient({ fetcher });

    await client.listCabinetModuleData("cabinet-1", "mod-1", "catalog", { page: 2, page_size: 10, search: "adapter", in_stock_only: false }, "session-1");
    await client.createCabinetModuleData("cabinet-1", "mod-1", "catalog", { sku: "A" }, "session-1");
    await client.patchCabinetModuleData("cabinet-1", "mod-1", "catalog", "row-1", { sku: "B" }, "session-1");
    await client.deleteCabinetModuleData("cabinet-1", "mod-1", "catalog", "row-1", "session-1");
    await client.invokeCabinetModuleAction("cabinet-1", "mod-1", "rebuild", { row_id: "row-1", project_id: "project-1", params: { full: true } }, "session-1");
    await client.uploadCabinetModuleSecret("cabinet-1", "mod-1", { secret: "stored-secret", label: "API key" });
    await client.getProjectChatErrorPolicy("project-1");
    await client.setProjectChatErrorPolicy("project-1", { interval_sec: 15, max_attempts: 3, fallback_models: ["model-1"] });

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/data/catalog?page=2&page_size=10&search=adapter&in_stock_only=false", undefined],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/data/catalog", "POST"],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/data/catalog/row-1", "PATCH"],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/data/catalog/row-1", "DELETE"],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/actions/rebuild/invoke", "POST"],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/secrets/upload", "POST"],
      ["/api/v1/projects/project-1/chat-error-policy", undefined],
      ["/api/v1/projects/project-1/chat-error-policy", "PUT"],
    ]);
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({ "X-Prodavan-Session-Id": "session-1" });
    expect(fetcher.mock.calls[1][1]?.body).toBe(JSON.stringify({ body: { sku: "A" } }));
    expect(fetcher.mock.calls[4][1]?.body).toBe(JSON.stringify({ row_id: "row-1", project_id: "project-1", params: { full: true } }));
    expect(fetcher.mock.calls[5][1]?.body).toBe(JSON.stringify({ secret: "stored-secret", label: "API key" }));
    expect(fetcher.mock.calls[7][1]?.body).toBe(JSON.stringify({ interval_sec: 15, max_attempts: 3, fallback_models: ["model-1"] }));
  });

  it("covers cabinet bindings, company/admin module CRUD, meta, actions, profile, and payload shapes", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createApiClient({ fetcher });

    await client.listCabinetModules("cabinet-1");
    await client.listCabinetModuleBoundProjects("cabinet-1", "mod-1");
    await client.getCabinetModuleMeta("cabinet-1", "mod-1", "mcp_tools");
    await client.listCompanyModules("company-1");
    await client.createCompanyModule("company-1", "Local module");
    await client.getCompanyModule("company-1", "mod-1");
    await client.patchCompanyModule("company-1", "mod-1", { cabinet_ids: ["cabinet-1"] });
    await client.copyCompanyModule("company-1", "mod-1", "Copied module");
    await client.deleteCompanyModule("company-1", "mod-1");
    await client.listCompanyModuleProjects("company-1", "mod-1");
    await client.listCompanyModuleCabinets("company-1", "mod-1");
    await client.listCompanyModuleMeta("company-1", "mod-1");
    await client.getCompanyModuleMeta("company-1", "mod-1", "tabs");
    await client.putCompanyModuleMeta("company-1", "mod-1", "tabs", [{ title: "Overview" }]);
    await client.listCompanyModuleData("company-1", "mod-1", "catalog");
    await client.createCompanyModuleData("company-1", "mod-1", "catalog", { sku: "A" });
    await client.patchCompanyModuleData("company-1", "mod-1", "catalog", "row-1", { sku: "B" });
    await client.deleteCompanyModuleData("company-1", "mod-1", "catalog", "row-1");
    await client.invokeCompanyModuleAction("company-1", "mod-1", "publish", { row_id: "row-1", params: { dry_run: true } });
    await client.listAdminModules();
    await client.createAdminModule("Platform module");
    await client.getAdminModule("mod-1");
    await client.patchAdminModule("mod-1", { company_ids: ["company-1"], cabinet_ids: ["cabinet-1"] });
    await client.copyAdminModule("mod-1", "Copy");
    await client.deleteAdminModule("mod-1");
    await client.listAdminModuleMeta("mod-1");
    await client.getAdminModuleMeta("mod-1", "tabs");
    await client.putAdminModuleMeta("mod-1", "tabs", [{ title: "Admin" }]);
    await client.deleteAdminModuleMeta("mod-1", "tabs");
    await client.listAdminModuleProjects("mod-1");
    await client.listAdminModuleCabinets("mod-1");
    await client.bindAdminProjectModule("mod-1", "project-1", { bind_kind: "global", child_may_edit: false });
    await client.unbindAdminProjectModule("mod-1", "project-1");
    await client.listAdminModuleData("mod-1", "catalog");
    await client.createAdminModuleData("mod-1", "catalog", { sku: "A" });
    await client.patchAdminModuleData("mod-1", "catalog", "row-1", { sku: "B" });
    await client.deleteAdminModuleData("mod-1", "catalog", "row-1");
    await client.invokeAdminModuleAction("mod-1", "reindex", { row_id: "row-1", params: { full: true } });
    await client.getAdminProfile();
    await client.setAdminLogin("platform-admin");
    await client.setAdminPassword("long-enough-password");

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/cabinets/cabinet-1/modules", undefined],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/bound-projects", undefined],
      ["/api/v1/cabinets/cabinet-1/modules/mod-1/meta/documents/mcp_tools", undefined],
      ["/api/v1/companies/company-1/modules", undefined],
      ["/api/v1/companies/company-1/modules", "POST"],
      ["/api/v1/companies/company-1/modules/mod-1", undefined],
      ["/api/v1/companies/company-1/modules/mod-1", "PATCH"],
      ["/api/v1/companies/company-1/modules/mod-1/copy", "POST"],
      ["/api/v1/companies/company-1/modules/mod-1", "DELETE"],
      ["/api/v1/companies/company-1/modules/mod-1/projects", undefined],
      ["/api/v1/companies/company-1/modules/mod-1/cabinets", undefined],
      ["/api/v1/companies/company-1/modules/mod-1/meta/documents", undefined],
      ["/api/v1/companies/company-1/modules/mod-1/meta/documents/tabs", undefined],
      ["/api/v1/companies/company-1/modules/mod-1/meta/documents/tabs", "PUT"],
      ["/api/v1/companies/company-1/modules/mod-1/data/catalog", undefined],
      ["/api/v1/companies/company-1/modules/mod-1/data/catalog", "POST"],
      ["/api/v1/companies/company-1/modules/mod-1/data/catalog/row-1", "PATCH"],
      ["/api/v1/companies/company-1/modules/mod-1/data/catalog/row-1", "DELETE"],
      ["/api/v1/companies/company-1/modules/mod-1/actions/publish/invoke", "POST"],
      ["/api/v1/admin/modules", undefined],
      ["/api/v1/admin/modules", "POST"],
      ["/api/v1/admin/modules/mod-1", undefined],
      ["/api/v1/admin/modules/mod-1", "PATCH"],
      ["/api/v1/admin/modules/mod-1/copy", "POST"],
      ["/api/v1/admin/modules/mod-1", "DELETE"],
      ["/api/v1/admin/modules/mod-1/meta/documents", undefined],
      ["/api/v1/admin/modules/mod-1/meta/documents/tabs", undefined],
      ["/api/v1/admin/modules/mod-1/meta/documents/tabs", "PUT"],
      ["/api/v1/admin/modules/mod-1/meta/documents/tabs", "DELETE"],
      ["/api/v1/admin/modules/mod-1/projects", undefined],
      ["/api/v1/admin/modules/mod-1/cabinets", undefined],
      ["/api/v1/admin/modules/mod-1/projects/project-1", "POST"],
      ["/api/v1/admin/modules/mod-1/projects/project-1", "DELETE"],
      ["/api/v1/admin/modules/mod-1/data/catalog", undefined],
      ["/api/v1/admin/modules/mod-1/data/catalog", "POST"],
      ["/api/v1/admin/modules/mod-1/data/catalog/row-1", "PATCH"],
      ["/api/v1/admin/modules/mod-1/data/catalog/row-1", "DELETE"],
      ["/api/v1/admin/modules/mod-1/actions/reindex/invoke", "POST"],
      ["/api/v1/admin/profile", undefined],
      ["/api/v1/admin/profile/login", "PUT"],
      ["/api/v1/admin/profile/password", "PUT"],
    ]);
    expect(fetcher.mock.calls[4][1]?.body).toBe(JSON.stringify({ name: "Local module" }));
    expect(fetcher.mock.calls[6][1]?.body).toBe(JSON.stringify({ cabinet_ids: ["cabinet-1"] }));
    expect(fetcher.mock.calls[13][1]?.body).toBe(JSON.stringify({ body: [{ title: "Overview" }] }));
    expect(fetcher.mock.calls[15][1]?.body).toBe(JSON.stringify({ body: { sku: "A" } }));
    expect(fetcher.mock.calls[18][1]?.body).toBe(JSON.stringify({ row_id: "row-1", params: { dry_run: true } }));
    expect(fetcher.mock.calls[20][1]?.body).toBe(JSON.stringify({ name: "Platform module" }));
    expect(fetcher.mock.calls[22][1]?.body).toBe(JSON.stringify({ company_ids: ["company-1"], cabinet_ids: ["cabinet-1"] }));
    expect(fetcher.mock.calls[23][1]?.body).toBe(JSON.stringify({ name: "Copy" }));
    expect(fetcher.mock.calls[27][1]?.body).toBe(JSON.stringify({ body: [{ title: "Admin" }] }));
    expect(fetcher.mock.calls[31][1]?.body).toBe(JSON.stringify({ bind_kind: "global", child_may_edit: false }));
    expect(fetcher.mock.calls[34][1]?.body).toBe(JSON.stringify({ body: { sku: "A" } }));
    expect(fetcher.mock.calls[37][1]?.body).toBe(JSON.stringify({ row_id: "row-1", params: { full: true } }));
    expect(fetcher.mock.calls[39][1]?.body).toBe(JSON.stringify({ login: "platform-admin" }));
    expect(fetcher.mock.calls[40][1]?.body).toBe(JSON.stringify({ password: "long-enough-password" }));
  });

  it("keeps project attachments JSON/base64 and downloads content as a response stream", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: "attachment-1", filename: "note.txt" }, 201))
      .mockResolvedValueOnce(jsonResponse({ deleted: true }))
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new TextEncoder().encode("attachment bytes")); controller.close(); },
      }), { headers: { "Content-Type": "text/plain", "Content-Disposition": "inline; filename=note.txt" } }));
    const client = createApiClient({ fetcher });

    await client.listAttachments("project-1");
    await client.uploadAttachment("project-1", { filename: "note.txt", contentBase64: "bm90ZQ==", contentType: "text/plain" });
    await client.deleteAttachment("project-1", "attachment-1");
    const file = await client.downloadAttachment("project-1", "attachment-1");

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/projects/project-1/attachments", undefined],
      ["/api/v1/projects/project-1/attachments", "POST"],
      ["/api/v1/projects/project-1/attachments/attachment-1", "DELETE"],
      ["/api/v1/projects/project-1/attachments/attachment-1/content", undefined],
    ]);
    expect(fetcher.mock.calls[1][1]?.body).toBe(JSON.stringify({ filename: "note.txt", content_base64: "bm90ZQ==", content_type: "text/plain" }));
    expect(file).toMatchObject({ filename: "note.txt", contentType: "text/plain", size: 16 });
    expect(await file.blob.text()).toBe("attachment bytes");
    expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({ Accept: "application/octet-stream" });
  });

  it("covers project module profile settings and content asset version lifecycle", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ module_ids: ["mod-1"], items: [] }))
      .mockResolvedValueOnce(jsonResponse({ module_id: "mod-1", profile_id: "profile-1", profiles: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [{ id: "asset-1" }] }))
      .mockResolvedValueOnce(jsonResponse({ id: "asset-1" }, 201))
      .mockResolvedValueOnce(jsonResponse({ id: "asset-1" }))
      .mockResolvedValueOnce(jsonResponse({ id: "asset-1" }))
      .mockResolvedValueOnce(jsonResponse({ version: { id: "version-1", asset_id: "asset-1", version: 1 }, upload_url: "https://blob.test/put", method: "PUT" }, 201))
      .mockResolvedValueOnce(jsonResponse({ id: "version-1", asset_id: "asset-1", version: 1 }))
      .mockResolvedValueOnce(jsonResponse({ id: "version-1", asset_id: "asset-1", version: 1 }))
      .mockResolvedValueOnce(new Response("asset bytes", { headers: { "Content-Type": "text/plain", "Content-Disposition": "attachment; filename=asset.txt" } }));
    const client = createApiClient({ fetcher });

    await client.listProjectModules("project-1");
    await client.setProjectModuleProfile("project-1", "mod-1", "profile-1");
    await client.listContentAssets("company-1", 10);
    await client.createContentAsset({ owner_company_id: "company-1", title: "Asset", mime: "text/plain", visibility: "company", tags: ["test"] });
    await client.patchContentAsset("asset-1", { title: "Renamed" });
    await client.deleteContentAsset("asset-1");
    await client.beginContentAssetVersion("asset-1", { mime: "text/plain", ttl_seconds: 120 });
    await client.uploadContentAssetVersion("asset-1", "version-1", "asset bytes", "text/plain");
    await client.finalizeContentAssetVersion("asset-1", "version-1");
    const file = await client.downloadContentAsset("asset-1", { blob_version_id: "version-1", proxy: true });

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/projects/project-1/modules", undefined],
      ["/api/v1/projects/project-1/modules/mod-1/profile", "PATCH"],
      ["/api/v1/content/assets?company_id=company-1&limit=10", undefined],
      ["/api/v1/content/assets", "POST"],
      ["/api/v1/content/assets/asset-1", "PATCH"],
      ["/api/v1/content/assets/asset-1", "DELETE"],
      ["/api/v1/content/assets/asset-1/versions", "POST"],
      ["/api/v1/content/assets/asset-1/versions/version-1/upload", "PUT"],
      ["/api/v1/content/assets/asset-1/versions/version-1/finalize", "POST"],
      ["/api/v1/content/assets/asset-1/download?blob_version_id=version-1&proxy=1", undefined],
    ]);
    expect(fetcher.mock.calls[1][1]?.body).toBe(JSON.stringify({ profile_id: "profile-1" }));
    expect(fetcher.mock.calls[3][1]?.body).toBe(JSON.stringify({ owner_company_id: "company-1", title: "Asset", mime: "text/plain", visibility: "company", tags: ["test"] }));
    expect(fetcher.mock.calls[6][1]?.body).toBe(JSON.stringify({ mime: "text/plain", ttl_seconds: 120 }));
    expect(fetcher.mock.calls[7][1]?.body).toBe("asset bytes");
    expect(fetcher.mock.calls[7][1]?.headers).toMatchObject({ "Content-Type": "text/plain" });
    expect(file).toMatchObject({ filename: "asset.txt", contentType: "text/plain", size: 11 });
  });

  it("uses FormData for owner module uploads and raw bytes for content version uploads", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ file_ref: { asset_id: "asset-1" } }))
      .mockResolvedValueOnce(jsonResponse({ uploaded: true }))
      .mockResolvedValueOnce(jsonResponse({ uploaded: true }));
    const client = createApiClient({ getAccessToken: () => "token", fetcher });
    const upload = new Blob(["module file"], { type: "text/plain" });
    await client.uploadCompanyModuleContent("company-1", "mod-1", upload, "module.txt");
    await client.uploadAdminModuleContent("mod-1", upload, "module.txt");
    await client.uploadContentAssetVersion("asset-1", "version-1", upload, "text/plain");

    const companyUpload = fetcher.mock.calls[0][1]!;
    const adminUpload = fetcher.mock.calls[1][1]!;
    const assetUpload = fetcher.mock.calls[2][1]!;
    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/companies/company-1/modules/mod-1/content/upload", "POST"],
      ["/api/v1/admin/modules/mod-1/content/upload", "POST"],
      ["/api/v1/content/assets/asset-1/versions/version-1/upload", "PUT"],
    ]);
    for (const init of [companyUpload, adminUpload]) {
      expect(init.body).toBeInstanceOf(FormData);
      expect(init.headers).not.toHaveProperty("Content-Type");
      expect(init.headers).toMatchObject({ Authorization: "Bearer token" });
    }
    expect(assetUpload.body).toBe(upload);
    expect(assetUpload.headers).toMatchObject({ "Content-Type": "text/plain", Authorization: "Bearer token" });
  });

  it("consumes binary download streams and preserves the response filename and media type", async () => {
    const bytes = new Uint8Array([0, 1, 2, 255]);
    const fetcher = vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(bytes); controller.close(); },
    }), { headers: { "Content-Type": "application/octet-stream", "Content-Disposition": "attachment; filename*=UTF-8''report%20one.bin" } }));
    const client = createApiClient({ fetcher });

    const file = await client.downloadProjectModuleFile({ asset_id: "asset/1", version_id: "version/1", filename: "fallback.bin" });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/content/assets/asset%2F1/download?proxy=1&blob_version_id=version%2F1", expect.objectContaining({ headers: { Accept: "application/octet-stream" } }));
    expect([...new Uint8Array(await file.blob.arrayBuffer())]).toEqual([0, 1, 2, 255]);
    expect(file).toMatchObject({ filename: "report one.bin", contentType: "application/octet-stream", size: 4 });
  });

  it("preserves structured FastAPI errors on expanded methods", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: "cron invalid", code: "VALIDATION_ERROR", trace_id: "trace-1" }), {
      status: 422,
      statusText: "Unprocessable Entity",
      headers: { "Content-Type": "application/json" },
    }));
    const client = createApiClient({ fetcher });

    await expect(client.createProjectSchedule("project-1", { name: "Bad", cron: "not-cron" })).rejects.toMatchObject({
      error: { code: "VALIDATION_ERROR", title: "Unprocessable Entity", status: 422, detail: "cron invalid", trace_id: "trace-1" },
    } satisfies Partial<ApiRequestError>);
  });
});