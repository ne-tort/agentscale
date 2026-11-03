import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EquipmentProcurementPage } from "./EquipmentProcurementPage";
import { emptyEquipmentWorkspace, type EquipmentWorkspace } from "./types";
import type { EquipmentCatalogWorkspace } from "./sources/contracts";
import type { EquipmentSourcesService } from "./sources/service";

const sourceWorkspace: EquipmentCatalogWorkspace = {
  module: { id: "mod_equipment", name: "Equipment catalogs" },
  metadata: { mappingFields: [], pauseSupported: false },
  rows: [{
    row_id: "supplier-catalog-1",
    body: {
      name: "Northwind catalog",
      source_kind: "remote",
      status: "error",
      last_indexed_at: "2026-10-30T10:00:00Z",
      reindex_interval_hours: 24,
    },
  }],
};

const selectedWorkspace: EquipmentWorkspace = {
  ...emptyEquipmentWorkspace(),
  requestLines: [{ id: "req-selected", title: "Industrial pump", quantity: 2, authoredBy: "user", selectedOfferId: "offer-selected" }],
  candidateGroups: [{ id: "group-selected", lineId: "req-selected", matchKind: "exact", aliasesPn: [], aliasesHash: [], authoredBy: "agent" }],
  offers: [{
    id: "offer-selected",
    lineId: "req-selected",
    groupId: "group-selected",
    title: "Pump model A",
    price: { amountOriginal: 20, currency: "USD", amountRub: 250 },
    provenance: { supplier: "Northwind", source: "catalog", sourceId: "source-1", sourceRef: "catalog-row-1", observedAt: "2026-11-01T10:00:00Z", freshness: "fresh", isStale: false },
    platformAuthoritative: true,
    manualOverrides: {},
  }],
};

describe("equipment workspace integration", () => {
  it("shows procurement navigation and makes missing project scope, freshness, selections, and budget explicit", () => {
    const workspace = {
      ...emptyEquipmentWorkspace(),
      requestLines: [{ id: "req-1", title: "Industrial pump", quantity: 2, authoredBy: "user" as const, selectedOfferId: null }],
    };
    const markup = renderToStaticMarkup(<EquipmentProcurementPage initialWorkspace={workspace} />);

    expect(markup).toContain("Procurement workflow");
    expect(markup).toContain("Source configuration");
    expect(markup).toContain("Requests");
    expect(markup).toContain("Candidate groups");
    expect(markup).toContain("Offers &amp; review");
    expect(markup).toContain("Procurement / budget");
    expect(markup).toContain("Tenant / cabinet / project context not exposed");
    expect(markup).toContain("0/1 selected");
    expect(markup).toContain("1 open review blocker");
  });

  it("presents supplied source data as read-only and never promotes catalog status to offer evidence", () => {
    const markup = renderToStaticMarkup(<EquipmentProcurementPage initialWorkspaceView="sources" initialSourcesWorkspace={sourceWorkspace} />);

    expect(markup).toContain("Northwind catalog");
    expect(markup).toContain("read-only snapshot");
    expect(markup).toContain("Последняя попытка завершилась ошибкой");
    expect(markup).toContain("not authoritative offers");
    expect(markup).toContain("Authenticated adapter not mounted");
    expect(markup).not.toContain("Select this offer");
    expect(markup).not.toContain("Reindex");
  });

  it("keeps selected-line totals and observed freshness visible in the procurement context", () => {
    const markup = renderToStaticMarkup(<EquipmentProcurementPage initialWorkspace={selectedWorkspace} />);

    expect(markup).toContain("1/1 selected");
    expect(markup).toContain("1 fresh · 0 stale · 0 unknown");
    expect(markup).toContain("RUB");
    expect(markup).toContain("0 open review blockers");
    expect(markup).toContain("Local typed service");
  });

  it("mounts the existing source configuration screen only with its typed service", () => {
    const service = {} as EquipmentSourcesService;
    const markup = renderToStaticMarkup(<EquipmentProcurementPage initialWorkspaceView="sources" initialSourcesWorkspace={sourceWorkspace} sourcesService={service} />);

    expect(markup).toContain("Загрузка catalog rows и metadata");
    expect(markup).toContain("API service supplied");
    expect(markup).not.toContain("Authenticated adapter not mounted");
    expect(markup).not.toContain("Northwind catalog");
  });
});