import { describe, expect, it } from "vitest";
import {
  catalogFreshness,
  catalogMappingReady,
  catalogSourceColumns,
  parseEquipmentCatalogMetadata,
  sanitizeCatalogMessage,
  secretRef,
  type EquipmentCatalogMetaDocument,
} from "./contracts";

const documents: EquipmentCatalogMetaDocument[] = [
  {
    slug: "views",
    body: [{
      slug: "catalogs_settings",
      ui_json: { fields: [
        { column: "column_map", schema: [
          { key: "title", label: { ru: "Название" }, required: true },
          { key: "price", label: { ru: "Цена" }, required: true },
          { key: "brand", label: "Brand", required: false },
        ] },
        { column: "paused", widget: "pause_toggle" },
      ] },
    }],
  },
  {
    slug: "actions",
    body: [
      { id: "index-catalog", kind: "content.index_opensearch", enabled: true, params: { table_slug: "catalogs" } },
      { id: "probe-catalog", kind: "content.probe_remote_sql", enabled: true, params: { table_slug: "catalogs" } },
      { id: "list-databases", kind: "content.list_remote_sql_databases", enabled: true, params: { table_slug: "catalogs" } },
      { id: "list-tables", kind: "content.list_remote_sql_tables", enabled: false, params: { table_slug: "catalogs" } },
      { id: "unrelated", kind: "content.probe_remote_sql", enabled: true, params: { table_slug: "other" } },
    ],
  },
];

describe("equipment catalog contracts", () => {
  it("derives action availability, pause metadata, and required mapping keys from catalogs metadata", () => {
    expect(parseEquipmentCatalogMetadata(documents)).toMatchObject({
      mappingFields: [
        { key: "title", label: "Название", required: true },
        { key: "price", label: "Цена", required: true },
        { key: "brand", label: "Brand", required: false },
      ],
      indexAction: { id: "index-catalog", enabled: true },
      probeAction: { id: "probe-catalog", enabled: true },
      databasesAction: { id: "list-databases", enabled: true },
      tablesAction: { id: "list-tables", enabled: false },
      pauseSupported: true,
    });
  });

  it("does not invent actions or mapping configuration when metadata is absent", () => {
    expect(parseEquipmentCatalogMetadata([])).toEqual({ mappingFields: [], pauseSupported: false });
    expect(catalogMappingReady({ title: "Name", price: "Price" }, [])).toBe(false);
  });

  it("parses schema columns without fabricating or duplicating fields", () => {
    expect(catalogSourceColumns({ columns_json: '["SKU", "Price", "SKU", 7]' })).toEqual(["SKU", "Price"]);
    expect(catalogSourceColumns({ columns_json: "invalid" })).toEqual([]);
    expect(catalogMappingReady({ title: "Name", price: "Price", brand: "" }, parseEquipmentCatalogMetadata(documents).mappingFields)).toBe(true);
    expect(catalogMappingReady({ title: "Name", price: " " }, parseEquipmentCatalogMetadata(documents).mappingFields)).toBe(false);
  });

  it("warns for stale/error/unknown freshness and retains the last successful time", () => {
    const now = Date.parse("2026-11-02T12:00:00Z");
    expect(catalogFreshness({ source_kind: "remote", status: "ready", last_indexed_at: "2026-11-02T11:30:00Z", reindex_interval_hours: 1 }, now).freshness).toBe("fresh");
    expect(catalogFreshness({ source_kind: "remote", status: "error", last_indexed_at: "2026-11-01T10:00:00Z" }, now)).toMatchObject({ freshness: "stale", lastGoodLabel: expect.any(String) });
    expect(catalogFreshness({ source_kind: "remote", status: "ready" }, now)).toMatchObject({ freshness: "unknown", reason: "never-indexed" });
  });

  it("accepts only nonempty secret references and sanitizes credentials from API errors", () => {
    expect(secretRef({ secret_ref: "vault://module_secrets/secret-1" })).toEqual({ secret_ref: "vault://module_secrets/secret-1" });
    expect(secretRef("postgresql://user:password@host/db")).toBeUndefined();
    expect(sanitizeCatalogMessage("connection failed postgresql://alice:s3cr3t@db.internal/catalog")).toBe("connection failed postgresql://***@db.internal/catalog");
    expect(sanitizeCatalogMessage("secret file://cabinet_secrets/abc")).toContain("[секрет скрыт]");
    expect(sanitizeCatalogMessage('{"secret_ref":"vault://module_secrets/platform/platform/id"}')).not.toContain("module_secrets/platform/platform/id");
  });
});