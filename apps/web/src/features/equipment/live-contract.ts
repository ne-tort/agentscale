import type { EquipmentWorkspace, ProcurementPatch } from "./types";

export type EquipmentServiceErrorCode =
  | "line_not_found" | "offer_not_found" | "offer_line_mismatch" | "offer_not_authoritative"
  | "stale_offer_blocked" | "invalid_margin" | "invalid_delivery" | "scope_unavailable"
  | "module_unavailable" | "module_read_only" | "action_unavailable" | "export_unavailable"
  | "api_contract_invalid";

export class EquipmentServiceError extends Error {
  constructor(public readonly code: EquipmentServiceErrorCode, message: string) {
    super(message);
    this.name = "EquipmentServiceError";
  }
}

export interface EquipmentWorkspaceScope {
  readonly projectId: string;
  readonly projectName: string;
  readonly cabinetId: string;
  readonly sessionId: string;
  readonly sessionTitle: string;
  readonly bindKind: "local" | "global";
  readonly childMayEdit: boolean;
}

/**
 * Why a workspace refresh did not complete. A code, because the service has no
 * translator: the screen turns it into a sentence in the reader's language.
 */
export type EquipmentWorkspaceBlocker = "refresh_failed";

export interface EquipmentSourceCatalog {
  readonly id: string;
  readonly name: string;
  readonly kind: "local" | "remote" | "unknown";
  readonly status: string;
  readonly paused: boolean;
  readonly lastIndexedAt?: string;
  readonly freshness: "fresh" | "stale" | "unknown";
  /** A code the screen translates (`catalog_missing`, `index_failed`, …), not a sentence. */
  readonly freshnessReason?: string;
}

export interface EquipmentProcurementExport {
  readonly filename: string;
  readonly mimeType: string;
  readonly blob: Blob;
  readonly rows: number;
}

export interface EquipmentProjectService {
  readonly scope?: EquipmentWorkspaceScope;
  readonly blocker?: EquipmentWorkspaceBlocker;
  getWorkspace(): Promise<EquipmentWorkspace>;
  getSourceCatalogs(): Promise<EquipmentSourceCatalog[]>;
  createRequest(title: string, partNumber: string, quantity: number): Promise<EquipmentWorkspace>;
  refreshOffers(): Promise<EquipmentWorkspace>;
  selectOffer(lineId: string, offerId: string | null): Promise<EquipmentWorkspace>;
  updateProcurement(supplier: string, patch: ProcurementPatch): Promise<EquipmentWorkspace>;
  buildExport(): Promise<EquipmentProcurementExport>;
}

export interface EquipmentWorkspaceService extends EquipmentProjectService {}
