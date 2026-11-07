import { describe, expect, it } from "vitest";
import { createTranslator } from "../../i18n/catalogue";
import type { ModuleManifest, ModuleViewDefinition } from "./contracts";
import { actionResultNotice, collectModuleActionParams, moduleRowActionMatches, moduleWriteNotice, normalizeProjectSelection, parseModuleCollectionRowAction, parseModuleColumnValue, resolvePrimaryCreateRoute, sameModuleRowBody, validateModuleActionResult, validateModuleRowBody } from "./commands";

const t = createTranslator("ru");

const views: ModuleViewDefinition[] = [
  { slug: "items", table_slug: "items", kind: "collection", ui_json: { version: 1, kind: "collection", primary_action: { kind: "create_row", view: "items_form" }, row_tap: { kind: "open_form", view: "items_form" } } },
  { slug: "items_form", table_slug: "items", kind: "form", ui_json: { version: 1, kind: "form", fields: [] } },
];
const manifest: ModuleManifest = { syntax_version: 1, tables: [], columns: [], views, tabs: [], actions: [] };

describe("module collection commands", () => {
  it("parses row actions and applies typed equality and membership conditions", () => {
    const parsed = parseModuleCollectionRowAction({ action: "take_to_work", label: "Take", when: { field: "status", in: ["new", "on_hold"] } });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(moduleRowActionMatches(parsed.action, { status: "new" })).toBe(true);
      expect(moduleRowActionMatches(parsed.action, { status: "done" })).toBe(false);
    }
    expect(parseModuleCollectionRowAction({ kind: "run_script", script: "x" })).toMatchObject({ ok: false });
  });

  it("parses typed row-action fields and rejects unsupported input schemas", () => {
    expect(parseModuleCollectionRowAction({ kind: "invoke_action", action: "take_to_work", params_form: [{ name: "line_id", type: "text", required: true }, { name: "qty", type: "number" }] })).toMatchObject({ ok: true, action: { params_form: [{ name: "line_id", required: true }, { name: "qty", type: "number" }] } });
    expect(parseModuleCollectionRowAction({ kind: "invoke_action", action: "take_to_work", params_form: [{ name: "bad", type: "script" }] })).toMatchObject({ ok: false, error: expect.stringContaining("unsupported type") });
    expect(parseModuleCollectionRowAction({ kind: "invoke_action", action: "take_to_work", params_form: [{ name: "qty", type: "number" }, { name: "qty", type: "number" }] })).toMatchObject({ ok: false, error: expect.stringContaining("duplicate") });
    expect(parseModuleCollectionRowAction({ kind: "open_view", view: "items_form", params_form: [] })).toMatchObject({ ok: false });
  });

  it("collects action params as typed values and rejects missing or malformed input", () => {
    const fields = [
      { name: "line_id", type: "text" as const, required: true },
      { name: "qty", type: "number" as const },
      { name: "enabled", type: "bool" as const },
      { name: "attrs", type: "json" as const },
    ];
    const data = new FormData();
    data.set("line_id", "row-1"); data.set("qty", "3.5"); data.set("enabled", "false"); data.set("attrs", "{\"kind\":\"display\"}");
    expect(collectModuleActionParams(fields, data, t)).toEqual({ params: { line_id: "row-1", qty: 3.5, enabled: false, attrs: { kind: "display" } } });
    expect(collectModuleActionParams(fields, new FormData(), t)).toMatchObject({ error: expect.stringContaining("обязательный") });
    data.set("qty", "NaN");
    expect(collectModuleActionParams(fields, data, t)).toMatchObject({ error: expect.stringContaining("числом") });
    data.set("qty", "2"); data.set("attrs", "not-json");
    expect(collectModuleActionParams(fields, data, t)).toMatchObject({ error: expect.stringContaining("корректный JSON") });
  });

  it("parses manifest column types and validates required row values before submit", () => {
    expect(parseModuleColumnValue({ name: "qty", type: "number" }, "2.5", t)).toEqual({ value: 2.5 });
    expect(parseModuleColumnValue({ name: "qty", type: "number" }, "NaN", t).error).toContain("числом");
    expect(parseModuleColumnValue({ name: "attrs", type: "json" }, "[]", t)).toEqual({ value: [] });
    expect(parseModuleColumnValue({ name: "attrs", type: "json" }, "{} ", t)).toEqual({ value: {} });
    expect(validateModuleRowBody({}, [{ name: "name", type: "text", required: true }], t)).toContain("обязательное поле");
    expect(validateModuleRowBody({ qty: "2" }, [{ name: "qty", type: "number" }], t)).toContain("заполнено неверно");
    expect(validateModuleRowBody({ qty: 2 }, [{ name: "qty", type: "number" }], t)).toBeUndefined();
    expect(sameModuleRowBody({ nested: { a: 1, b: [2] } }, { nested: { b: [2], a: 1 } })).toBe(true);
    expect(sameModuleRowBody({ nested: { a: 1 } }, { nested: { a: 2 } })).toBe(false);
  });

  it("resolves create_row to an enabled form while retaining return context", () => {
    expect(resolvePrimaryCreateRoute(manifest, views[0], "profile-1")).toMatchObject({ route: { viewSlug: "items_form", mode: "create", contextRowId: "profile-1", returnTo: { viewSlug: "items", contextRowId: "profile-1" } } });
    expect(resolvePrimaryCreateRoute(manifest, { ...views[0], ui_json: { ...views[0].ui_json, primary_action: { kind: "create_row", view: "missing" } } })).toMatchObject({ error: expect.stringContaining("unavailable") });
    const otherTable = { ...views[1], slug: "other_form", table_slug: "other" };
    expect(resolvePrimaryCreateRoute({ ...manifest, views: [...views, otherTable] }, { ...views[0], ui_json: { ...views[0].ui_json, primary_action: { kind: "create_row", view: "other_form" } } })).toMatchObject({ error: expect.stringContaining("different table") });

  });

  it("normalizes explicit project selections and rejects ambiguous empty selections", () => {
    expect(normalizeProjectSelection("all", [], t)).toEqual({ project_ids: [] });
    expect(normalizeProjectSelection("selected", ["project-a", "project-a", "project-b"], t)).toEqual({ project_ids: ["project-a", "project-b"] });
    expect(normalizeProjectSelection("selected", [], t)).toMatchObject({ error: expect.stringContaining("хотя бы один проект") });
    expect(normalizeProjectSelection(null, [], t)).toMatchObject({ error: expect.stringContaining("все проекты") });
  });

  it("rejects unknown action result kinds, statuses, malformed results, and backend failure outcomes", () => {
    expect(() => validateModuleActionResult(null, "equipment.budget_export")).toThrow("unsupported module action result");
    expect(() => validateModuleActionResult({ kind: "other.action" }, "equipment.budget_export")).toThrow("instead of 'equipment.budget_export'");
    expect(() => validateModuleActionResult({ kind: "equipment.budget_export", status: "mystery" }, "equipment.budget_export")).toThrow("unsupported module action status");
    expect(() => validateModuleActionResult({ kind: "equipment.budget_export", status: "failed", detail: "No rows" }, "equipment.budget_export")).toThrow("No rows");
    expect(actionResultNotice(validateModuleActionResult({ kind: "equipment.budget_export", file_ref: { asset_id: "asset" } }, "equipment.budget_export"), t)).toContain("выполнено");
    expect(validateModuleActionResult({ ok: true, status: "sent" }, "equipment.mail_send")).toMatchObject({ ok: true, status: "sent" });
  });

  it("words every create/update readback outcome identically across both write surfaces", () => {
    const outcomes = ["confirmed", "missing", "unavailable", "mismatch"] as const;
    // Each outcome must stay distinguishable: collapsing them would hide whether a write is
    // confirmed, absent, unreadable, or answered with a foreign row.
    expect(new Set(outcomes.map((outcome) => moduleWriteNotice("create", outcome, t))).size).toBe(outcomes.length);
    expect(new Set(outcomes.map((outcome) => moduleWriteNotice("update", outcome, t))).size).toBe(outcomes.length);
    expect(moduleWriteNotice("create", "confirmed", t)).toContain("подтверждена сервером");
    expect(moduleWriteNotice("create", "missing", t)).toContain("не показал её в списке");
    expect(moduleWriteNotice("create", "unavailable", t)).toContain("перечитать список не удалось");
    expect(moduleWriteNotice("create", "mismatch", t)).toContain("не повторяйте операцию автоматически");
    expect(moduleWriteNotice("update", "confirmed", t)).toContain("подтверждены сервером");
    expect(moduleWriteNotice("update", "mismatch", t)).toContain("не повторяйте операцию автоматически");
  });
});
