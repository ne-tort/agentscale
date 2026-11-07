import {
  type EquipmentAuthoritativeOffer,
  type EquipmentCandidateGroup,
  type EquipmentExport,
  type EquipmentId,
  type EquipmentOfferPrice,
  type EquipmentProcurementSettings,
  type EquipmentWorkspace,
  type MatchKind,
  type ProcurementBlocker,
  type ProcurementCalculation,
  type ProcurementItem,
  type ProcurementSupplierSummary,
} from "./types";

/** Сумма в валюте платформы; `locale` задаёт вид чисел, а не текст подписи. */
export function formatEquipmentMoney(amount: number | null | undefined, currency = "RUB", locale = "ru"): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return "Price unavailable";
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount.toLocaleString(locale)} ${currency}`;
  }
}

export function formatObservedAt(value?: string): string {
  if (!value) return "Observed time unavailable";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

export function matchKindLabel(kind: MatchKind): string {
  return kind === "exact" ? "Exact" : kind === "analog" ? "Analog" : "Doubt";
}

export function priceIsKnown(price: EquipmentOfferPrice): boolean {
  return price.amountRub !== null && Number.isFinite(price.amountRub);
}

function offerHasSource(offer: EquipmentAuthoritativeOffer): boolean {
  return offer.provenance.supplier.trim().length > 0
    && offer.provenance.source.trim().length > 0
    && Boolean(offer.provenance.sourceRef?.trim());
}

function findMatchKind(
  offer: EquipmentAuthoritativeOffer,
  groups: EquipmentCandidateGroup[],
): MatchKind {
  return groups.find((group) => group.id === offer.groupId)?.matchKind ?? "doubt";
}

function selectedOfferForLine(workspace: EquipmentWorkspace, lineId: EquipmentId): EquipmentAuthoritativeOffer | undefined {
  const line = workspace.requestLines.find((item) => item.id === lineId);
  if (!line?.selectedOfferId) return undefined;
  return workspace.offers.find((offer) => offer.id === line.selectedOfferId);
}

function blockerForItem(
  lineId: EquipmentId,
  offer: EquipmentAuthoritativeOffer,
  quantity: number,
): ProcurementBlocker | undefined {
  if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") {
    return {
      code: offer.provenance.isStale ? "stale_selection" : "stale_offer",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: "Selection needs a fresh platform observation before procurement.",
    };
  }
  if (!offer.platformAuthoritative) {
    return {
      code: "non_authoritative_offer",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier || undefined,
      message: "This row is visible for review but was not materialized by the platform.",
    };
  }
  if (!offerHasSource(offer)) {
    return {
      code: "missing_source",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier || undefined,
      message: "Supplier, source, and source reference are required before this offer can enter a budget.",
    };
  }
  const observedAt = offer.provenance.observedAt;
  if (!observedAt?.trim() || !Number.isFinite(Date.parse(observedAt))) {
    return {
      code: "missing_observed_at",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier || undefined,
      message: "Observed time is required to establish offer freshness.",
    };
  }
  if (offer.price.amountRub === null) {
    return {
      code: "unknown_price",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: "The platform has no RUB conversion for this source price; total is unresolved.",
    };
  }
  if (offer.price.amountOriginal === null || !offer.price.currency?.trim()) {
    return {
      code: "missing_currency",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: "The original source amount and its currency are required; total cannot be trusted.",
    };
  }
  if (offer.price.amountOriginal !== null && (!Number.isFinite(offer.price.amountOriginal) || offer.price.amountOriginal < 0)) {
    return {
      code: "invalid_price",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: "The original source amount is invalid.",
    };
  }
  if (!Number.isFinite(offer.price.amountRub) || offer.price.amountRub < 0 || !Number.isFinite(quantity) || quantity < 0) {
    return {
      code: "invalid_price",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: "The offer price or request quantity is invalid.",
    };
  }
  return undefined;
}

function getSettings(
  workspace: EquipmentWorkspace,
  supplier: string,
): EquipmentProcurementSettings {
  const explicit = workspace.procurementSettings.find((item) => item.supplier === supplier);
  if (explicit) return explicit;
  const policy = workspace.supplierPolicies.find((item) => item.supplier === supplier);
  return {
    supplier,
    marginPct: policy?.marginPct ?? workspace.defaultMarginPct,
    marginSource: policy?.marginPct === undefined ? "default" : "seller",
    includeDelivery: policy?.includeDelivery ?? false,
    deliveryRub: policy?.deliveryRub ?? null,
    manualFields: [],
  };
}

function sumOrNull(values: Array<number | null>): number | null {
  return values.length === 0 || values.some((value) => value === null)
    ? null
    : values.reduce<number>((total, value) => total + (value ?? 0), 0);
}

export function calculateProcurement(workspace: EquipmentWorkspace): ProcurementCalculation {
  const itemsBySupplier = new Map<string, ProcurementItem[]>();
  const blockers: ProcurementBlocker[] = [];
  let selectedLineCount = 0;
  let staleCount = 0;

  for (const line of workspace.requestLines) {
    if (!line.selectedOfferId) {
      blockers.push({
        code: "no_selection",
        lineId: line.id,
        message: "No human selection has been recorded for this request line.",
      });
      continue;
    }
    selectedLineCount += 1;
    const offer = selectedOfferForLine(workspace, line.id);
    if (!offer) {
      blockers.push({
        code: "selected_offer_missing",
        lineId: line.id,
        offerId: line.selectedOfferId,
        message: "The selected offer is not present in the current platform snapshot.",
      });
      continue;
    }
    if (offer.lineId !== line.id || !workspace.candidateGroups.some((group) => group.id === offer.groupId && group.lineId === line.id)) {
      blockers.push({
        code: "selection_mismatch",
        lineId: line.id,
        offerId: offer.id,
        supplier: offer.provenance.supplier || undefined,
        message: "The selected offer is not attached to a candidate group for this request line.",
      });
      continue;
    }
    if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") staleCount += 1;
    const blocker = blockerForItem(line.id, offer, line.quantity);
    if (blocker) blockers.push(blocker);
    const lineTotalRub = offer.price.amountRub === null
      || !Number.isFinite(offer.price.amountRub)
      || offer.price.amountRub < 0
      || !Number.isFinite(line.quantity)
      || line.quantity < 0
      ? null
      : line.quantity * offer.price.amountRub;
    const item: ProcurementItem = {
      lineId: line.id,
      lineTitle: line.title,
      quantity: line.quantity,
      offer,
      matchKind: findMatchKind(offer, workspace.candidateGroups),
      lineTotalRub,
      ...(blocker ? { blocker } : {}),
    };
    const supplier = offer.provenance.supplier || "Supplier unavailable";
    itemsBySupplier.set(supplier, [...(itemsBySupplier.get(supplier) ?? []), item]);
  }

  const suppliers: ProcurementSupplierSummary[] = [...itemsBySupplier.entries()].map(([supplier, items]) => {
    const settings = getSettings(workspace, supplier);
    const lineTotals = items.map((item) => item.lineTotalRub);
    const knownLineTotals = lineTotals.filter((value): value is number => value !== null);
    const knownSubtotalRub = knownLineTotals.length === 0
      ? null
      : knownLineTotals.reduce((total, value) => total + value, 0);
    const hasBlockedItem = items.some((item) => item.blocker !== undefined);
    const subtotalRub = hasBlockedItem ? null : sumOrNull(lineTotals);
    const marginRub = subtotalRub === null ? null : subtotalRub * settings.marginPct / 100;
    let deliveryRub: number | null = null;
    if (settings.includeDelivery) {
      deliveryRub = settings.deliveryRub;
      if (deliveryRub === null) {
        blockers.push({
          code: "missing_delivery",
          supplier,
          message: "Delivery is enabled but no authoritative delivery amount is recorded.",
        });
      }
    }
    const totalRub = subtotalRub === null || marginRub === null || (settings.includeDelivery && deliveryRub === null)
      ? null
      : subtotalRub + marginRub + (deliveryRub ?? 0);
    return {
      supplier,
      items,
      selectedCount: items.length,
      knownSubtotalRub,
      subtotalRub,
      marginPct: settings.marginPct,
      marginSource: settings.marginSource,
      marginRub,
      includeDelivery: settings.includeDelivery,
      deliveryRub,
      totalRub,
      manualFields: [...settings.manualFields],
      unresolvedCount: items.filter((item) => item.blocker !== undefined).length + (settings.includeDelivery && deliveryRub === null ? 1 : 0),
    };
  });

  const supplierSubtotals = suppliers.map((supplier) => supplier.subtotalRub);
  const knownSubtotals = suppliers.map((supplier) => supplier.knownSubtotalRub).filter((value): value is number => value !== null);
  const supplierMargins = suppliers.map((supplier) => supplier.marginRub);
  const supplierDelivery = suppliers.map((supplier) => supplier.includeDelivery ? supplier.deliveryRub : 0);
  const totals: ProcurementCalculation["totals"] = {
    requestLineCount: workspace.requestLines.length,
    selectedLineCount,
    knownSubtotalRub: knownSubtotals.length === 0 ? null : knownSubtotals.reduce((total, value) => total + value, 0),
    subtotalRub: blockers.length === 0 ? sumOrNull(supplierSubtotals) : null,
    marginRub: blockers.length === 0 ? sumOrNull(supplierMargins) : null,
    deliveryRub: blockers.length === 0 ? sumOrNull(supplierDelivery) : null,
    totalRub: blockers.length === 0 ? sumOrNull(suppliers.map((supplier) => supplier.totalRub)) : null,
    unresolvedCount: blockers.length,
    staleCount,
  };
  return { suppliers, totals, blockers };
}

function csvCell(value: string | number | boolean | null | undefined): string {
  const text = value === null || value === undefined ? "" : String(value);
  const first = text[0];
  if (typeof value === "string" && ["=", "+", "@", "-", "\t", "\r"].includes(first ?? "")) return `"'${text.replace(/"/g, '""')}"`;
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function manualOverrideLabel(offer: EquipmentAuthoritativeOffer): string {
  const fields = [...(offer.manualOverrides.fields ?? [])];
  if (offer.manualOverrides.priceOriginal) fields.push("price_original");
  if (offer.manualOverrides.priceRub) fields.push("price_rub");
  if (offer.manualOverrides.title) fields.push("title");
  if (offer.manualOverrides.supplier) fields.push("supplier");
  return [...new Set(fields)].join(";");
}

