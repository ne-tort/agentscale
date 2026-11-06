import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EquipmentProcurementView } from "./EquipmentProcurementPage";
import { emptyEquipmentWorkspace, type EquipmentWorkspace } from "./types";
import type { EquipmentProjectService, EquipmentWorkspaceScope } from "./live-contract";
import type { EquipmentCatalogWorkspace } from "./sources/contracts";
import type { EquipmentSourcesService } from "./sources/service";

/** Сервис через renderToStaticMarkup не вызывается: снимок приходит пропом. */
const service: EquipmentProjectService = {
  getWorkspace: () => Promise.reject(new Error("snapshot render must not load the workspace")),
  getSourceCatalogs: () => Promise.reject(new Error("snapshot render must not load catalogs")),
  createRequest: () => Promise.reject(new Error("snapshot render must not mutate")),
  refreshOffers: () => Promise.reject(new Error("snapshot render must not mutate")),
  selectOffer: () => Promise.reject(new Error("snapshot render must not mutate")),
  updateProcurement: () => Promise.reject(new Error("snapshot render must not mutate")),
  buildExport: () => Promise.reject(new Error("snapshot render must not export")),
};

const localScope: EquipmentWorkspaceScope = {
  projectId: "project-1",
  projectName: "Northwind project",
  cabinetId: "cabinet-1",
  sessionId: "session-1",
  sessionTitle: "Pump procurement",
  bindKind: "local",
  childMayEdit: true,
};

const readOnlyScope: EquipmentWorkspaceScope = { ...localScope, bindKind: "global", childMayEdit: false };

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
    provenance: {
      supplier: "Northwind",
      source: "catalog",
      sourceId: "source-1",
      sourceRef: "catalog-row-1",
      sourceIndexAt: "2026-10-30T10:00:00Z",
      observedAt: "2026-11-01T10:00:00Z",
      freshness: "fresh",
      isStale: false,
    },
    platformAuthoritative: true,
    manualOverrides: {},
  }],
};

describe("equipment workspace integration", () => {
  it("shows procurement navigation and makes missing selections and budget explicit", () => {
    const workspace: EquipmentWorkspace = {
      ...emptyEquipmentWorkspace(),
      requestLines: [{ id: "req-1", title: "Industrial pump", quantity: 2, authoredBy: "user", selectedOfferId: null }],
    };
    const markup = renderToStaticMarkup(<EquipmentProcurementView heading="Equipment procurement" initialScope={localScope} initialWorkspace={workspace} service={service} />);

    expect(markup).toContain("Procurement workflow");
    expect(markup).toContain("Source configuration");
    expect(markup).toContain("Requests");
    expect(markup).toContain("Candidate groups");
    expect(markup).toContain("Offers &amp; review");
    expect(markup).toContain("Procurement / budget");
    expect(markup).toContain("Northwind project");
    expect(markup).toContain("0/1 selected");
    expect(markup).toContain("Needs selection");
  });

  it("presents supplied source data as a read-only snapshot without an admin service", () => {
    // Source configuration открывается только после того, как загружен workspace:
    // без снимка workspace экран честно остаётся в состоянии загрузки.
    const markup = renderToStaticMarkup(<EquipmentProcurementView heading="Equipment procurement" initialScope={localScope} initialSourcesWorkspace={sourceWorkspace} initialWorkspace={emptyEquipmentWorkspace()} initialWorkspaceView="sources" service={service} />);

    expect(markup).toContain("Northwind catalog");
    expect(markup).not.toContain("Select this offer");
    // Без admin-сервиса запись не изобретается: экран остаётся снимком.
    expect(markup).not.toContain("Создать источник");
  });

  it("keeps selected-line totals and freshness visible in the procurement context", () => {
    const markup = renderToStaticMarkup(<EquipmentProcurementView heading="Equipment procurement" initialScope={localScope} initialWorkspace={selectedWorkspace} service={service} />);

    expect(markup).toContain("1/1 selected");
    expect(markup).toContain("1 source-current · 0 stale · 0 freshness unknown");
    expect(markup).toContain("RUB");
  });

  it("marks every writable stage read-only when the binding forbids child edits", () => {
    const markup = renderToStaticMarkup(<EquipmentProcurementView heading="Equipment procurement" initialScope={readOnlyScope} initialWorkspace={selectedWorkspace} service={service} />);

    expect(markup).toContain("Read-only binding");
    expect(markup).toContain("Project binding is read-only");
  });

  it("mounts the existing source configuration screen only with its typed service", () => {
    const sourcesService = {} as EquipmentSourcesService;
    const markup = renderToStaticMarkup(<EquipmentProcurementView heading="Equipment procurement" initialScope={localScope} initialSourcesWorkspace={sourceWorkspace} initialWorkspace={emptyEquipmentWorkspace()} initialWorkspaceView="sources" service={service} sourcesService={sourcesService} />);

    expect(markup).toContain("Каталоги источников");
    expect(markup).not.toContain("Northwind catalog");
  });
});
