import { describe, expect, it } from "vitest";
import type { JsonObject } from "../../api/types";
import { createEquipmentProjectService, EquipmentServiceError, type EquipmentApiPort } from "./project-service";

/**
 * Прежний `service.test.ts` проверял локальный in-memory адаптер `createEquipmentLocalService`.
 * Того модуля больше нет: procurement читает и пишет только через project module API
 * (`mod_equipment`), поэтому тест переведён на реальный `createEquipmentProjectService`.
 * Набор проверок сохранён по смыслу — те же fail-closed гарантии (одна человеческая
 * селекция на строку, отказ для stale/неавторитетных офферов, запрет записи в read-only
 * биндинге), но теперь против того кода, который действительно исполняется.
 */
const PROJECT_ID = "project-1";
const CABINET_ID = "cabinet-1";
const SESSION_ID = "session-1";

function row(rowId: string, body: JsonObject, sessionId: string | null = SESSION_ID) {
  return { row_id: rowId, body, session_id: sessionId, created_at: "2026-11-01T10:00:00Z" };
}

interface HarnessOptions {
  readonly bindKind?: "local" | "global";
  readonly childMayEdit?: boolean;
  readonly catalogStatus?: string;
  readonly catalogIndexedAt?: string;
  readonly selectedOfferId?: string | null;
}

function harness(options: HarnessOptions = {}) {
  const calls: Array<{ action: string; rowId?: string; body?: JsonObject }> = [];
  const api: EquipmentApiPort = {
    getProject: async () => ({ id: PROJECT_ID, cabinet_id: CABINET_ID, name: "Northwind project", status: "active" }),
    getSelection: async () => ({ cabinet_id: CABINET_ID, project_id: PROJECT_ID, chat_session_id: SESSION_ID }),
    getSession: async () => ({ id: SESSION_ID, project_id: PROJECT_ID, title: "Pump procurement", status: "active" }),
    listProjectRuntimeModules: async () => ({
      items: [{
        id: "mod_equipment",
        module_id: "mod_equipment",
        name: "Equipment",
        bind_kind: options.bindKind ?? "local",
        child_may_edit: options.childMayEdit ?? true,
      }],
    }),
    getProjectRuntimeModuleMeta: async () => ({
      slug: "actions",
      body: [
        { id: "act-pipeline", kind: "equipment.pipeline", enabled: true },
        { id: "act-select", kind: "data.select_row", enabled: true },
        { id: "act-export", kind: "equipment.budget_export", enabled: true },
      ],
    }),
    listProjectRuntimeModuleData: async (_projectId, _moduleId, tableSlug) => {
      switch (tableSlug) {
        case "request_lines":
          return { items: [
            row("line-1", { title: "Industrial pump", qty: 2, selected_offer_id: "offer-1" }),
            row("line-2", { title: "Spare seal kit", qty: 1, selected_offer_id: options.selectedOfferId === undefined ? null : options.selectedOfferId }),
          ] };
        case "found_groups":
          return { items: [
            row("group-1", { line_id: "line-1", match_kind: "exact" }),
            row("group-2", { line_id: "line-2", match_kind: "analog" }),
          ] };
        case "found_offers":
          return { items: [
            row("offer-1", { line_id: "line-1", group_id: "group-1", catalog_id: "catalog-1", seller: "Northwind", title: "Pump model A", price_orig: 20, currency: "USD", price: 250 * 2, src_hash: "hash-1", in_stock: true }),
            row("offer-2", { line_id: "line-2", group_id: "group-2", catalog_id: "catalog-1", seller: "Northwind", title: "Seal kit", price_orig: 5, currency: "USD", price: 500, src_hash: "hash-2" }),
            // Не авторитетен: нет src_hash, то есть нет привязки к материализованному источнику.
            row("offer-unbacked", { line_id: "line-2", group_id: "group-2", catalog_id: "catalog-1", seller: "Northwind", title: "Unbacked seal kit", price_orig: 5, currency: "USD", price: 500 }),
          ] };
        case "catalogs":
          return { items: [row("catalog-1", {
            name: "Northwind catalog",
            source_kind: "local",
            status: options.catalogStatus ?? "ready",
            last_indexed_at: options.catalogIndexedAt ?? "2026-11-01T10:00:00Z",
          }, null)] };
        case "trusted_sellers":
          return { items: [row("seller-1", { name: "Northwind", is_verified: true, margin_pct: 8, delivery_rub: 500 }, null)] };
        case "procurement":
          return { items: [row("proc-1", { seller: "Northwind", margin_pct: 8, include_delivery: false, delivery_rub: 500, selected_count: 2, sum_rub: 500, sum_with_margin_rub: 540, sum_margin_rub: 40 })] };
        default:
          return { items: [] };
      }
    },
    createProjectRuntimeModuleData: async () => ({}),
    patchProjectRuntimeModuleData: async () => ({}),
    invokeProjectModuleAction: async (_cabinetId, _projectId, _moduleId, actionId, body) => {
      calls.push({ action: actionId, ...(typeof body?.row_id === "string" ? { rowId: body.row_id } : {}) });
      return {};
    },
    downloadProjectModuleFile: async () => ({ blob: new Blob([]), filename: "budget.xlsx", contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size: 0 }),
  };
  return { api, calls, service: createEquipmentProjectService(api, PROJECT_ID) };
}

