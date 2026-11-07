import { describe, expect, it, vi } from "vitest";
import { createModuleRuntimeService, ModuleRuntimeRequestError } from "./service";
import { equipmentModuleFixture, invalidModuleFixture, managementModuleFixture } from "./fixtures";
import { validateManifestDocuments, validateModuleManifest } from "./manifest";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function docsFor(documentMap: Record<string, unknown>) {
  const endpoints = new Map(Object.entries(documentMap).map(([slug, document]) => [slug, jsonResponse(document)]));
  return endpoints;
}

describe("module manifest validation", () => {
  it("accepts realistic prompt/profile, file-ref and equipment seed view shapes", () => {
    // One merged module now carries the prompt/profile tables, MCP packages and file refs.
    const merged = validateManifestDocuments(managementModuleFixture.documents);
    expect(merged.ok).toBe(true);
    expect(merged.manifest?.invalid_views ?? {}).toEqual({});
    expect(merged.manifest?.tables.map((table) => table.slug)).toEqual(["prompt_profiles", "profile_settings", "prompt_paths", "mcp_packages", "files"]);
    expect(validateManifestDocuments(equipmentModuleFixture.documents).ok).toBe(true);
  });

  it("rejects unknown and malformed UI meta with an explicit repair hint", () => {
    const result = validateManifestDocuments(invalidModuleFixture);
    expect(result.ok).toBe(false);
    expect(result.error).toContain("unsupported kind");
    expect(result.repairHint).toContain("will not execute unsupported meta");
    expect(validateModuleManifest(null).error).toContain("JSON object");
  });

  it("rejects unknown fields, widgets and action identifiers rather than rendering a fallback", () => {
    const base = validateManifestDocuments(managementModuleFixture.documents).manifest!;
    const unknownWidget = structuredClone(base) as unknown as Record<string, unknown>;
    const views = unknownWidget.views as Array<Record<string, unknown>>;
    const form = views.find((view) => view.slug === "files_form")!;
    (form.ui_json as Record<string, unknown>).fields = [{ column: "name", widget: "arbitrary_html" }];
    expect(validateModuleManifest(unknownWidget).manifest?.invalid_views?.files_form).toContain("unsupported widget");

    const unknownAction = structuredClone(base) as unknown as Record<string, unknown>;
    (unknownAction.actions as Array<Record<string, unknown>>).push({ id: "files_import", kind: "content.import", enabled: true });
    const actionViews = unknownAction.views as Array<Record<string, unknown>>;
    const list = actionViews.find((view) => view.slug === "files_list")!;
    (list.ui_json as Record<string, unknown>).toolbar = [{ kind: "invoke_action", action: "hidden_privileged_action" }];
    expect(validateModuleManifest(unknownAction).manifest?.invalid_views?.files_list).toContain("unknown action");
  });
});

