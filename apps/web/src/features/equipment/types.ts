export type EquipmentId = string;

export type MatchKind = "exact" | "analog" | "doubt";
export type OfferFreshness = "fresh" | "stale" | "unknown";
export type SourceKind = "catalog" | "s4b" | "web" | "api" | "file" | "unknown";
export type RequestLineAuthor = "agent" | "user" | "import";
export type MarginSource = "default" | "seller" | "manual";
export type ManualProcurementField = "marginPct" | "includeDelivery" | "deliveryRub";

/** A customer requirement. This is the only line-level selection boundary. */
export interface EquipmentRequestLine {
  id: EquipmentId;
  title: string;
  partNumber?: string;
  quantity: number;
  notes?: string;
  authoredBy: "agent" | "user" | "import" | "unknown";
  selectedOfferId?: EquipmentId | null;
  /** Server-owned chat bucket that owns this request line. */
  sessionId?: EquipmentId;
}

/** Agent-authored matching intent. It contains no authoritative offer facts. */
export interface EquipmentCandidateGroup {
  id: EquipmentId;
  lineId: EquipmentId;
  matchKind: MatchKind;
  partNumber?: string;
  aliasesPn: string[];
  aliasesHash: string[];
  note?: string;
  authoredBy: "agent";
  createdAt?: string;
}

export interface EquipmentOfferPrice {
  /** Original source amount. null means the source did not provide a usable amount. */
  amountOriginal: number | null;
  /** Source currency. It is never inferred from the display amount. */
  currency: string | null;
  /** Platform conversion into RUB, if the platform has one. */
  amountRub: number | null;
}

export interface EquipmentOfferManualOverrides {
  priceOriginal?: boolean;
  priceRub?: boolean;
  title?: boolean;
  supplier?: boolean;
  fields?: string[];
}

export interface EquipmentOfferProvenance {
  supplier: string;
  source: string;
  sourceId?: string;
  sourceKind?: SourceKind;
  /** Stable catalog row hash used by the materialized offer. */
  sourceRef?: string;
  /** Catalog indexing timestamp; not an item-level offer observation time. */
  sourceIndexAt?: string;
  artifactId?: string;
  observedAt?: string;
  freshness: OfferFreshness;
  isStale: boolean;
  staleReason?: string;
  ingestionRunId?: string;
  snapshotId?: string;
  snapshotContentHash?: string;
  provenanceDigest?: string;
  evidenceId?: string;
}

/** Platform-materialized offer. Agents cannot author or mutate this shape. */
export interface EquipmentAuthoritativeOffer {
  id: EquipmentId;
  lineId: EquipmentId;
  groupId: EquipmentId;
  title: string;
  sku?: string;
  price: EquipmentOfferPrice;
  provenance: EquipmentOfferProvenance;
  available?: "in_stock" | "on_order" | "unknown";
  leadTimeDays?: number | null;
  /** False is retained only to display/quarantine malformed or agent-authored input. */
  platformAuthoritative: boolean;
  manualOverrides: EquipmentOfferManualOverrides;
}

export interface EquipmentSupplierPolicy {
  supplier: string;
  verified?: boolean;
  marginPct?: number;
  deliveryRub?: number | null;
  includeDelivery?: boolean;
}

export interface EquipmentProcurementSettings {
  supplier: string;
  rowId?: EquipmentId;
  projectSubtotalRub?: number | null;
  projectTotalRub?: number | null;
  projectMarginRub?: number | null;
  projectSelectedCount?: number;
  marginPct: number;
  marginSource: MarginSource;
  includeDelivery: boolean;
  deliveryRub: number | null;
  manualFields: ManualProcurementField[];
}

export interface EquipmentSelectionAudit {
  lineId: EquipmentId;
  offerId: EquipmentId | null;
  action: "selected" | "cleared";
  selectedAt: string;
  selectedBy: "human";
  previousOfferId?: EquipmentId | null;
}

export interface EquipmentWorkspace {
  revision: number;
  requestLines: EquipmentRequestLine[];
  candidateGroups: EquipmentCandidateGroup[];
  offers: EquipmentAuthoritativeOffer[];
  supplierPolicies: EquipmentSupplierPolicy[];
  procurementSettings: EquipmentProcurementSettings[];
  selectionAudit: EquipmentSelectionAudit[];
  defaultMarginPct: number;
  serverBudgetLines?: Array<{ lineId: EquipmentId; seller: string; quantity: number; priceRub: number | null; markupPct: number }>;
  projectTotals?: { subtotalRub: number; totalRub: number; marginRub: number; selectedCount: number };
}

export interface ProcurementPatch {
  marginPct?: number;
  includeDelivery?: boolean;
  deliveryRub?: number | null;
}

export interface ProcurementBlocker {
  code:
    | "no_selection"
    | "stale_selection"
    | "selected_offer_missing"
    | "unknown_price"
    | "invalid_price"
    | "missing_source"
    | "missing_currency"
    | "missing_observed_at"
    | "non_authoritative_offer"
    | "selection_mismatch"
    | "stale_offer"
    | "missing_delivery";
  lineId?: EquipmentId;
  offerId?: EquipmentId;
  supplier?: string;
  message: string;
}

export interface ProcurementItem {
  lineId: EquipmentId;
  lineTitle: string;
  quantity: number;
  offer: EquipmentAuthoritativeOffer;
  matchKind: MatchKind;
  lineTotalRub: number | null;
  blocker?: ProcurementBlocker;
}

export interface ProcurementSupplierSummary {
  supplier: string;
  items: ProcurementItem[];
  selectedCount: number;
  knownSubtotalRub: number | null;
  subtotalRub: number | null;
  marginPct: number;
  marginSource: MarginSource;
  marginRub: number | null;
  includeDelivery: boolean;
  deliveryRub: number | null;
  totalRub: number | null;
  manualFields: ManualProcurementField[];
  unresolvedCount: number;
}

export interface ProcurementTotals {
  requestLineCount: number;
  selectedLineCount: number;
  knownSubtotalRub: number | null;
  subtotalRub: number | null;
  marginRub: number | null;
  deliveryRub: number | null;
  totalRub: number | null;
  unresolvedCount: number;
  staleCount: number;
}

export interface ProcurementCalculation {
  suppliers: ProcurementSupplierSummary[];
  totals: ProcurementTotals;
  blockers: ProcurementBlocker[];
}

export interface EquipmentExport {
  filename: string;
  mimeType: "text/csv;charset=utf-8";
  contents: string;
  blockers: ProcurementBlocker[];
}

export function emptyEquipmentWorkspace(): EquipmentWorkspace {
  return {
    revision: 0,
    requestLines: [],
    candidateGroups: [],
    offers: [],
    supplierPolicies: [],
    procurementSettings: [],
    selectionAudit: [],
    defaultMarginPct: 10,
  };
}
