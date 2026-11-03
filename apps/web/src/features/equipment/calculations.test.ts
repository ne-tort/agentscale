import { describe, expect, it } from "vitest";
import { buildProcurementExport, calculateProcurement } from "./calculations";
import type { EquipmentWorkspace } from "./types";

const baseWorkspace: EquipmentWorkspace = {
  revision: 0,
  defaultMarginPct: 10,
  requestLines: [
    {
      id: "line-1",
      title: "24-inch monitor",
      partNumber: "MON-24",
      quantity: 2,
      authoredBy: "agent",
      selectedOfferId: "offer-1",
    },
    {
      id: "line-2",
      title: "USB-C dock",
      quantity: 1,
      authoredBy: "user",
      selectedOfferId: null,
    },
  ],
  candidateGroups: [
    {
      id: "group-1",
      lineId: "line-1",
      matchKind: "exact",
      partNumber: "MON-24",
      aliasesPn: [],
      aliasesHash: [],
      authoredBy: "agent",
      note: "Exact part number match from the request.",
    },
    {
      id: "group-2",
      lineId: "line-2",
      matchKind: "doubt",
      aliasesPn: ["DOCK-ALT"],
      aliasesHash: ["hash-dock"],
      authoredBy: "agent",
      note: "Needs human review.",
    },
  ],
  offers: [
    {
      id: "offer-1",
      lineId: "line-1",
      groupId: "group-1",
      title: "Monitor 24",
      sku: "MON-24",
      price: { amountOriginal: 100, currency: "USD", amountRub: 9000 },
      provenance: {
        supplier: "Supplier A",
        source: "catalog-a",
        sourceKind: "catalog",
        sourceRef: "source://catalog-a/run-1",
        observedAt: "2026-11-01T10:00:00.000Z",
        freshness: "fresh",
        isStale: false,
        ingestionRunId: "run-1",
        snapshotId: "snapshot-1",
      },
      available: "in_stock",
      platformAuthoritative: true,
      manualOverrides: { fields: ["title"] },
    },
    {
      id: "offer-stale",
      lineId: "line-1",
      groupId: "group-1",
      title: "Monitor 24 (stale)",
      price: { amountOriginal: 90, currency: "USD", amountRub: 8100 },
      provenance: {
        supplier: "Supplier B",
        source: "web-b",
        sourceKind: "web",
        sourceRef: "https://supplier-b.example/item",
        observedAt: "2026-10-01T10:00:00.000Z",
        freshness: "stale",
        isStale: true,
        staleReason: "Source item disappeared during reconciliation",
      },
      platformAuthoritative: true,
      manualOverrides: {},
    },
    {
      id: "offer-unknown-price",
      lineId: "line-2",
      groupId: "group-2",
      title: "USB-C dock alternative",
      price: { amountOriginal: null, currency: null, amountRub: null },
      provenance: {
        supplier: "Supplier C",
        source: "catalog-c",
        sourceKind: "file",
        sourceRef: "source://catalog-c/run-3",
        observedAt: "2026-11-01T10:00:00.000Z",
        freshness: "fresh",
        isStale: false,
      },
      platformAuthoritative: true,
      manualOverrides: {},
    },
  ],
  supplierPolicies: [{ supplier: "Supplier A", verified: true, marginPct: 12 }],
  procurementSettings: [],
  selectionAudit: [],
};

describe("equipment procurement calculations", () => {
  it("keeps exact/analog/doubt as group intent and aggregates only explicit selections", () => {
    const calculation = calculateProcurement(baseWorkspace);

    expect(calculation.suppliers).toHaveLength(1);
    expect(calculation.suppliers[0]?.supplier).toBe("Supplier A");
    expect(calculation.suppliers[0]?.subtotalRub).toBe(18000);
    expect(calculation.suppliers[0]?.marginRub).toBe(2160);
    expect(calculation.totals.selectedLineCount).toBe(1);
    expect(calculation.totals.requestLineCount).toBe(2);
    expect(calculation.blockers.some((blocker) => blocker.code === "no_selection")).toBe(true);
  });

  it("never treats an unknown source price as zero", () => {
    const workspace: EquipmentWorkspace = {
      ...baseWorkspace,
      requestLines: baseWorkspace.requestLines.map((line) => line.id === "line-2" ? { ...line, selectedOfferId: "offer-unknown-price" } : line),
    };
    const calculation = calculateProcurement(workspace);

    expect(calculation.blockers.some((blocker) => blocker.code === "unknown_price")).toBe(true);
    expect(calculation.suppliers.find((supplier) => supplier.supplier === "Supplier C")?.subtotalRub).toBeNull();
    expect(calculation.suppliers.find((supplier) => supplier.supplier === "Supplier C")?.totalRub).toBeNull();
    expect(calculation.totals.totalRub).toBeNull();
  });

  it("keeps stale offers visible as blockers and does not erase their last known price", () => {
    const workspace: EquipmentWorkspace = {
      ...baseWorkspace,
      requestLines: baseWorkspace.requestLines.map((line) => line.id === "line-1" ? { ...line, selectedOfferId: "offer-stale" } : line),
    };
    const calculation = calculateProcurement(workspace);

    expect(calculation.totals.staleCount).toBe(1);
    expect(calculation.blockers.some((blocker) => blocker.code === "stale_selection")).toBe(true);
    expect(calculation.suppliers[0]?.items[0]?.offer.price.amountRub).toBe(8100);
    expect(calculation.suppliers[0]?.totalRub).toBeNull();
  });

  it("exports provenance and warnings without inventing missing values", () => {
    const exported = buildProcurementExport(baseWorkspace);

    expect(exported.filename).toBe("equipment-procurement.csv");
    expect(exported.contents).toContain("price_original,currency,price_rub");
    expect(exported.contents).toContain("100,USD,9000");
    expect(exported.contents).toContain("Supplier A");
    expect(exported.contents).toContain("source://catalog-a/run-1");
    expect(exported.contents).toContain("budget_subtotal_rub");
    expect(exported.contents).toContain("not_selected");
    expect(exported.contents).not.toContain("offer-unknown-price,USB-C dock alternative,Supplier C");
  });
});