export function buildProcurementExport(workspace: EquipmentWorkspace): EquipmentExport {
  const calculation = calculateProcurement(workspace);
  const header = [
    "request_line_id",
    "request_title",
    "quantity",
    "offer_id",
    "offer_title",
    "supplier",
    "source",
    "source_id",
    "source_ref",
    "artifact_id",
    "ingestion_run_id",
    "snapshot_id",
    "evidence_id",
    "observed_at",
    "freshness",
    "is_stale",
    "stale_reason",
    "price_original",
    "currency",
    "price_rub",
    "manual_overrides",
    "margin_pct",
    "margin_source",
    "include_delivery",
    "delivery_rub",
    "procurement_manual_fields",
    "selection_status",
    "budget_subtotal_rub",
    "margin_rub",
    "budget_delivery_rub",
    "budget_total_rub",
    "blocker_count",
    "warnings",
  ];
  const rows = workspace.requestLines.map((line) => {
    const offer = line.selectedOfferId ? workspace.offers.find((item) => item.id === line.selectedOfferId) : undefined;
    const item = calculation.suppliers.flatMap((supplier) => supplier.items).find((candidate) => candidate.lineId === line.id);
    const lineBlockers = calculation.blockers.filter((blocker) => blocker.lineId === line.id || blocker.offerId === offer?.id);
    const supplierSummary = calculation.suppliers.find((supplier) => supplier.supplier === offer?.provenance.supplier);
    const supplierBlockers = calculation.blockers.filter((blocker) => blocker.supplier === offer?.provenance.supplier && blocker.lineId === undefined && blocker.offerId === undefined);
    const warnings = [...lineBlockers, ...supplierBlockers]
      .map((blocker) => blocker.message)
      .join(" | ");
    return [
      line.id,
      line.title,
      line.quantity,
      offer?.id,
      offer?.title,
      offer?.provenance.supplier,
      offer?.provenance.source,
      offer?.provenance.sourceId,
      offer?.provenance.sourceRef,
      offer?.provenance.artifactId,
      offer?.provenance.ingestionRunId,
      offer?.provenance.snapshotId,
      offer?.provenance.evidenceId,
      offer?.provenance.observedAt,
      offer?.provenance.freshness,
      offer?.provenance.isStale,
      offer?.provenance.staleReason,
      offer?.price.amountOriginal,
      offer?.price.currency,
      offer?.price.amountRub,
      offer ? manualOverrideLabel(offer) : undefined,
      supplierSummary?.marginPct,
      supplierSummary?.marginSource,
      supplierSummary?.includeDelivery,
      supplierSummary?.deliveryRub,
      supplierSummary?.manualFields.join(";"),
      item ? "selected" : "not_selected",
      calculation.totals.subtotalRub,
      calculation.totals.marginRub,
      calculation.totals.deliveryRub,
      calculation.totals.totalRub,
      lineBlockers.length + supplierBlockers.length,
      warnings,
    ].map(csvCell);
  });
  const contents = [header.map(csvCell).join(","), ...rows.map((row) => row.join(","))].join("\n");
  return {
    filename: "equipment-procurement.csv",
    mimeType: "text/csv;charset=utf-8",
    contents: `\ufeff${contents}\n`,
    blockers: calculation.blockers,
  };
}