describe("module runtime service", () => {
  function fixtureFetcher(fixture = managementModuleFixture, overrides: Record<string, unknown> = {}) {
    const requests: Array<{ url: string; method: string; headers: Headers; body?: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);
      requests.push({ url, method: init?.method ?? "GET", headers, body: typeof init?.body === "string" ? init.body : undefined });
      if (url.endsWith(`/projects/${fixture.project.id}/runtime-modules`)) return jsonResponse(fixture.modules);
      if (url.endsWith(`/projects/${fixture.project.id}`)) return jsonResponse(overrides.project ?? fixture.project);
      if (url.includes(`/runtime-modules/${fixture.module.id}/meta/documents/`)) {
        const slug = url.split("/").at(-1) ?? "";
        return jsonResponse(overrides[`meta:${slug}`] ?? fixture.documents[slug]);
      }
      if (url.endsWith(`/cabinets/${fixture.project.cabinet_id}/modules/${fixture.module.id}/bound-projects`)) return jsonResponse({ items: [{ project_id: fixture.project.id, name: "Primary" }] });
      if (url.endsWith(`/cabinets/${fixture.project.cabinet_id}/modules/${fixture.module.id}/secrets/upload`)) return jsonResponse(overrides.secret ?? { secret_ref: "file://cabinet_secrets/test", label: "Password" });
      if (url.includes(`/cabinets/${fixture.project.cabinet_id}/modules/${fixture.module.id}/actions/`)) return jsonResponse(overrides.action ?? { kind: "equipment.budget_export", file_ref: { asset_id: "asset-2", version_id: "version-2", filename: "export.xlsx" } });
      if (url.includes("/content/assets/asset-2/download")) return new Response(new Blob(["xlsx"]), { status: 200, headers: { "Content-Disposition": 'attachment; filename="export.xlsx"' } });
      if (url.includes("/content/assets/asset-1/download")) return new Response(new Blob(["pdf"]), { status: 200 });
      if (url.endsWith(`/projects/${fixture.project.id}/modules/${fixture.module.id}/profile`)) return jsonResponse({ module_id: fixture.module.id, profile_id: "profile_default" });
      if (init?.method === "POST" && url.includes("/data/")) return jsonResponse({ row_id: "new-row", body: JSON.parse(String(init.body)).body });
      if (init?.method === "PATCH" && url.includes("/data/")) {
        const rowId = new URL(url, "http://localhost").pathname.split("/").at(-1) ?? "";
        return jsonResponse({ row_id: rowId, body: JSON.parse(String(init.body)).body });
      }
      // DELETE must be answered before the generic data-page branch below: the backend
      // returns `{deleted: true, row_id}` for a confirmed delete, not a row page.
      if (init?.method === "DELETE") {
        const rowId = new URL(url, "http://localhost").pathname.split("/").at(-1) ?? "";
        return jsonResponse({ deleted: true, row_id: rowId });
      }
      if (url.includes("/runtime-modules/") && url.includes("/data/")) {
        const page = Number(new URL(url, "http://localhost").searchParams.get("page") ?? 1);
        const dataByPage = overrides.dataByPage;
        if (Array.isArray(dataByPage)) return jsonResponse(dataByPage[page - 1] ?? { items: [], total: 0, page, page_size: 200 });
        return jsonResponse(overrides.data ?? fixture.page);
      }
      throw new Error(`unexpected endpoint ${url}`);
    });
    return { fetcher: fetcher as typeof fetch, requests };
  }

  it("loads only a module bound to this project and preserves local/global editability", async () => {
    const global = fixtureFetcher();
    const service = createModuleRuntimeService({ fetcher: global.fetcher, getAccessToken: () => "token-1" });
    const loaded = await service.loadModule(managementModuleFixture.project.id, managementModuleFixture.module.id, managementModuleFixture.sessionId);
    expect(loaded.scope).toMatchObject({ projectId: "project-beta", cabinetId: "cabinet-beta", bindKind: "local", childMayEdit: true, readOnly: false, sessionId: "session-beta" });
    // The merged module's first tab is its hub; the Files area keeps its own tab.
    expect(loaded.manifest.tabs.find((tab) => tab.view_slug === "files_list")).toMatchObject({ id: "tab_files", default_project_bind: "global" });
    expect(loaded.manifest.tabs[0]).toMatchObject({ id: "tab_management", view_slug: "management_hub" });
    expect(global.requests.every((request) => request.headers.get("Authorization") === "Bearer token-1")).toBe(true);

    const readOnlyFixture = { ...equipmentModuleFixture, modules: { items: [{ id: "mod_equipment", name: "Equipment", bind_kind: "global", child_may_edit: false }] } };
    const readonly = fixtureFetcher(readOnlyFixture);
    const readonlyService = createModuleRuntimeService({ fetcher: readonly.fetcher });
    expect((await readonlyService.loadModule(readOnlyFixture.project.id, readOnlyFixture.module.id)).scope).toMatchObject({ bindKind: "global", childMayEdit: false, readOnly: true });
  });

  it("denies cross-project module access before reading module meta", async () => {
    const { requests } = fixtureFetcher(managementModuleFixture, { data: { items: [] } });
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/projects/project-other/runtime-modules")) return jsonResponse({ items: [] });
      return jsonResponse({ cabinet_id: managementModuleFixture.project.cabinet_id });
    });
    const service = createModuleRuntimeService({ fetcher: fetcher as typeof fetch });
    await expect(service.loadModule("project-other", managementModuleFixture.module.id)).rejects.toMatchObject({ status: 404, code: "MODULE_NOT_BOUND" });
    expect(requests.some((request) => request.url.includes("/meta/documents/"))).toBe(false);
  });

  it("rejects project responses from another project", async () => {
    const { fetcher } = fixtureFetcher(managementModuleFixture, { project: { ...managementModuleFixture.project, id: "project-other" } });
    const service = createModuleRuntimeService({ fetcher });
    await expect(service.loadModule(managementModuleFixture.project.id, managementModuleFixture.module.id))
      .rejects.toMatchObject({ code: "PROJECT_MISMATCH" });
  });

  it("rejects malformed module lists, row bodies, mismatched update rows, and delete readbacks", async () => {
    const normal = fixtureFetcher(managementModuleFixture);
    const listFetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/runtime-modules")) return jsonResponse(null);
      return normal.fetcher(input, init);
    });
    const listService = createModuleRuntimeService({ fetcher: listFetcher as typeof fetch });
    await expect(listService.listProjectModules(managementModuleFixture.project.id)).rejects.toMatchObject({ code: "INVALID_MODULE_LIST" });

    const malformed = fixtureFetcher(managementModuleFixture, { data: { items: [{ row_id: "bad", body: null }] } });
    const badService = createModuleRuntimeService({ fetcher: malformed.fetcher });
    const scope = { projectId: "project-beta", cabinetId: "cabinet-beta", moduleId: "mod_management", moduleName: "Управление", bindKind: "local" as const, childMayEdit: true, readOnly: false };
    await expect(badService.listRows(scope, "files")).rejects.toMatchObject({ code: "INVALID_MODULE_ROW" });

    const mismatched = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") return jsonResponse({ row_id: "other-row", body: { name: "Other" } });
      if (init?.method === "DELETE") return jsonResponse({ deleted: true, row_id: "other-row" });
      return normal.fetcher(input, init);
    });
    const mismatchService = createModuleRuntimeService({ fetcher: mismatched as typeof fetch });
    await expect(mismatchService.patchRow(scope, "files", "file_row", { name: "Changed" })).rejects.toMatchObject({ code: "ROW_MISMATCH" });
    await expect(mismatchService.deleteRow(scope, "files", "file_row")).rejects.toMatchObject({ code: "INVALID_DELETE_RESULT" });
  });

  it("sends overfetch pagination and session scope for client-side filters", async () => {
    const { fetcher, requests } = fixtureFetcher(managementModuleFixture, { data: { items: Array.from({ length: 11 }, (_, index) => ({
      row_id: `prompt_path_${index + 1}`,
      body: { profile_id: "profile_default", name: `Tone ${index + 1}` },
    })) } });
    const service = createModuleRuntimeService({ fetcher });
    const { scope } = await service.loadModule(managementModuleFixture.project.id, managementModuleFixture.module.id, managementModuleFixture.sessionId);
    const result = await service.listRows(scope, "prompt_paths", { page: 2, pageSize: 10, search: "tone", filters: { profile_id: "profile_default" } });
    expect(result).toMatchObject({ total: 11, page: 2, page_size: 10, items: [{ row_id: "prompt_path_11" }] });
    const listRequest = requests.find((request) => request.url.includes("/data/prompt_paths?"))!;
    expect(listRequest.url).toContain("page=1");
    expect(listRequest.url).toContain("page_size=200");
    expect(listRequest.url).toContain("search=tone");
    expect(listRequest.headers.get("X-Prodavan-Session-Id")).toBe("session-beta");
  });

  it("creates, patches and deletes through runtime routes", async () => {
    const { fetcher, requests } = fixtureFetcher(managementModuleFixture);
    const service = createModuleRuntimeService({ fetcher });
    const { scope } = await service.loadModule(managementModuleFixture.project.id, managementModuleFixture.module.id, managementModuleFixture.sessionId);
    const created = await service.createRow(scope, "prompt_paths", { profile_id: "profile_default", name: "Voice" });
    const updated = await service.patchRow(scope, "prompt_paths", "prompt_path_1", { name: "Voice 2" });
    await service.deleteRow(scope, "prompt_paths", "prompt_path_1");
    expect(created).toMatchObject({ row_id: "new-row", body: { profile_id: "profile_default", name: "Voice" } });
    expect(updated).toMatchObject({ row_id: "prompt_path_1", body: { name: "Voice 2" } });
    expect(requests.filter((request) => request.url.includes("/runtime-modules/mod_management/data/")).map((request) => request.method)).toEqual(["POST", "PATCH", "DELETE"]);
  });

  it("filters project-route all-row responses before applying the requested page", async () => {
    const { fetcher, requests } = fixtureFetcher(managementModuleFixture, { data: { items: [
      { row_id: "a", body: { profile_id: "one", name: "Alpha" } },
      { row_id: "b", body: { profile_id: "two", name: "Beta" } },
      { row_id: "c", body: { profile_id: "one", name: "Charlie" } },
    ] } });
    const service = createModuleRuntimeService({ fetcher });
    const scope = { projectId: "p", cabinetId: "c", moduleId: "m", moduleName: "M", bindKind: "local" as const, childMayEdit: true, readOnly: false, sessionId: "session-filter" };
    const page = await service.listRows(scope, "rows", { page: 2, pageSize: 1, filters: { profile_id: "one" } });
    expect(page).toMatchObject({ total: 2, page: 2, page_size: 1, items: [{ row_id: "c" }] });
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toContain("page=1");
    expect(requests[0].url).toContain("page_size=200");
    expect(requests[0].headers.get("X-Prodavan-Session-Id")).toBe("session-filter");

    const pagedFetcher = fixtureFetcher(managementModuleFixture, { data: { items: [{ row_id: "os:1", body: { title: "Switch" } }], total: 7400, page: 3, page_size: 25 } });
    const virtualService = createModuleRuntimeService({ fetcher: pagedFetcher.fetcher });
    const virtual = await virtualService.listRows(scope, "master_price", { page: 3, pageSize: 25, search: "switch" });
    expect(virtual).toMatchObject({ total: 7400, page: 3, page_size: 25, items: [{ row_id: "os:1" }] });
  });

  it("aggregates every backend page before filtering and reports filtered totals", async () => {
    const firstPage = Array.from({ length: 200 }, (_, index) => ({
      row_id: `os:${index + 1}`,
      body: { selected: index === 199, name: index === 199 ? "First match" : `Row ${index + 1}` },
    }));
    const secondPage = [{ row_id: "os:201", body: { selected: true, name: "Second match" } }];
    const { fetcher, requests } = fixtureFetcher(managementModuleFixture, {
      dataByPage: [
        { items: firstPage, total: 201, page: 1, page_size: 200 },
        { items: secondPage, total: 201, page: 2, page_size: 200 },
      ],
    });
    const service = createModuleRuntimeService({ fetcher });
    const scope = { projectId: "p", cabinetId: "c", moduleId: "m", moduleName: "M", bindKind: "local" as const, childMayEdit: true, readOnly: false };
    const result = await service.listRows(scope, "master_price", { page: 2, pageSize: 1, filters: { selected: true } });
    expect(result).toMatchObject({ total: 2, page: 2, page_size: 1, items: [{ row_id: "os:201" }] });
    const dataRequests = requests.filter((request) => request.url.includes("/data/master_price?"));
    expect(dataRequests).toHaveLength(2);
    expect(dataRequests[0].url).toContain("page=1");
    expect(dataRequests[1].url).toContain("page=2");
    expect(dataRequests.every((request) => request.url.includes("page_size=200"))).toBe(true);
  });

  it("returns empty filtered results and rejects malformed pagination metadata", async () => {
    const { fetcher } = fixtureFetcher(managementModuleFixture, {
      data: { items: [], total: 0, page: 1, page_size: 200 },
    });
    const service = createModuleRuntimeService({ fetcher });
    const scope = { projectId: "p", cabinetId: "c", moduleId: "m", moduleName: "M", bindKind: "local" as const, childMayEdit: true, readOnly: false };
    const empty = await service.listRows(scope, "master_price", { page: 1, pageSize: 25, filters: { selected: true } });
    expect(empty).toEqual({ items: [], total: 0, page: 1, page_size: 25 });

    const malformed = fixtureFetcher(managementModuleFixture, {
      data: { items: [], total: 0, page: 1 },
    });
    const malformedService = createModuleRuntimeService({ fetcher: malformed.fetcher });
    await expect(malformedService.listRows(scope, "master_price", { filters: { selected: true } }))
      .rejects.toMatchObject({ code: "INVALID_MODULE_PAGE" });
  });

  it("fails closed when a paged backend response ends before its declared total", async () => {
    const { fetcher } = fixtureFetcher(managementModuleFixture, {
      dataByPage: [
        { items: Array.from({ length: 200 }, (_, index) => ({ row_id: `os:${index + 1}`, body: { selected: true } })), total: 201, page: 1, page_size: 200 },
        { items: [], total: 201, page: 2, page_size: 200 },
      ],
    });
    const service = createModuleRuntimeService({ fetcher });
    const scope = { projectId: "p", cabinetId: "c", moduleId: "m", moduleName: "M", bindKind: "local" as const, childMayEdit: true, readOnly: false };
    await expect(service.listRows(scope, "master_price", { filters: { selected: true } }))
      .rejects.toMatchObject({ code: "INVALID_MODULE_PAGE" });
  });

  it("does not permit mutations through global read-only bindings but allows project actions", async () => {
    const fixture = { ...equipmentModuleFixture, modules: { items: [{ id: "mod_equipment", name: "Equipment", bind_kind: "global", child_may_edit: false }] } };
    const { fetcher, requests } = fixtureFetcher(fixture);
    const service = createModuleRuntimeService({ fetcher });
    const { scope } = await service.loadModule(fixture.project.id, fixture.module.id, fixture.sessionId);
    await expect(service.createRow(scope, "request_lines", { title: "forbidden" })).rejects.toMatchObject({ status: 403, code: "MODULE_READ_ONLY" });
    await service.invokeAction(scope, "budget_export", { rowId: "budget-1", params: { format: "xlsx" } });
    const action = requests.find((request) => request.url.includes("/actions/budget_export/invoke"))!;
    expect(action.method).toBe("POST");
    expect(JSON.parse(action.body ?? "{}")).toEqual({ project_id: fixture.project.id, row_id: "budget-1", params: { format: "xlsx" } });
  });

  it("downloads FileRef action results through the proxy asset endpoint", async () => {
    const { fetcher, requests } = fixtureFetcher(equipmentModuleFixture);
    const service = createModuleRuntimeService({ fetcher });
    const scope = (await service.loadModule(equipmentModuleFixture.project.id, equipmentModuleFixture.module.id)).scope;
    const result = await service.invokeAction(scope, "budget_export");
    expect(result).toMatchObject({ kind: "equipment.budget_export", file_ref: { asset_id: "asset-2" } });
    const file = await service.downloadFile(scope, result.file_ref!);
    expect(file.filename).toBe("export.xlsx");
    expect(requests.some((request) => request.url.includes("/content/assets/asset-2/download?proxy=1&blob_version_id=version-2"))).toBe(true);
  });

  it("rejects malformed bound-project options rather than silently dropping entries", async () => {
    const fixture = fixtureFetcher(managementModuleFixture);
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/bound-projects")) return jsonResponse({ items: [{ project_id: "project-beta", name: "Beta" }, { project_id: "missing-name" }] });
      return fixture.fetcher(input, init);
    });
    const service = createModuleRuntimeService({ fetcher: fetcher as typeof fetch });
    const scope = { projectId: "project-beta", cabinetId: "cabinet-beta", moduleId: "mod_management", moduleName: "Управление", bindKind: "local" as const, childMayEdit: true, readOnly: false };
    await expect(service.listBoundProjects(scope)).rejects.toMatchObject({ code: "INVALID_PROJECT_OPTIONS" });
  });

  it("denies file upload through read-only bindings before making a request", async () => {
    const { fetcher, requests } = fixtureFetcher();
    const service = createModuleRuntimeService({ fetcher });
    const scope = { projectId: "p", cabinetId: "c", moduleId: "m", moduleName: "M", bindKind: "global" as const, childMayEdit: false, readOnly: true };
    await expect(service.uploadContent(scope, new File(["x"], "secret.txt"))).rejects.toMatchObject({ status: 403, code: "MODULE_READ_ONLY" });
    expect(requests).toHaveLength(0);
  });

  it("rejects a missing project cabinet bind and malformed row payload", async () => {
    const noCabinet = fixtureFetcher(managementModuleFixture, { "cabinet:none": {} });
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/projects/project-beta")) return jsonResponse({ id: "project-beta" });
      return noCabinet.fetcher(input, init);
    });
    const service = createModuleRuntimeService({ fetcher: fetcher as typeof fetch });
    await expect(service.loadModule("project-beta", "mod_management")).rejects.toMatchObject({ code: "INVALID_PROJECT_BINDING" });
    const badRow = fixtureFetcher(managementModuleFixture, { data: { items: [{ id: "missing-row-id" }] } });
    const badService = createModuleRuntimeService({ fetcher: badRow.fetcher });
    const scope = { projectId: "project-beta", cabinetId: "cabinet-beta", moduleId: "mod_management", moduleName: "Управление", bindKind: "local" as const, childMayEdit: true, readOnly: false };
    await expect(badService.listRows(scope, "files")).rejects.toBeInstanceOf(ModuleRuntimeRequestError);
  });
});
