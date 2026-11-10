import { describe, expect, it } from "vitest";

import type { AdminPluginRow } from "../../api/types";
import { pluginMenuItems } from "./pluginMenu";

function row(overrides: Partial<AdminPluginRow> = {}): AdminPluginRow {
  return {
    company_id: "co_1",
    plugin_id: "hello_plugin",
    plugin_instance_id: "inst_1",
    manifest_version: "1.0.0",
    artifact_digest: "sha256:" + "a".repeat(64),
    grant_status: "active",
    enabled: true,
    ...overrides,
  } as AdminPluginRow;
}

describe("pluginMenuItems", () => {
  it("даёт пункт для установленного модуля с активным grant", () => {
    const items = pluginMenuItems([row()], "co_1");
    expect(items).toHaveLength(1);
    expect(items[0].href).toBe("/plugins/hello_plugin/co_1");
  });

  it("модуль без активного grant в меню не попадает", () => {
    // Открыл бы страницу, которая честно ответит notGranted.
    expect(pluginMenuItems([row({ grant_status: "revoked" })], "co_1")).toEqual([]);
    expect(pluginMenuItems([row({ grant_status: "" })], "co_1")).toEqual([]);
  });

  it("пустой company_id не даёт ни одного пункта", () => {
    expect(pluginMenuItems([row()], "")).toEqual([]);
  });

  it("дубли plugin_id схлопываются, порядок стабильный", () => {
    const items = pluginMenuItems([
      row({ plugin_id: "zeta" }),
      row({ plugin_id: "alpha" }),
      row({ plugin_id: "alpha" }),
    ], "co_1");
    expect(items.map((item) => item.pluginId)).toEqual(["alpha", "zeta"]);
  });

  it("ид плагина и компании экранируются в href", () => {
    const items = pluginMenuItems([row({ plugin_id: "a/b" })], "co 1");
    expect(items[0].href).toBe("/plugins/a%2Fb/co%201");
  });
});
