import { describe, expect, it } from "vitest";
import { createEquipmentLocalService, EquipmentServiceError } from "./service";
import type { EquipmentWorkspace } from "./types";

const workspace: EquipmentWorkspace = {
  revision: 0,
  defaultMarginPct: 10,
  requestLines: [
    { id: "line-1", title: "Keyboard", quantity: 1, authoredBy: "agent", selectedOfferId: null },
    { id: "line-2", title: "Mouse", quantity: 1, authoredBy: "agent", selectedOfferId: null },
  ],
  candidateGroups: [
    { id: "group-1", lineId: "line-1", matchKind: "exact", aliasesPn: [], aliasesHash: [], authoredBy: "agent" },
    { id: "group-2", lineId: "line-2", matchKind: "analog", aliasesPn: [], aliasesHash: [], authoredBy: "agent" },
  ],
  offers: [
    {
      id: "offer-1",
      lineId: "line-1",
      groupId: "group-1",
      title: "Keyboard offer",
      price: { amountOriginal: 50, currency: "USD", amountRub: 4500 },
      provenance: { supplier: "Seller", source: "catalog", sourceRef: "source://seller/catalog", observedAt: "2026-11-01T10:00:00.000Z", freshness: "fresh", isStale: false },
      platformAuthoritative: true,
      manualOverrides: {},
    },
    {
      id: "offer-2",
      lineId: "line-1",
      groupId: "group-1",
      title: "Second keyboard offer",
      price: { amountOriginal: 55, currency: "USD", amountRub: 4950 },
      provenance: { supplier: "Seller", source: "catalog", sourceRef: "source://seller/catalog", observedAt: "2026-11-01T10:00:00.000Z", freshness: "fresh", isStale: false },
      platformAuthoritative: true,
      manualOverrides: {},
    },
    {
      id: "offer-stale",
      lineId: "line-2",
      groupId: "group-2",
      title: "Stale mouse offer",
      price: { amountOriginal: 20, currency: "USD", amountRub: 1800 },
      provenance: { supplier: "Seller", source: "catalog", sourceRef: "source://seller/catalog", observedAt: "2026-10-01T10:00:00.000Z", freshness: "stale", isStale: true },
      platformAuthoritative: true,
      manualOverrides: {},
    },
  ],
  supplierPolicies: [{ supplier: "Seller", marginPct: 8, deliveryRub: 500, includeDelivery: false }],
  procurementSettings: [],
  selectionAudit: [],
};

describe("equipment local service", () => {
  it("stores one explicit human selection per request line and supports replacement", async () => {
    const service = createEquipmentLocalService(workspace);

    const first = await service.selectOffer("line-1", "offer-1");
    expect(first.requestLines.find((line) => line.id === "line-1")?.selectedOfferId).toBe("offer-1");
    expect(first.selectionAudit).toHaveLength(1);

    const replaced = await service.selectOffer("line-1", "offer-2");
    expect(replaced.requestLines.find((line) => line.id === "line-1")?.selectedOfferId).toBe("offer-2");
    expect(replaced.selectionAudit).toHaveLength(2);
    expect(replaced.selectionAudit[1]?.previousOfferId).toBe("offer-1");
  });

  it("rejects stale offers before they can become a new selection", async () => {
    const service = createEquipmentLocalService(workspace);

    await expect(service.selectOffer("line-2", "offer-stale")).rejects.toMatchObject({ code: "stale_offer_blocked" });
  });

  it("rejects an offer belonging to another request line", async () => {
    const service = createEquipmentLocalService(workspace);

    let error: unknown;
    try {
      await service.selectOffer("line-2", "offer-1");
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(EquipmentServiceError);
    expect((error as EquipmentServiceError).code).toBe("offer_line_mismatch");
  });

  it("rejects a row that is not platform-authoritative", async () => {
    const service = createEquipmentLocalService({
      ...workspace,
      offers: [{ ...workspace.offers[0]!, platformAuthoritative: false }],
    });

    await expect(service.selectOffer("line-1", "offer-1")).rejects.toMatchObject({ code: "offer_not_authoritative" });
  });
  it("allows an explicit human selection to remain unresolved when the source has no price", async () => {
    const unresolvedOffer = {
      ...workspace.offers[2]!,
      id: "offer-unresolved",
      price: { amountOriginal: null, currency: null, amountRub: null },
      provenance: { ...workspace.offers[2]!.provenance, freshness: "fresh" as const, isStale: false },
    };
    const service = createEquipmentLocalService({
      ...workspace,
      requestLines: workspace.requestLines.map((line) => line.id === "line-2" ? { ...line, selectedOfferId: null } : line),
      offers: [unresolvedOffer],
    });

    const selected = await service.selectOffer("line-2", "offer-unresolved");
    expect(selected.requestLines.find((line) => line.id === "line-2")?.selectedOfferId).toBe("offer-unresolved");
  });

  it("marks procurement inputs manual and preserves seller defaults until changed", async () => {
    const service = createEquipmentLocalService(workspace);

    const initial = await service.getWorkspace();
    expect(initial.procurementSettings).toHaveLength(0);

    const updated = await service.updateProcurement("Seller", { marginPct: 15, includeDelivery: true, deliveryRub: 750 });
    const settings = updated.procurementSettings[0];
    expect(settings?.marginPct).toBe(15);
    expect(settings?.marginSource).toBe("manual");
    expect(settings?.includeDelivery).toBe(true);
    expect(settings?.deliveryRub).toBe(750);
    expect(settings?.manualFields).toEqual(expect.arrayContaining(["marginPct", "includeDelivery", "deliveryRub"]));
  });

  it("does not mutate the caller-owned workspace snapshot", async () => {
    const service = createEquipmentLocalService(workspace);

    await service.selectOffer("line-1", "offer-1");
    expect(workspace.requestLines.find((line) => line.id === "line-1")?.selectedOfferId).toBeNull();
    expect(workspace.selectionAudit).toHaveLength(0);
  });
});