describe("equipment project service", () => {
  it("resolves project/cabinet/chat scope instead of inferring it", async () => {
    const { service } = harness();
    const workspace = await service.getWorkspace();

    expect(service.scope).toMatchObject({ projectId: PROJECT_ID, cabinetId: CABINET_ID, sessionId: SESSION_ID, bindKind: "local" });
    expect(workspace.requestLines.map((line) => line.id)).toEqual(["line-1", "line-2"]);
    expect(workspace.selectionAudit).toEqual([]);
  });

  it("records one explicit human selection per request line through the module action", async () => {
    const { service, calls } = harness();
    const workspace = await service.getWorkspace();

    expect(workspace.requestLines.find((line) => line.id === "line-1")?.selectedOfferId).toBe("offer-1");
    await service.selectOffer("line-1", "offer-1");
    expect(calls).toContainEqual({ action: "act-select", rowId: "offer-1" });
  });

  it("rejects an offer belonging to another request line", async () => {
    const { service } = harness();

    await expect(service.selectOffer("line-2", "offer-1")).rejects.toMatchObject({ code: "offer_line_mismatch" });
  });

  it("rejects an offer that is not platform-authoritative", async () => {
    const { service } = harness();

    await expect(service.selectOffer("line-2", "offer-unbacked")).rejects.toMatchObject({ code: "offer_not_authoritative" });
  });

  it("rejects a stale source before it can become a selection", async () => {
    const { service } = harness({ catalogStatus: "error", selectedOfferId: null });

    await expect(service.selectOffer("line-2", "offer-2")).rejects.toMatchObject({ code: "stale_offer_blocked" });
  });

  it("rejects an unknown request line id rather than creating a selection", async () => {
    const { service } = harness();

    let error: unknown;
    try {
      await service.selectOffer("line-missing", "offer-1");
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(EquipmentServiceError);
    expect((error as EquipmentServiceError).code).toBe("line_not_found");
  });

  it("refuses writes when the global binding forbids child edits", async () => {
    const { service, calls } = harness({ bindKind: "global", childMayEdit: false });

    await expect(service.selectOffer("line-1", "offer-1")).rejects.toMatchObject({ code: "module_read_only" });
    await expect(service.createRequest("Pump", "P-1", 1)).rejects.toMatchObject({ code: "module_read_only" });
    await expect(service.updateProcurement("Northwind", { marginPct: 15 })).rejects.toMatchObject({ code: "module_read_only" });
    expect(calls.filter((call) => call.action === "act-select")).toEqual([]);
  });

  it("fails closed when the request line has no positive quantity", async () => {
    const { service } = harness();

    await expect(service.createRequest("  ", "P-1", 1)).rejects.toMatchObject({ code: "api_contract_invalid" });
    await expect(service.createRequest("Pump", "P-1", 0)).rejects.toMatchObject({ code: "api_contract_invalid" });
  });

  it("derives procurement totals only from selected offers and keeps delivery a manual input", async () => {
    const { service } = harness();
    const workspace = await service.getWorkspace();

    expect(workspace.procurementSettings[0]).toMatchObject({ supplier: "Northwind", marginSource: "seller", includeDelivery: false, deliveryRub: 500 });
    expect(workspace.projectTotals).toEqual({ subtotalRub: 500, totalRub: 540, marginRub: 40, selectedCount: 2 });
  });

  it("blocks export while an active-chat line is still unselected", async () => {
    const { service } = harness({ selectedOfferId: null });

    await expect(service.buildExport()).rejects.toMatchObject({ code: "export_unavailable" });
  });
});
