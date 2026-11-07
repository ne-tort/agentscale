import type { Translate } from "../../i18n/catalogue";
import type { Locale } from "../../i18n/core";
import { formatDateTime, formatNumber } from "../../i18n/format";
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
export function formatEquipmentMoney(amount: number | null | undefined, currency = "RUB", locale: Locale = "ru"): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return "";
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${formatNumber(amount, locale) || String(amount)} ${currency}`;
  }
}

/**
 * Когда за предложением наблюдали: дата в конвенциях `locale`.
 *
 * Пустая строка для отсутствующего значения и исходная строка для
 * нечитаемого — решает вызывающий, что показать вместо них.
 */
export function formatObservedAt(value: string | undefined, locale: Locale): string {
  if (!value?.trim()) return "";
  return formatDateTime(value, locale) || value;
}

/**
 * Название вида совпадения. Без `translate` возвращается код (`exact`), а не
 * английское слово: код — это данные, а предложение должно быть переведено.
 */
export function matchKindLabel(kind: MatchKind, translate?: Translate): string {
  if (!translate) return kind;
  return translate(kind === "exact" ? "equipment.matchExact" : kind === "analog" ? "equipment.matchAnalog" : "equipment.matchDoubt");
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

/**
 * Sentence for each blocker code. Shared with the procurement screen so the
 * notice and the CSV warning say the same thing.
 */
export const PROCUREMENT_BLOCKER_TEXT_KEYS: Record<ProcurementBlocker["code"], string> = {
  no_selection: "equipment.blockerNoSelection",
  selected_offer_missing: "equipment.blockerSelectedOfferMissing",
  selection_mismatch: "equipment.blockerSelectionMismatch",
  stale_selection: "equipment.blockerStaleSelection",
  stale_offer: "equipment.blockerStaleOffer",
  non_authoritative_offer: "equipment.blockerNonAuthoritativeOffer",
  missing_source: "equipment.blockerMissingSource",
  missing_observed_at: "equipment.blockerMissingObservedAt",
  unknown_price: "equipment.blockerUnknownPrice",
  invalid_price: "equipment.blockerInvalidPrice",
  missing_currency: "equipment.blockerMissingCurrency",
  missing_delivery: "equipment.blockerMissingDelivery",
};

/**
 * A blocker's message, in the reader's language when a translator is at hand.
 *
 * Without one the code itself is returned: the CSV and the screen both have a
 * translator, and a code is data rather than a sentence in the wrong language.
 */
function blockerMessage(code: ProcurementBlocker["code"], translate?: Translate): string {
  return translate ? translate(PROCUREMENT_BLOCKER_TEXT_KEYS[code]) : code;
}

function blockerForItem(
  lineId: EquipmentId,
  offer: EquipmentAuthoritativeOffer,
  quantity: number,
  translate?: Translate,
): ProcurementBlocker | undefined {
  if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") {
    const code = offer.provenance.isStale ? "stale_selection" : "stale_offer";
    return {
      code,
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: blockerMessage(code, translate),
    };
  }
  if (!offer.platformAuthoritative) {
    return {
      code: "non_authoritative_offer",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier || undefined,
      message: blockerMessage("non_authoritative_offer", translate),
    };
  }
  if (!offerHasSource(offer)) {
    return {
      code: "missing_source",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier || undefined,
      message: blockerMessage("missing_source", translate),
    };
  }
  const observedAt = offer.provenance.observedAt;
  if (!observedAt?.trim() || !Number.isFinite(Date.parse(observedAt))) {
    return {
      code: "missing_observed_at",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier || undefined,
      message: blockerMessage("missing_observed_at", translate),
    };
  }
  if (offer.price.amountRub === null) {
    return {
      code: "unknown_price",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: blockerMessage("unknown_price", translate),
    };
  }
  if (offer.price.amountOriginal === null || !offer.price.currency?.trim()) {
    return {
      code: "missing_currency",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: blockerMessage("missing_currency", translate),
    };
  }
  if (offer.price.amountOriginal !== null && (!Number.isFinite(offer.price.amountOriginal) || offer.price.amountOriginal < 0)) {
    return {
      code: "invalid_price",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: blockerMessage("invalid_price", translate),
    };
  }
  if (!Number.isFinite(offer.price.amountRub) || offer.price.amountRub < 0 || !Number.isFinite(quantity) || quantity < 0) {
    return {
      code: "invalid_price",
      lineId,
      offerId: offer.id,
      supplier: offer.provenance.supplier,
      message: blockerMessage("invalid_price", translate),
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

export function calculateProcurement(workspace: EquipmentWorkspace, translate?: Translate): ProcurementCalculation {
  const itemsBySupplier = new Map<string, ProcurementItem[]>();
  const blockers: ProcurementBlocker[] = [];
  let selectedLineCount = 0;
  let staleCount = 0;

  for (const line of workspace.requestLines) {
    if (!line.selectedOfferId) {
      blockers.push({
        code: "no_selection",
        lineId: line.id,
        message: blockerMessage("no_selection", translate),
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
        message: blockerMessage("selected_offer_missing", translate),
      });
      continue;
    }
    if (offer.lineId !== line.id || !workspace.candidateGroups.some((group) => group.id === offer.groupId && group.lineId === line.id)) {
      blockers.push({
        code: "selection_mismatch",
        lineId: line.id,
        offerId: offer.id,
        supplier: offer.provenance.supplier || undefined,
        message: blockerMessage("selection_mismatch", translate),
      });
      continue;
    }
    if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") staleCount += 1;
    const blocker = blockerForItem(line.id, offer, line.quantity, translate);
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
    // Без поставщика строка остаётся блокером, но подпись берётся из единицы
    // copy: английское "Supplier unavailable" раньше попадало прямо на экран.
    const supplier = offer.provenance.supplier || translate?.("equipment.sellerUnresolved") || "";
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
          message: blockerMessage("missing_delivery", translate),
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

/**
 * CSV-выгрузка закупки. `translate` обязателен: колонка `warnings` — это
 * текст для человека, и без переводчика в файл попадали бы коды.
 */
export function buildProcurementExport(workspace: EquipmentWorkspace, translate: Translate): EquipmentExport {
  const calculation = calculateProcurement(workspace, translate);
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
