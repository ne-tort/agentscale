// @vitest-environment jsdom
import { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot } from "react-dom/client";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { SettingsProvider } from "../../../preferences/SettingsProvider";
import { EquipmentSourcesPage } from "./EquipmentSourcesPage";
import type { EquipmentCatalogWorkspace } from "./contracts";
import { EquipmentSourcesRequestError, type EquipmentSourcesService } from "./service";

const service = {} as EquipmentSourcesService;

/** Копия берётся из провайдера настроек: по умолчанию экран говорит по-русски. */
function render(element: ReactElement): string {
  return renderToStaticMarkup(<SettingsProvider>{element}</SettingsProvider>);
}

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
    const markup = render(<EquipmentSourcesPage initialWorkspace={workspace} service={service} />);

    expect(markup).toContain("Каталоги источников");
    expect(markup).toContain("Primary supplier");
    expect(markup).toContain("public.items");
    expect(markup).toContain("Устарело");
    expect(markup).not.toContain("vault://module_secrets");
    expect(markup.toLowerCase()).not.toContain("secret-1");
    expect(markup).toContain("Сохранённая строка подключения не отображается");
    expect(markup).toContain("Строка подключения сохранена, значение скрыто");
    expect(markup).toContain("Переиндексировать");
  });

  it("keeps pause/resume blocked even when metadata declares a UI pause toggle", () => {
    const markup = render(<EquipmentSourcesPage initialWorkspace={workspace} service={service} />);
    const container = document.createElement("div");
    container.innerHTML = markup;
    const pauseButton = Array.from(container.querySelectorAll("button"))
      .find((candidate) => candidate.textContent?.trim() === "Пауза недоступна");

    expect(pauseButton?.getAttribute("role") ?? pauseButton?.localName).toBe("button");
    expect(pauseButton?.getAttribute("aria-label") ?? pauseButton?.textContent?.trim()).toBe("Пауза недоступна");
    expect(pauseButton?.disabled).toBe(true);
    expect(pauseButton?.title).toBe("Приостановить источник нельзя: отдельного действия для этого нет");
    expect(container.textContent).toContain("Приостановить источник отсюда нельзя");
  });

  it("explains disabled probe/index actions and mapping requirements from metadata gaps", () => {
    const missingMetadata: EquipmentCatalogWorkspace = {
      ...workspace,
      metadata: { mappingFields: [], pauseSupported: false },
    };
    const markup = render(<EquipmentSourcesPage initialWorkspace={missingMetadata} service={service} />);
    expect(markup).toContain("не описаны поля сопоставления");
    expect(markup).toContain("Переиндексация недоступна");
    expect(markup).toContain("Индексация недоступна");
    expect(markup).toContain("Часть проверок отключена");
    expect(markup.toLowerCase()).not.toContain("успешно выполнено");
  });

  it("writes the last indexed date in Russian when the interface is Russian", () => {
    const markup = render(<EquipmentSourcesPage initialWorkspace={workspace} service={service} />);

    // Строка каталога проиндексирована 2000-01-01: месяц берётся из языка
    // интерфейса, а не из локали браузера (раньше здесь был `toLocaleString()`).
    expect(markup).toMatch(/янв/i);
    expect(markup).not.toContain("Jan");
  });

  it("writes the same date in English when the interface is English", () => {
    window.localStorage.setItem("agentscale.locale", "en");
    try {
      const markup = render(<EquipmentSourcesPage initialWorkspace={workspace} service={service} />);
      expect(markup).toContain("Jan");
      expect(markup).not.toMatch(/янв/i);
    } finally {
      window.localStorage.removeItem("agentscale.locale");
    }
  });

  it("turns a service error code into a sentence and keeps the server detail as a second line", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const failing = {
      loadWorkspace: () => Promise.reject(new EquipmentSourcesRequestError("http", {
        status: 500,
        detail: "index worker unavailable",
        message: "GET /admin/modules responded 500",
      })),
    } as unknown as EquipmentSourcesService;

    try {
      await act(async () => {
        root.render(<SettingsProvider><EquipmentSourcesPage service={failing} /></SettingsProvider>);
      });
      await act(async () => { await Promise.resolve(); });

      const text = host.textContent ?? "";
      expect(text).toContain("Сервер не выполнил запрос");
      expect(text).toContain("index worker unavailable");
      // Техническое сообщение остаётся в журнале, а не на экране.
      expect(text).not.toContain("responded 500");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});