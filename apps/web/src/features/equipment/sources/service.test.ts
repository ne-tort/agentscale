import { describe, expect, it, vi } from "vitest";
import { createEquipmentSourcesService, EquipmentSourcesRequestError } from "./service";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const moduleRow = { id: "mod_equipment", name: "Equipment" };
const documents = [
  { slug: "views", body: [{ slug: "catalogs_settings", ui_json: { fields: [{ column: "column_map", schema: [{ key: "title", required: true }, { key: "price", required: true }] }, { column: "paused", widget: "pause_toggle" }] } }] },
  { slug: "actions", body: [
    { id: "index_action", kind: "content.index_opensearch", enabled: true, params: { table_slug: "catalogs" } },
    { id: "probe_action", kind: "content.probe_remote_sql", enabled: true, params: { table_slug: "catalogs" } },
    { id: "list_dbs", kind: "content.list_remote_sql_databases", enabled: true, params: { table_slug: "catalogs" } },
    { id: "list_tables", kind: "content.list_remote_sql_tables", enabled: false, params: { table_slug: "catalogs" } },
  ] },
];

describe("equipment sources typed adapter API contract", () => {
  it("loads the admin module catalog row, module metadata, and catalogs owner rows", async () => {
    const calls: Array<{ url: string; method: string; body?: BodyInit | null; authorization?: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input), method: init?.method ?? "GET", body: init?.body,
        authorization: (init?.headers as Record<string, string> | undefined)?.Authorization,
      });
      if (String(input).endsWith("/api/v1/admin/modules")) return json({ items: [moduleRow] });
      if (String(input).endsWith("/api/v1/admin/modules/mod_equipment")) return json(moduleRow);
      if (String(input).endsWith("/meta/documents")) return json({ items: documents });
      if (String(input).endsWith("/data/catalogs")) return json({ items: [{ row_id: "catalog-1", body: { name: "Remote", source_kind: "remote", remote_dsn: { secret_ref: "vault://module_secrets/dsn" } } }] });
      throw new Error(`Unexpected endpoint ${String(input)}`);
    });
    const service = createEquipmentSourcesService({ fetcher, getAccessToken: () => "access" });

    const loaded = await service.loadWorkspace();

    expect(loaded.module).toEqual({ id: "mod_equipment", name: "Equipment" });
    expect(loaded.rows[0]).toMatchObject({ row_id: "catalog-1", body: { name: "Remote" } });
    expect(loaded.metadata.indexAction?.id).toBe("index_action");
    expect(calls.map((call) => [call.url, call.method])).toEqual([
      ["/api/v1/admin/modules", "GET"],
      ["/api/v1/admin/modules/mod_equipment", "GET"],
      ["/api/v1/admin/modules/mod_equipment/meta/documents", "GET"],
      ["/api/v1/admin/modules/mod_equipment/data/catalogs", "GET"],
    ]);
    expect(calls.every((call) => call.authorization === "Bearer access")).toBe(true);
  });

  it("creates and patches rows through the confirmed catalogs data API", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({ row_id: "catalog-2", body: { name: "Local" } }));
    const service = createEquipmentSourcesService({ fetcher });
    await service.createCatalog({ name: "Local", source_kind: "local" });
    await service.patchCatalog("catalog/2", { name: "Updated", column_map: { title: "Name" } });
    expect(fetcher.mock.calls.map(([input, init]) => [String(input), init?.method, init?.body])).toEqual([
      ["/api/v1/admin/modules/mod_equipment/data/catalogs", "POST", JSON.stringify({ body: { name: "Local", source_kind: "local" } })],
      ["/api/v1/admin/modules/mod_equipment/data/catalogs/catalog%2F2", "PATCH", JSON.stringify({ body: { name: "Updated", column_map: { title: "Name" } } })],
    ]);
  });

  it("uploads a multipart file and requires a complete FileRef response", async () => {
    const fetcher = vi.fn(async () => json({ asset_id: "asset-1", version_id: "version-1", storage_key: "blob/key", filename: "price.csv" }));
    const service = createEquipmentSourcesService({ fetcher, getAccessToken: () => "access" });
    const ref = await service.uploadFile(new File(["Name,Price\nWidget,10"], "price.csv", { type: "text/csv" }));
    expect(ref).toMatchObject({ asset_id: "asset-1", filename: "price.csv" });
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/admin/modules/mod_equipment/content/upload");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer access");
    const invalid = createEquipmentSourcesService({ fetcher: vi.fn(async () => json({ asset_id: "asset-only" })) });
    await expect(invalid.uploadFile(new File(["x"], "x.csv"))).rejects.toMatchObject({ code: "uploadIncomplete" });
  });

  it("sends secrets to the module secrets endpoint and only returns the secret reference", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({ secret_ref: "vault://module_secrets/ref-1", secret_ref_prefix: "vault://module_secrets/" }));
    const service = createEquipmentSourcesService({ fetcher });
    await expect(service.uploadSecret("postgresql://user:secret@db/catalog", "Catalog DSN")).resolves.toEqual({ secret_ref: "vault://module_secrets/ref-1" });
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/admin/modules/mod_equipment/secrets/upload");
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify({ secret: "postgresql://user:secret@db/catalog", label: "Catalog DSN" }));
  });

  it("invokes only enabled actions declared by the loaded catalogs metadata", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      if (String(input).endsWith("/admin/modules")) return json({ items: [moduleRow] });
      if (String(input).endsWith("/admin/modules/mod_equipment")) return json(moduleRow);
      if (String(input).endsWith("/meta/documents")) return json({ items: documents });
      if (String(input).endsWith("/data/catalogs")) return json({ items: [] });
      if (String(input).endsWith("/actions/probe_action/invoke")) return json({ kind: "content.probe_remote_sql", status: "draft", row_id: "catalog-1", columns: ["Name", "Price"] });
      throw new Error(`Unexpected endpoint ${String(input)}`);
    });
    const service = createEquipmentSourcesService({ fetcher });
    await expect(service.invoke("catalog-1", "probe")).resolves.toMatchObject({ columns: ["Name", "Price"] });
    await expect(service.invoke("catalog-1", "tables")).rejects.toMatchObject({ code: "actionDisabled" });
    expect(calls.filter(([url]) => url.endsWith("/actions/probe_action/invoke")).map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/v1/admin/modules/mod_equipment/actions/probe_action/invoke", "POST", JSON.stringify({ row_id: "catalog-1" })],
    ]);
  });

  it("rejects missing module/action contracts and preserves server failures as codes", async () => {
    const missing = createEquipmentSourcesService({ fetcher: vi.fn(async () => json({ items: [] })) });
    await expect(missing.loadWorkspace()).rejects.toMatchObject({ code: "moduleMissing" });
    const failed = createEquipmentSourcesService({ fetcher: vi.fn(async () => json({ detail: "index worker unavailable" }, 503)) });
    await expect(failed.patchCatalog("catalog-1", { name: "Updated" })).rejects.toMatchObject({
      code: "http",
      status: 503,
      detail: "index worker unavailable",
    });
    await expect(failed.invoke("catalog-1", "index")).rejects.toMatchObject({ code: "http", status: 503 });
    expect(EquipmentSourcesRequestError).toBeDefined();
  });

  it("names every distinct failure with a code and keeps server detail as data", async () => {
    const offline = createEquipmentSourcesService({
      fetcher: vi.fn(async () => { throw new TypeError("Failed to fetch"); }),
    });
    await expect(offline.loadWorkspace()).rejects.toMatchObject({ code: "network" });

    const unconfirmed = createEquipmentSourcesService({
      fetcher: vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/admin/modules")
        ? json({ items: [moduleRow] })
        : json({ id: "another_module" })),
    });
    await expect(unconfirmed.loadWorkspace()).rejects.toMatchObject({ code: "moduleUnconfirmed" });

    const badList = createEquipmentSourcesService({ fetcher: vi.fn(async () => json({ unexpected: true })) });
    await expect(badList.loadWorkspace()).rejects.toMatchObject({ code: "invalidList" });

    const badRow = createEquipmentSourcesService({
      fetcher: vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/admin/modules")) return json({ items: [moduleRow] });
        if (String(input).endsWith("/admin/modules/mod_equipment")) return json(moduleRow);
        return json({ items: [] });
      }),
    });
    await expect(badRow.patchCatalog("catalog-1", { name: "x" })).rejects.toMatchObject({ code: "invalidRow" });

    const brokenJson = createEquipmentSourcesService({
      fetcher: vi.fn(async () => new Response("not json", { status: 200, headers: { "Content-Type": "application/json" } })),
    });
    await expect(brokenJson.patchCatalog("catalog-1", { name: "x" })).rejects.toMatchObject({ code: "invalidJson" });

    const uploadRejected = createEquipmentSourcesService({ fetcher: vi.fn(async () => json({ detail: "too large" }, 413)) });
    await expect(uploadRejected.uploadFile(new File(["x"], "x.csv"))).rejects.toMatchObject({ code: "uploadRejected", status: 413 });

    const noSecretRef = createEquipmentSourcesService({ fetcher: vi.fn(async () => json({ ok: true })) });
    await expect(noSecretRef.uploadSecret("postgresql://u:p@h/db", "DSN")).rejects.toMatchObject({ code: "secretIncomplete" });
  });

  it("keeps module metadata free of action declarations as an actionUndefined code", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/admin/modules")) return json({ items: [moduleRow] });
      if (String(input).endsWith("/admin/modules/mod_equipment")) return json(moduleRow);
      if (String(input).endsWith("/meta/documents")) return json({ items: [] });
      return json({ items: [] });
    });
    const service = createEquipmentSourcesService({ fetcher });
    await expect(service.invoke("catalog-1", "probe")).rejects.toMatchObject({ code: "actionUndefined" });
  });
});