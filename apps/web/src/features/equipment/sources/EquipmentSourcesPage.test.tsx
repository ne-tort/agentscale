import { renderToStaticMarkup } from "react-dom/server";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";
import { EquipmentSourcesPage } from "./EquipmentSourcesPage";
import type { EquipmentCatalogWorkspace } from "./contracts";
import type { EquipmentSourcesService } from "./service";

const service = {} as EquipmentSourcesService;

const workspace: EquipmentCatalogWorkspace = {
  module: { id: "mod_equipment", name: "Подбор техники" },
  metadata: {
    mappingFields: [
      { key: "title", label: "Название", required: true },
      { key: "price", label: "Цена", required: true },
    ],
    indexAction: { id: "index_catalog_opensearch", kind: "content.index_opensearch", enabled: true },
    probeAction: { id: "probe_catalog_remote", kind: "content.probe_remote_sql", enabled: true },
    databasesAction: { id: "list_catalog_remote_databases", kind: "content.list_remote_sql_databases", enabled: true },
    tablesAction: { id: "list_catalog_remote_tables", kind: "content.list_remote_sql_tables", enabled: false },
    pauseSupported: true,
  },
  rows: [{
    row_id: "catalog-live",
    body: {
      name: "Primary supplier",
      source_kind: "remote",
      remote_dsn: { secret_ref: "vault://module_secrets/platform/platform/dsn-1" },
      remote_password: { secret_ref: "vault://module_secrets/platform/platform/password-1" },
      remote_database: "inventory",
      remote_table: "public.items",
      remote_dsn_url_has_database: true,
      remote_dsn_has_database: true,
      columns_json: '["name", "price"]',
      column_map: { title: "name", price: "price" },
      status: "ready",
      paused: false,
      row_count: 125,
      last_indexed_at: "2000-01-01T00:00:00Z",
      reindex_interval_hours: 24,
    },
  }],
};

describe("equipment catalog source UI", () => {
  it("shows real source, mapping and lifecycle values without exposing secret references or values", () => {
    const markup = renderToStaticMarkup(<EquipmentSourcesPage initialWorkspace={workspace} service={service} />);

    expect(markup).toContain("Каталоги источников");
    expect(markup).toContain("Primary supplier");
    expect(markup).toContain("public.items");
    expect(markup).toContain("Нужно обновить");
    expect(markup).not.toContain("vault://module_secrets");
    expect(markup.toLowerCase()).not.toContain("secret-1");
    expect(markup).toContain("Сохранённый DSN не отображается");
    expect(markup).toContain("Секрет сохранён через secrets API");
    expect(markup).toContain("Переиндексировать");
  });

  it("keeps pause/resume blocked even when metadata declares a UI pause toggle", () => {
    const markup = renderToStaticMarkup(<EquipmentSourcesPage initialWorkspace={workspace} service={service} />);
    const document = new JSDOM(markup).window.document;
    const pauseButton = Array.from(document.querySelectorAll("button"))
      .find((button) => button.textContent?.trim() === "Пауза недоступна");

    expect(pauseButton?.getAttribute("role") ?? pauseButton?.localName).toBe("button");
    expect(pauseButton?.getAttribute("aria-label") ?? pauseButton?.textContent?.trim()).toBe("Пауза недоступна");
    expect(pauseButton?.disabled).toBe(true);
    expect(pauseButton?.title).toBe("API gap: отдельного pause/resume endpoint нет; catalogs PATCH запускает auto-index action");
    expect(document.body.textContent).toContain("Pause/resume заблокированы: отдельного API действия нет, а PATCH строки каталога запускает auto-index action.");
  });

  it("explains disabled probe/index actions and mapping requirements from metadata gaps", () => {
    const missingMetadata: EquipmentCatalogWorkspace = {
      ...workspace,
      metadata: { mappingFields: [], pauseSupported: false },
    };
    const markup = renderToStaticMarkup(<EquipmentSourcesPage initialWorkspace={missingMetadata} service={service} />);
    expect(markup).toContain("Metadata не объявляет schema для column_map");
    expect(markup).toContain("Переиндексация заблокирована");
    expect(markup).toContain("Действие индексации отсутствует в actions metadata");
    expect(markup).toContain("Действие отключено или не объявлено в actions metadata");
    expect(markup.toLowerCase()).not.toContain("успешно выполнено");
  });
});