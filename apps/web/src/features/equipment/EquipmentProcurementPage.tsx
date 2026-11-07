import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi, useWorkContext } from "../../app/context";
import type { Translate } from "../../i18n/catalogue";
import { useI18n } from "../../i18n/useT";
import type { Locale } from "../../i18n/core";
import { formatNumber } from "../../i18n/format";
import "./equipment.css";
import {
  calculateProcurement,
  formatEquipmentMoney,
  formatObservedAt,
  PROCUREMENT_BLOCKER_TEXT_KEYS,
} from "./calculations";
import {
  createEquipmentProjectService,
  EquipmentServiceError,
} from "./project-service";
import {
  type EquipmentProjectService,
  type EquipmentProcurementExport,
  type EquipmentServiceErrorCode,
  type EquipmentWorkspaceBlocker,
  type EquipmentWorkspaceScope,
  type EquipmentSourceCatalog,
} from "./live-contract";
import { EquipmentSourcesPage, EquipmentSourcesRequestError, type EquipmentCatalogWorkspace, type EquipmentSourcesService } from "./sources";
import {
  type EquipmentAuthoritativeOffer,
  type EquipmentCandidateGroup,
  type EquipmentRequestLine,
  type EquipmentWorkspace,
  type MatchKind,
  type ProcurementBlocker,
  type ProcurementCalculation,
  type ProcurementPatch,
} from "./types";

type EquipmentStage = "requests" | "candidates" | "offers" | "procurement" | "export";

/**
 * Read-only mount: без admin-сервиса источника Source configuration показывает только
 * переданный снимок, поэтому любая запись обязана отказать, а не сделать вид, что ушла на сервер.
 * Отказ приходит кодом — экран переводит его так же, как остальные отказы сервиса.
 */
function unmountedSources(): Promise<never> {
  return Promise.reject(new EquipmentSourcesRequestError("serviceUnavailable", {
    message: "Equipment source admin service is not mounted in this project route.",
  }));
}

const UNMOUNTED_SOURCES_SERVICE: EquipmentSourcesService = {
  loadWorkspace: unmountedSources,
  createCatalog: unmountedSources,
  patchCatalog: unmountedSources,
  invoke: unmountedSources,
  uploadFile: unmountedSources,
  uploadSecret: unmountedSources,
};

export interface EquipmentProcurementPageProps {
  service?: EquipmentProjectService;
  heading?: string;
  /** Prerender/acceptance snapshot: без service экран не выдаёт себя за живой API. */
  initialWorkspace?: EquipmentWorkspace;
  initialScope?: EquipmentWorkspaceScope;
  initialCatalogs?: EquipmentSourceCatalog[];
  initialWorkspaceView?: "procurement" | "sources";
  initialSourcesWorkspace?: EquipmentCatalogWorkspace;
  sourcesService?: EquipmentSourcesService;
}

const STAGES: Array<{ id: EquipmentStage; labelKey: string }> = [
  { id: "requests", labelKey: "equipment.stageRequests" },
  { id: "candidates", labelKey: "equipment.stageCandidates" },
  { id: "offers", labelKey: "equipment.stageOffers" },
  { id: "procurement", labelKey: "equipment.stageProcurement" },
  { id: "export", labelKey: "equipment.stageExport" },
];

/** Блокер расчёта приходит кодом; на экране он превращается в тему и фразу. */
const BLOCKER_GROUP_KEYS: Record<ProcurementBlocker["code"], string> = {
  no_selection: "equipment.blockerLabelSelection",
  selected_offer_missing: "equipment.blockerLabelSelection",
  selection_mismatch: "equipment.blockerLabelSelection",
  stale_selection: "equipment.blockerLabelFreshness",
  stale_offer: "equipment.blockerLabelFreshness",
  missing_observed_at: "equipment.blockerLabelFreshness",
  non_authoritative_offer: "equipment.blockerLabelSupplier",
  missing_source: "equipment.blockerLabelSupplier",
  unknown_price: "equipment.blockerLabelPrice",
  invalid_price: "equipment.blockerLabelPrice",
  missing_currency: "equipment.blockerLabelPrice",
  missing_delivery: "equipment.blockerLabelDelivery",
};

/** Тот же перевод, что попадает в колонку `warnings` CSV-выгрузки. */
const BLOCKER_TEXT_KEYS = PROCUREMENT_BLOCKER_TEXT_KEYS;

/** Коды ошибок сервиса переводятся подписями: сообщения сервиса пишутся для логов. */
const SERVICE_ERROR_KEYS: Record<EquipmentServiceErrorCode, string> = {
  line_not_found: "equipment.serviceErrorLineNotFound",
  offer_not_found: "equipment.serviceErrorOfferNotFound",
  offer_line_mismatch: "equipment.serviceErrorOfferLineMismatch",
  offer_not_authoritative: "equipment.serviceErrorOfferNotAuthoritative",
  stale_offer_blocked: "equipment.serviceErrorStaleOfferBlocked",
  invalid_margin: "equipment.serviceErrorInvalidMargin",
  invalid_delivery: "equipment.serviceErrorInvalidDelivery",
  scope_unavailable: "equipment.serviceErrorScopeUnavailable",
  module_unavailable: "equipment.serviceErrorModuleUnavailable",
  module_read_only: "equipment.serviceErrorModuleReadOnly",
  action_unavailable: "equipment.serviceErrorActionUnavailable",
  export_unavailable: "equipment.serviceErrorExportUnavailable",
  api_contract_invalid: "equipment.serviceErrorApiContractInvalid",
};

/**
 * Сообщения сервиса пишутся для журнала, а не для экрана, поэтому на экран идёт
 * подпись по коду: одинаково понятная и не выдающая внутренние детали.
 */
function serviceErrorText(error: unknown, t: Translate, fallbackKey = "equipment.actionFailed"): string {
  if (error instanceof EquipmentServiceError) return t(SERVICE_ERROR_KEYS[error.code] ?? fallbackKey);
  return t(fallbackKey);
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="equipment-empty-state">
      <span className="equipment-empty-icon" aria-hidden="true">—</span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

function CountPill({ value }: { value: number }) {
  return <span className="equipment-count-pill">{value}</span>;
}

function StatusBadge({ tone, children }: { tone: "neutral" | "positive" | "warning" | "danger" | "intent"; children: ReactNode }) {
  return <span className={`equipment-status-badge equipment-status-${tone}`}>{children}</span>;
}

function matchTone(kind: EquipmentCandidateGroup["matchKind"]): "positive" | "warning" | "intent" {
  return kind === "exact" ? "positive" : kind === "analog" ? "warning" : "intent";
}

function money(t: Translate, locale: Locale, amount: number | null | undefined, currency = "RUB"): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return t("equipment.unresolvedPrice");
  return formatEquipmentMoney(amount, currency, locale);
}

/**
 * Время в конвенциях выбранного языка. Пустую строку из `formatObservedAt`
 * заменяет подпись «время не указано», а нечитаемое значение остаётся как есть.
 */
function timeText(t: Translate, locale: Locale, value?: string): string {
  return formatObservedAt(value, locale) || t("equipment.timeMissing");
}

function matchKindText(t: Translate, kind: MatchKind): string {
  return kind === "exact" ? t("equipment.matchExact") : kind === "analog" ? t("equipment.matchAnalog") : t("equipment.matchDoubt");
}

function sourcePriceLabel(t: Translate, locale: Locale, offer: EquipmentAuthoritativeOffer): string {
  const { amountOriginal, currency } = offer.price;
  if (amountOriginal === null || !Number.isFinite(amountOriginal)) return t("equipment.sourceAmountUnavailable");
  if (!currency) return t("equipment.sourceAmountNoCurrency");
  return `${formatNumber(amountOriginal, locale) || String(amountOriginal)} ${currency}`;
}

/**
 * Причина устаревания: наши коды переводятся, текст источника показывается как
 * пришёл — это данные, а не наша фраза.
 */
const STALE_REASON_KEYS: Record<string, string> = {
  catalog_missing: "equipment.staleReasonCatalogMissing",
  index_failed: "equipment.staleReasonIndexFailed",
  never_indexed: "equipment.staleReasonNoIndex",
  reindex_overdue: "equipment.staleReasonReindexOverdue",
};

function staleReasonText(t: Translate, reason: string): string {
  const key = STALE_REASON_KEYS[reason];
  return key ? t(key) : reason;
}

function freshnessLabel(t: Translate, offer: EquipmentAuthoritativeOffer): string {
  if (offer.provenance.isStale) return t("equipment.freshnessStale");
  if (offer.provenance.freshness === "fresh") return t("equipment.freshnessFresh");
  return t("equipment.freshnessUnknown");
}

function freshnessTone(offer: EquipmentAuthoritativeOffer): "positive" | "warning" | "danger" {
  if (offer.provenance.isStale) return "danger";
  return offer.provenance.freshness === "fresh" ? "positive" : "warning";
}

function manualOverrideLabels(t: Translate, offer: EquipmentAuthoritativeOffer): string[] {
  const labels: string[] = [];
  for (const field of offer.manualOverrides.fields ?? []) {
    if (field === "price_original" || field === "source price") labels.push(t("equipment.overridePriceOriginal"));
    else if (field === "price_rub" || field === "RUB price") labels.push(t("equipment.overridePriceRub"));
    else if (field === "title") labels.push(t("equipment.overrideTitle"));
    else if (field === "supplier") labels.push(t("equipment.overrideSupplier"));
    else labels.push(field);
  }
  if (offer.manualOverrides.priceOriginal) labels.push(t("equipment.overridePriceOriginal"));
  if (offer.manualOverrides.priceRub) labels.push(t("equipment.overridePriceRub"));
  if (offer.manualOverrides.title) labels.push(t("equipment.overrideTitle"));
  if (offer.manualOverrides.supplier) labels.push(t("equipment.overrideSupplier"));
  return [...new Set(labels)];
}

function selectionBlockReason(t: Translate, offer: EquipmentAuthoritativeOffer): string | undefined {
  if (!offer.platformAuthoritative) return t("equipment.blockNotAuthoritative");
  if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") return t("equipment.blockNotFresh");
  if (!offer.provenance.sourceRef?.trim()) return t("equipment.blockNoSourceRow");
  if (!offer.provenance.sourceIndexAt || !Number.isFinite(Date.parse(offer.provenance.sourceIndexAt))) return t("equipment.blockNoIndexTime");
  if (offer.price.amountOriginal !== null && (!Number.isFinite(offer.price.amountOriginal) || offer.price.amountOriginal < 0)) return t("equipment.blockInvalidOriginal");
  if (offer.price.amountRub !== null && (!Number.isFinite(offer.price.amountRub) || offer.price.amountRub < 0)) return t("equipment.blockInvalidRub");
  return undefined;
}

function availabilityText(t: Translate, offer: EquipmentAuthoritativeOffer): string {
  if (offer.available === "in_stock") return t("equipment.availabilityInStock");
  if (offer.available === "on_order") return t("equipment.availabilityOnOrder");
  return t("equipment.availabilityUnknown");
}

function useEquipmentMutation(
  service: EquipmentProjectService,
  setWorkspace: (workspace: EquipmentWorkspace) => void,
  setError: (error: string | undefined) => void,
  t: Translate,
) {
  const [busyAction, setBusyAction] = useState<string>();
  async function mutate(action: string, operation: () => Promise<EquipmentWorkspace>): Promise<void> {
    setBusyAction(action);
    setError(undefined);
    try {
      setWorkspace(await operation());
    } catch (error) {
      setError(serviceErrorText(error, t));
    } finally {
      setBusyAction(undefined);
    }
  }
  return { busyAction, mutate };
}

function PageHeader({ workspace, heading }: { workspace: EquipmentWorkspace; heading: string }) {
  const { t } = useI18n();
  const calculation = calculateProcurement(workspace);
  return (
    <>
      <div className="equipment-page-header">
        <div><p className="equipment-eyebrow">{t("equipment.eyebrow")}</p><h1>{heading}</h1><p className="equipment-lede">{t("equipment.lede")}</p></div>
        <div className="equipment-header-facts"><StatusBadge tone="positive">{t("equipment.projectConnected")}</StatusBadge><span>{t("equipment.snapshot", { revision: workspace.revision })}</span><span>{t("equipment.selectedOfTotal", { selected: calculation.totals.selectedLineCount, total: calculation.totals.requestLineCount })}</span></div>
      </div>
      <div className="equipment-boundary-banner" role="note"><div className="equipment-boundary-mark" aria-hidden="true">i</div><div><strong>{t("equipment.authorityTitle")}</strong><p><span>{t("equipment.authorityAgent")}</span> {t("equipment.authorityAgentText")} <span>{t("equipment.authorityPlatform")}</span> {t("equipment.authorityPlatformText")} {t("equipment.authoritySelection")}</p></div></div>
    </>
  );
}

function StageNavigation({ activeStage, workspace, onStage }: { activeStage: EquipmentStage; workspace: EquipmentWorkspace; onStage: (stage: EquipmentStage) => void }) {
  const { t } = useI18n();
  const calculation = calculateProcurement(workspace);
  const counts: Record<EquipmentStage, number> = {
    requests: workspace.requestLines.length,
    candidates: workspace.candidateGroups.length,
    offers: workspace.offers.length,
    procurement: calculation.totals.selectedLineCount,
    export: calculation.blockers.length,
  };
  return (
    <nav className="equipment-stage-navigation" aria-label={t("equipment.stagesAria")}>
      {STAGES.map((stage, index) => (
        <button
          className={`equipment-stage-tab ${activeStage === stage.id ? "equipment-stage-active" : ""}`}
          key={stage.id}
          onClick={() => onStage(stage.id)}
          type="button"
        >
          <span className="equipment-stage-index">0{index + 1}</span>
          <span>{t(stage.labelKey)}</span>
          <CountPill value={counts[stage.id]} />
        </button>
      ))}
    </nav>
  );
}

function EquipmentWorkspaceNavigation({
  activeView,
  onView,
  workspace,
  sourceCount,
  sourceServiceConnected,
}: {
  activeView: "procurement" | "sources";
  onView: (view: "procurement" | "sources") => void;
  workspace: EquipmentWorkspace;
  sourceCount?: number;
  sourceServiceConnected: boolean;
}) {
  const { t } = useI18n();
  const calculation = calculateProcurement(workspace);
  return (
    <nav className="equipment-workspace-navigation" aria-label={t("equipment.navAria")}>
      <button aria-current={activeView === "procurement" ? "page" : undefined} onClick={() => onView("procurement")} type="button">
        <strong>{t("equipment.navProcurement")}</strong>
        <small>{t("equipment.navProcurementHint")}</small>
        <span>{t("equipment.selectedOfTotal", { selected: calculation.totals.selectedLineCount, total: calculation.totals.requestLineCount })}</span>
      </button>
      <button aria-current={activeView === "sources" ? "page" : undefined} onClick={() => onView("sources")} type="button">
        <strong>{t("equipment.navSources")}</strong>
        <small>{t("equipment.navSourcesHint")}</small>
        <span>{sourceCount === undefined ? sourceServiceConnected ? t("equipment.sourcesConnected") : t("equipment.sourcesNotConnected") : t("equipment.catalogsCount", { count: sourceCount })}</span>
      </button>
    </nav>
  );
}

function WorkspaceSnapshotContext({ workspace, scope, blocker }: { workspace: EquipmentWorkspace; scope?: EquipmentWorkspaceScope; blocker?: EquipmentWorkspaceBlocker }) {
  const { t, locale } = useI18n();
  const calculation = calculateProcurement(workspace);
  const freshness = workspace.offers.reduce((counts, offer) => {
    if (offer.provenance.isStale || offer.provenance.freshness === "stale") counts.stale += 1;
    else if (offer.provenance.freshness === "fresh") counts.fresh += 1;
    else counts.unknown += 1;
    return counts;
  }, { fresh: 0, stale: 0, unknown: 0 });
  const binding = scope
    ? `${scope.bindKind === "local" ? t("equipment.scopeBindingLocal") : t("equipment.scopeBindingGlobal")} · ${scope.childMayEdit ? t("equipment.scopeBindingEditable") : t("equipment.scopeBindingReadOnly")}`
    : t("equipment.scopeUnresolved");
  return (
    <>
      <section className="equipment-workspace-context" aria-label={t("equipment.scopeAria")}>
        <div><span>{t("equipment.scopeProject")}</span><strong>{scope?.projectName ?? t("equipment.scopeProjectResolving")}</strong><small title={scope?.projectId}>{scope ? t("equipment.scopeCabinet", { cabinet: scope.cabinetId }) : t("equipment.scopeUnresolved")}</small></div>
        <div><span>{t("equipment.scopeChat")}</span><strong>{scope?.sessionTitle ?? t("equipment.scopeChatNone")}</strong><small title={scope?.sessionId}>{scope?.sessionTitle ? t("equipment.scopeChatActive") : t("equipment.scopeChatHint")}</small></div>
        <div><span>{t("equipment.scopeBinding")}</span><strong>{binding}</strong><small>{t("equipment.scopeBindingNote")}</small></div>
        <div><span>{t("equipment.scopeBudget")}</span><strong>{t("equipment.selectedOfTotal", { selected: calculation.totals.selectedLineCount, total: calculation.totals.requestLineCount })} · {calculation.totals.totalRub === null ? t("equipment.scopeBudgetUnresolved") : money(t, locale, calculation.totals.totalRub)}</strong><small>{t("equipment.scopeFreshness", { fresh: freshness.fresh, stale: freshness.stale, unknown: freshness.unknown })}</small></div>
      </section>
      {blocker && <div className="equipment-boundary-banner" role="status"><strong>{t("equipment.refreshFailedTitle")}</strong><p>{t("equipment.refreshFailedText")}</p></div>}
    </>
  );
}

function SourceConfigurationView({ scope, catalogs }: { scope?: EquipmentWorkspaceScope; catalogs: EquipmentSourceCatalog[] }) {
  const { t, locale } = useI18n();
  return (
    <section className="equipment-sources-unavailable" aria-label={t("equipment.navSources")}>
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">{t("equipment.srcEyebrow")}</p><h2>{t("equipment.navSources")}</h2></div><StatusBadge tone="warning">{t("equipment.sourcesReadOnlyBadge")}</StatusBadge></div>
      <p>{t("equipment.sourcesIntro")}</p>
      <section className="equipment-source-snapshot" aria-label={t("equipment.catalogsTitle")}><div className="equipment-source-snapshot-heading"><strong>{t("equipment.catalogsTitle")}</strong><span>{t("equipment.catalogsProjectCount", { count: catalogs.length })}</span></div>
        {!catalogs.length ? <p>{t("equipment.catalogsEmpty")}</p> : <ul>{catalogs.map((catalog) => <li key={catalog.id}><div><strong>{catalog.name || t("equipment.unavailable")}</strong><small title={catalog.id}>{catalog.kind === "remote" ? t("equipment.catalogKindRemote") : catalog.kind === "local" ? t("equipment.catalogKindLocal") : t("equipment.catalogKindUnknown")} · {catalogStatusText(t, catalog.status)}{catalog.paused ? ` · ${t("equipment.catalogPaused")}` : ""}</small></div><span title={catalog.freshnessReason ? staleReasonText(t, catalog.freshnessReason) : undefined}>{catalog.freshness === "fresh" ? t("equipment.freshnessFresh") : catalog.freshness === "stale" ? t("equipment.freshnessStale") : t("equipment.freshnessUnknown")}{catalog.lastIndexedAt ? ` · ${t("equipment.catalogIndexedAt", { time: formatObservedAt(catalog.lastIndexedAt, locale) || catalog.lastIndexedAt })}` : ` · ${t("equipment.catalogNotIndexed")}`}</span></li>)}</ul>}
      </section>
      {scope && <div className="equipment-source-contract-note" role="note"><strong>{t("equipment.catalogEditTitle")}</strong><span>{t("equipment.catalogEditText")} <Link to={`/projects/${encodeURIComponent(scope.projectId)}/modules/mod_equipment/catalogs_settings`}>{t("equipment.catalogEditLink")}</Link></span></div>}
      <div className="equipment-source-contract-note" role="note"><strong>{t("equipment.catalogProvenanceTitle")}</strong><span>{t("equipment.catalogProvenanceText")}</span></div>
    </section>
  );
}

function catalogStatusText(t: Translate, status: string): string {
  switch (status) {
    case "draft": return t("equipment.catalogStatusDraft");
    case "queued": return t("equipment.catalogStatusQueued");
    case "indexing": return t("equipment.catalogStatusIndexing");
    case "ready": return t("equipment.catalogStatusReady");
    case "error": return t("equipment.catalogStatusError");
    default: return t("equipment.catalogStatusUnknown");
  }
}

function RequestLineCard({
  line,
  groups,
  offers,
  onStage,
}: {
  line: EquipmentRequestLine;
  groups: EquipmentCandidateGroup[];
  offers: EquipmentAuthoritativeOffer[];
  onStage: (stage: EquipmentStage) => void;
}) {
  const { t } = useI18n();
  const selectedOffer = offers.find((offer) => offer.id === line.selectedOfferId);
  return (
    <article className="equipment-request-card">
      <div className="equipment-card-topline">
        <span className="equipment-line-number" title={line.id}>{t("equipment.lineLabel")}</span>
        <StatusBadge tone={line.authoredBy === "agent" ? "intent" : line.authoredBy === "unknown" ? "warning" : "neutral"}>
          {line.authoredBy === "agent" ? t("equipment.authoredAgent") : line.authoredBy === "import" ? t("equipment.authoredImport") : line.authoredBy === "unknown" ? t("equipment.authoredUnknown") : t("equipment.authoredUser")}
        </StatusBadge>
        {line.selectedOfferId ? <StatusBadge tone="positive">{t("equipment.selectionRecorded")}</StatusBadge> : <StatusBadge tone="warning">{t("equipment.needsSelection")}</StatusBadge>}
      </div>
      <div className="equipment-request-card-main">
        <div>
          <h3>{line.title || t("equipment.lineUntitled")}</h3>
          <p className="equipment-muted">{line.partNumber ? t("equipment.partNumberValue", { partNumber: line.partNumber }) : t("equipment.partNumberMissing")} · {t("equipment.quantityValue", { quantity: line.quantity })}</p>
          {line.notes && <p className="equipment-card-note">{line.notes}</p>}
        </div>
        <div className="equipment-request-card-stats">
          <div><strong>{groups.length}</strong><span>{t("equipment.groupsCountLabel")}</span></div>
          <div><strong>{offers.length}</strong><span>{t("equipment.offersCountLabel")}</span></div>
        </div>
      </div>
      <div className="equipment-request-card-footer">
        <div className="equipment-selection-summary">
          <span className="equipment-muted">{t("equipment.selectedOfferLabel")}</span>
          <strong>{selectedOffer ? `${selectedOffer.provenance.supplier} · ${selectedOffer.title || t("equipment.offerUntitled")}` : t("equipment.noSelectionNote")}</strong>
        </div>
        <div className="equipment-inline-actions">
          <button className="equipment-secondary-button" onClick={() => onStage("candidates")} type="button">{t("equipment.reviewCandidates")}</button>
          <button className="equipment-primary-button" onClick={() => onStage("offers")} type="button">{t("equipment.reviewOffers")} <span aria-hidden="true">→</span></button>
        </div>
      </div>
    </article>
  );
}

function RequestsStage({ workspace, onStage, onCreate, busy, readOnly, projectId }: { workspace: EquipmentWorkspace; onStage: (stage: EquipmentStage) => void; onCreate: (title: string, partNumber: string, quantity: number) => void; busy: boolean; readOnly: boolean; projectId?: string }) {
  const { t } = useI18n();
  const [title, setTitle] = useState("");
  const [partNumber, setPartNumber] = useState("");
  const [quantity, setQuantity] = useState("1");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsedQuantity = Number(quantity);
    if (!title.trim() || !Number.isFinite(parsedQuantity) || parsedQuantity <= 0) return;
    onCreate(title.trim(), partNumber.trim(), parsedQuantity);
    setTitle("");
    setPartNumber("");
    setQuantity("1");
  }
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">{t("equipment.requestsEyebrow")}</p><h2>{t("equipment.requestsTitle")}</h2><p className="equipment-muted">{t("equipment.requestsNote")}</p></div><StatusBadge tone="neutral">{t("equipment.linesCount", { count: workspace.requestLines.length })}</StatusBadge></div>
      <form className="equipment-request-create" onSubmit={submit}>
        <label>{t("equipment.fieldRequirement")}<input disabled={readOnly} value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
        <label>{t("equipment.fieldPartNumber")}<input disabled={readOnly} value={partNumber} onChange={(event) => setPartNumber(event.target.value)} /></label>
        <label>{t("equipment.fieldQuantity")}<input disabled={readOnly} min="0.01" step="any" type="number" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
        <button className="equipment-primary-button" disabled={busy || readOnly} type="submit">{busy ? t("common.saving") : readOnly ? t("equipment.readOnlyBinding") : t("equipment.addRequestLine")}</button>
      </form>
      {readOnly && <div className="equipment-source-contract-note"><strong>{t("equipment.projectReadOnlyTitle")}</strong><span>{t("equipment.projectReadOnlyText")}</span></div>}
      {workspace.requestLines.length === 0 ? <EmptyState title={t("equipment.requestsEmptyTitle")} detail={t("equipment.requestsEmptyHint")} /> : <div className="equipment-card-list">{workspace.requestLines.map((line) => <RequestLineCard groups={workspace.candidateGroups.filter((group) => group.lineId === line.id)} key={line.id} line={line} offers={workspace.offers.filter((offer) => offer.lineId === line.id)} onStage={onStage} />)}</div>}
    </section>
  );
}

function CandidateGroupCard({ group, line, offerCount, projectId }: { group: EquipmentCandidateGroup; line?: EquipmentRequestLine; offerCount: number; projectId?: string }) {
  const { t } = useI18n();
  return (
    <article className="equipment-candidate-card" title={group.id}>
      <div className="equipment-card-topline">
        <StatusBadge tone={matchTone(group.matchKind)}>{matchKindText(t, group.matchKind)}</StatusBadge>
        <StatusBadge tone="intent">{t("equipment.agentIntentBadge")}</StatusBadge>
      </div>
      <h3>{line?.title || t("equipment.lineUnavailable")}</h3>
      <p className="equipment-muted">{t("equipment.groupOffersCount", { count: offerCount })}</p>
      <div className="equipment-candidate-keys">
        {group.partNumber && <span>{t("equipment.partNumberValue", { partNumber: group.partNumber })}</span>}
        {group.aliasesPn.map((alias) => <span key={`pn-${alias}`}>{t("equipment.aliasLabel", { alias })}</span>)}
        {group.aliasesHash.map((hash) => <span key={`hash-${hash}`}>{t("equipment.hashLabel", { hash })}</span>)}
      </div>
      {group.note && <p className="equipment-card-note">{group.note}</p>}
      <div className="equipment-intent-callout"><strong>{t("equipment.intentOnlyTitle")}</strong><span>{t("equipment.intentOnlyText")}</span></div>
      {!offerCount && projectId && <div className="equipment-source-contract-note"><strong>{t("equipment.noOfferTitle")}</strong><span>{t("equipment.noOfferText")} <Link to={`/projects/${encodeURIComponent(projectId)}/chat`}>{t("equipment.openProjectChat")}</Link></span></div>}
    </article>
  );
}

function CandidatesStage({ workspace, projectId }: { workspace: EquipmentWorkspace; projectId?: string }) {
  const { t } = useI18n();
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading">
        <div><p className="equipment-eyebrow">{t("equipment.candidatesEyebrow")}</p><h2>{t("equipment.candidatesTitle")}</h2><p className="equipment-muted">{t("equipment.candidatesNote")}</p></div>
        <StatusBadge tone="intent">{t("equipment.candidatesBadge")}</StatusBadge>
      </div>
      {workspace.candidateGroups.length === 0 ? <><EmptyState title={t("equipment.candidatesEmptyTitle")} detail={t("equipment.candidatesEmptyHint")} />{projectId && <Link to={`/projects/${encodeURIComponent(projectId)}/chat`}>{t("equipment.openProjectChat")}</Link>}</> : (
        <div className="equipment-candidate-grid">
          {workspace.candidateGroups.map((group) => (
            <CandidateGroupCard
              group={group}
              key={group.id}
              line={workspace.requestLines.find((line) => line.id === group.lineId)}
              offerCount={workspace.offers.filter((offer) => offer.groupId === group.id).length}
              projectId={projectId}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function OfferCard({
  offer,
  matchKind,
  selected,
  onSelect,
  busy,
  readOnly,
}: {
  offer: EquipmentAuthoritativeOffer;
  matchKind: EquipmentCandidateGroup["matchKind"];
  selected: boolean;
  onSelect: (offerId: string | null) => void;
  busy: boolean;
  readOnly: boolean;
}) {
  const { t, locale } = useI18n();
  const overrides = manualOverrideLabels(t, offer);
  const blockReason = selectionBlockReason(t, offer);
  const selectable = blockReason === undefined;
  return (
    <article className={`equipment-offer-card ${selected ? "equipment-offer-selected" : ""} ${offer.provenance.isStale ? "equipment-offer-stale" : ""}`}>
      <div className="equipment-offer-header">
        <div>
          <div className="equipment-card-topline">
            <StatusBadge tone={matchTone(matchKind)}>{matchKindText(t, matchKind)}</StatusBadge>
            <StatusBadge tone={offer.platformAuthoritative ? "positive" : "warning"}>{offer.platformAuthoritative ? t("equipment.offerPlatform") : t("equipment.offerUnverified")}</StatusBadge>
            <StatusBadge tone={freshnessTone(offer)}>{freshnessLabel(t, offer)}</StatusBadge>
            {offer.provenance.isStale && <StatusBadge tone="danger">{t("equipment.priceHeld")}</StatusBadge>}
          </div>
          <h3>{offer.title || t("equipment.offerUntitled")}</h3>
          <p className="equipment-muted" title={offer.id}>{offer.sku ? t("equipment.skuValue", { sku: offer.sku }) : t("equipment.skuMissing")}</p>
        </div>
        <div className="equipment-offer-price-block">
          <strong>{money(t, locale, offer.price.amountRub)}</strong>
          <span>{sourcePriceLabel(t, locale, offer)}</span>
        </div>
      </div>
      <div className="equipment-offer-facts">
        <div><span>{t("equipment.factSupplier")}</span><strong>{offer.provenance.supplier || t("equipment.unavailable")}</strong></div>
        <div><span>{t("equipment.factSource")}</span><strong>{offer.provenance.source || t("equipment.unavailable")}</strong>{offer.provenance.sourceRef && <small>{offer.provenance.sourceRef}</small>}</div>
        <div><span>{t("equipment.factCatalogIndexed")}</span><strong>{timeText(t, locale, offer.provenance.sourceIndexAt)}</strong><small>{t("equipment.factCatalogIndexedHint")}</small></div>
        <div><span>{t("equipment.factOfferObserved")}</span><strong>{timeText(t, locale, offer.provenance.observedAt)}</strong></div>
        <div><span>{t("equipment.factAvailability")}</span><strong>{availabilityText(t, offer)}</strong></div>
      </div>
      {offer.provenance.staleReason && <p className="equipment-offer-warning"><strong>{t("equipment.staleReasonLabel")}</strong> {staleReasonText(t, offer.provenance.staleReason)}</p>}
      <div className="equipment-offer-footer">
        <div className="equipment-override-list">
          <span className="equipment-muted">{t("equipment.manualOverrides")}</span>
          {overrides.length === 0 ? <span className="equipment-override-none">{t("equipment.manualOverridesNone")}</span> : overrides.map((override) => <span className="equipment-override-chip" key={override}>{override}</span>)}
        </div>
        <div className="equipment-inline-actions">
          {selected && <StatusBadge tone="positive">{t("equipment.selectedForLine")}</StatusBadge>}
          {selected ? (
            <button className="equipment-secondary-button" disabled={busy || readOnly} onClick={() => onSelect(null)} type="button">{t("equipment.clearSelection")}</button>
          ) : (
            <button className="equipment-primary-button" disabled={!selectable || busy || readOnly} onClick={() => onSelect(offer.id)} title={readOnly ? t("equipment.selectTitleReadOnly") : selectable ? t("equipment.selectTitleHint") : blockReason} type="button">
              {readOnly ? t("equipment.readOnlyBinding") : selectable ? t("equipment.selectOffer") : t("equipment.selectionBlocked")}
            </button>
          )}
        </div>
      </div>
      {!selectable && !selected && <p className="equipment-offer-warning">{t("equipment.selectionBlockedPrefix", { reason: blockReason })}</p>}
    </article>
  );
}

function OffersStage({ workspace, busyAction, onRefresh, onSelect, readOnly }: { workspace: EquipmentWorkspace; busyAction?: string; onRefresh: () => void; onSelect: (lineId: string, offerId: string | null) => void; readOnly: boolean }) {
  const { t } = useI18n();
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading">
        <div><p className="equipment-eyebrow">{t("equipment.offersEyebrow")}</p><h2>{t("equipment.offersTitle")}</h2><p className="equipment-muted">{t("equipment.offersNote")}</p></div>
        <div className="equipment-inline-actions"><StatusBadge tone="positive">{t("equipment.platformOffersCount", { count: workspace.offers.filter((offer) => offer.platformAuthoritative).length })}</StatusBadge><button className="equipment-secondary-button" disabled={busyAction === "refresh"} onClick={onRefresh} type="button">{busyAction === "refresh" ? t("equipment.refreshing") : t("equipment.refreshOffers")}</button></div>
      </div>
      {workspace.requestLines.length === 0 ? <EmptyState title={t("equipment.offersEmptyTitle")} detail={t("equipment.offersEmptyHint")} /> : workspace.requestLines.map((line) => {
        const offers = workspace.offers.filter((offer) => offer.lineId === line.id);
        return (
          <section className="equipment-line-review" key={line.id}>
            <div className="equipment-line-review-heading">
              <div><span className="equipment-line-number" title={line.id}>{t("equipment.lineLabel")}</span><h3>{line.title || t("equipment.lineUntitled")}</h3><p className="equipment-muted">{t("equipment.quantityAndSelection", { quantity: line.quantity })}</p></div>
              {line.selectedOfferId ? <StatusBadge tone="positive">{t("equipment.selectionRecorded")}</StatusBadge> : <StatusBadge tone="warning">{t("equipment.noSelection")}</StatusBadge>}
            </div>
            {offers.length === 0 ? <EmptyState title={t("equipment.offersForLineEmptyTitle")} detail={t("equipment.offersForLineEmptyHint")} /> : <div className="equipment-offer-list">{offers.map((offer) => <OfferCard busy={busyAction === `select-${offer.id}` || busyAction === `clear-${line.id}`} key={offer.id} matchKind={workspace.candidateGroups.find((group) => group.id === offer.groupId)?.matchKind ?? "doubt"} offer={offer} readOnly={readOnly} selected={line.selectedOfferId === offer.id} onSelect={(offerId) => onSelect(line.id, offerId)} />)}</div>}
          </section>
        );
      })}
    </section>
  );
}

function BudgetMetric({ label, value, detail, tone = "neutral" }: { label: string; value: string; detail: string; tone?: "neutral" | "positive" | "warning" }) {
  return <div className={`equipment-budget-metric equipment-metric-${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function BlockerList({ calculation }: { calculation: ProcurementCalculation }) {
  const { t } = useI18n();
  if (calculation.blockers.length === 0) return <div className="equipment-clear-state"><span aria-hidden="true">✓</span><strong>{t("equipment.blockersNone")}</strong></div>;
  return <div className="equipment-blocker-list"><div className="equipment-blocker-heading"><strong>{t("equipment.blockersCount", { count: calculation.blockers.length })}</strong><span>{t("equipment.blockersNote")}</span></div>{calculation.blockers.map((blocker, index) => <div className="equipment-blocker" key={`${blocker.code}-${blocker.lineId ?? blocker.supplier ?? "global"}-${index}`}><span className="equipment-blocker-code">{t(BLOCKER_GROUP_KEYS[blocker.code])}</span><span>{t(BLOCKER_TEXT_KEYS[blocker.code])}</span></div>)}</div>;
}

function SupplierBudgetCard({ setting, busy, readOnly, onPatch }: { setting: EquipmentWorkspace["procurementSettings"][number]; busy: boolean; readOnly: boolean; onPatch: (patch: ProcurementPatch) => void }) {
  const { t, locale } = useI18n();
  const [marginDraft, setMarginDraft] = useState(String(setting.marginPct));
  const [deliveryDraft, setDeliveryDraft] = useState(setting.deliveryRub === null ? "" : String(setting.deliveryRub));
  const [includeDeliveryDraft, setIncludeDeliveryDraft] = useState(setting.includeDelivery);
  useEffect(() => {
    setMarginDraft(String(setting.marginPct));
    setDeliveryDraft(setting.deliveryRub === null ? "" : String(setting.deliveryRub));
    setIncludeDeliveryDraft(setting.includeDelivery);
  }, [setting.marginPct, setting.deliveryRub, setting.includeDelivery]);
  function applyDraft() {
    const margin = marginDraft.trim() === "" ? Number.NaN : Number(marginDraft);
    const delivery = deliveryDraft.trim() === "" ? undefined : Number(deliveryDraft);
    onPatch({ marginPct: margin, includeDelivery: includeDeliveryDraft, ...(delivery === undefined ? {} : { deliveryRub: delivery }) });
  }
  return (
    <article className="equipment-supplier-budget-card">
      <div className="equipment-supplier-heading"><div><p className="equipment-eyebrow">{t("equipment.supplierEyebrow")}</p><h3>{setting.supplier}</h3></div><StatusBadge tone="neutral">{t("equipment.supplierSelectedCount", { count: setting.projectSelectedCount ?? 0 })}</StatusBadge></div>
      <div className="equipment-supplier-totals"><div><span>{t("equipment.supplierSum")}</span><strong>{setting.projectSubtotalRub === null || setting.projectSubtotalRub === undefined ? t("equipment.unavailable") : money(t, locale, setting.projectSubtotalRub)}</strong></div><div><span>{t("equipment.supplierMargin")}</span><strong>{setting.projectMarginRub === null || setting.projectMarginRub === undefined ? t("equipment.unavailable") : money(t, locale, setting.projectMarginRub)}</strong></div><div><span>{t("equipment.supplierTotal")}</span><strong>{setting.projectTotalRub === null || setting.projectTotalRub === undefined ? t("equipment.unavailable") : money(t, locale, setting.projectTotalRub)}</strong></div></div>
      <div className="equipment-supplier-controls">
        <label>{t("equipment.marginPct")} <input disabled={readOnly} inputMode="decimal" min="0" step="0.1" type="number" value={marginDraft} onChange={(event) => setMarginDraft(event.target.value)} /></label>
        <label className="equipment-toggle-label"><input checked={includeDeliveryDraft} disabled={readOnly} onChange={(event) => setIncludeDeliveryDraft(event.target.checked)} type="checkbox" /> {t("equipment.includeDelivery")}</label>
        <label>{t("equipment.deliveryAmount")} <input disabled={!includeDeliveryDraft || readOnly} inputMode="decimal" min="0" step="0.01" type="number" value={deliveryDraft} onChange={(event) => setDeliveryDraft(event.target.value)} placeholder={t("equipment.deliveryPlaceholder")} /></label>
        <button className="equipment-secondary-button" disabled={busy || readOnly} onClick={applyDraft} type="button">{readOnly ? t("equipment.readOnlyBinding") : t("equipment.applyInputs")}</button>
      </div>
      <p className="equipment-muted equipment-control-note">{t("equipment.supplierNote")}</p>
    </article>
  );
}

function ProcurementStage({ workspace, busy, readOnly, onPatch }: { workspace: EquipmentWorkspace; busy: boolean; readOnly: boolean; onPatch: (supplier: string, patch: ProcurementPatch) => void }) {
  const { t, locale } = useI18n();
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">{t("equipment.procurementEyebrow")}</p><h2>{t("equipment.procurementTitle")}</h2><p className="equipment-muted">{t("equipment.procurementNote")}</p></div><StatusBadge tone="neutral">{t("equipment.projectScope")}</StatusBadge></div>
      {workspace.projectTotals && <div className="equipment-budget-metrics"><BudgetMetric detail={t("equipment.metricSelectionsDetail", { count: workspace.projectTotals.selectedCount })} label={t("equipment.metricProjectSum")} value={money(t, locale, workspace.projectTotals.subtotalRub)} /><BudgetMetric detail={t("equipment.metricProjectTotalDetail")} label={t("equipment.metricProjectTotal")} value={money(t, locale, workspace.projectTotals.totalRub)} /><BudgetMetric detail={t("equipment.metricProjectMarginDetail")} label={t("equipment.metricProjectMargin")} value={money(t, locale, workspace.projectTotals.marginRub)} /><BudgetMetric detail={t("equipment.metricSelectedLinesDetail")} label={t("equipment.metricSelectedLines")} value={`${calculateProcurement(workspace).totals.selectedLineCount}/${workspace.requestLines.length}`} /></div>}
      {!workspace.projectTotals && <div className="equipment-source-contract-note" role="note"><strong>{t("equipment.projectTotalsMissingTitle")}</strong><span>{t("equipment.projectTotalsMissingText")}</span></div>}
      {workspace.procurementSettings.length > 0 && <div className="equipment-supplier-list">{workspace.procurementSettings.map((setting) => <SupplierBudgetCard busy={busy} key={setting.supplier} onPatch={(patch) => onPatch(setting.supplier, patch)} readOnly={readOnly} setting={setting} />)}</div>}
      <section className="equipment-line-review"><div className="equipment-line-review-heading"><div><h3>{t("equipment.chatBudgetTitle")}</h3><p className="equipment-muted">{t("equipment.chatBudgetNote")}</p></div></div>
        {workspace.serverBudgetLines?.length ? <ul className="equipment-budget-line-list">{workspace.serverBudgetLines.map((line) => <li key={line.lineId}><strong>{workspace.requestLines.find((request) => request.id === line.lineId)?.title || t("equipment.lineLabel")}</strong><span>{line.seller || t("equipment.sellerUnresolved")} · {line.quantity} × {line.priceRub === null ? t("equipment.priceUnresolved") : money(t, locale, line.priceRub)} · {t("equipment.markup", { percent: line.markupPct })}</span></li>)}</ul> : <EmptyState title={t("equipment.budgetEmptyTitle")} detail={t("equipment.budgetEmptyHint")} />}
      </section>
      {calculateProcurement(workspace).blockers.length > 0 && <BlockerList calculation={calculateProcurement(workspace)} />}
    </section>
  );
}

function ExportStage({ workspace, exportData, busy, onExport }: { workspace: EquipmentWorkspace; exportData?: EquipmentProcurementExport; busy: boolean; onExport: () => void }) {
  const { t } = useI18n();
  const calculation = calculateProcurement(workspace);
  const hasBlockingSelections = calculation.blockers.some((blocker) => blocker.code !== "missing_observed_at");
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">{t("equipment.exportEyebrow")}</p><h2>{t("equipment.exportTitle")}</h2><p className="equipment-muted">{t("equipment.exportNote")}</p></div><StatusBadge tone="neutral">XLSX</StatusBadge></div>
      <div className="equipment-export-layout"><div className="equipment-export-copy"><div className="equipment-export-icon" aria-hidden="true">↓</div><h3>{t("equipment.exportHeading")}</h3><p className="equipment-muted">{t("equipment.exportCopy")}</p><div className="equipment-export-meta"><span>{t("equipment.exportMetaLines", { count: workspace.requestLines.length })}</span><span>{t("equipment.exportMetaSelections", { count: calculation.totals.selectedLineCount })}</span><span>{t("equipment.exportMetaNotices", { count: calculation.blockers.length })}</span></div><button className="equipment-primary-button" disabled={busy || workspace.requestLines.length === 0 || hasBlockingSelections} onClick={onExport} type="button">{busy ? t("equipment.exportPreparing") : t("equipment.exportButton")}</button></div><div className="equipment-export-preview"><div className="equipment-preview-heading"><strong>{exportData ? exportData.filename : t("equipment.exportPreviewNone")}</strong>{exportData && <StatusBadge tone="positive">{t("equipment.exportRows", { count: exportData.rows })} · {exportData.mimeType}</StatusBadge>}</div>{exportData ? <p className="equipment-preview-empty">{t("equipment.exportDownloaded")}</p> : <div className="equipment-preview-empty">{t("equipment.exportPreviewNote")}</div>}</div></div>
      {calculation.blockers.some((blocker) => blocker.code === "missing_observed_at") && <div className="equipment-export-warning"><strong>{t("equipment.exportGapTitle")}</strong><span>{t("equipment.exportGapText")}</span></div>}
      {calculation.blockers.length > 0 && <BlockerList calculation={calculation} />}
    </section>
  );
}

export function EquipmentProcurementPage({
  service: injectedService,
  heading,
  initialWorkspace,
  initialScope,
  initialCatalogs,
  initialWorkspaceView,
  initialSourcesWorkspace,
  sourcesService,
}: EquipmentProcurementPageProps) {
  const { t } = useI18n();
  const api = useApi();
  const workset = useWorkContext();
  const projectId = workset.projectId;
  const service = useMemo(() => injectedService ?? (projectId ? createEquipmentProjectService(api, projectId) : undefined), [api, injectedService, projectId]);
  if (!projectId || !service) {
    return <main className="equipment-page"><div className="equipment-scope-blocker" role="alert"><strong>{t("equipment.pageScopeTitle")}</strong><p>{t("equipment.pageScopeText")}</p></div></main>;
  }
  return <EquipmentProcurementView
    heading={heading ?? t("equipment.title")}
    initialCatalogs={initialCatalogs}
    initialScope={initialScope}
    initialSourcesWorkspace={initialSourcesWorkspace}
    initialWorkspace={initialWorkspace}
    initialWorkspaceView={initialWorkspaceView}
    service={service}
    sourcesService={sourcesService}
  />;
}

export interface EquipmentProcurementViewProps {
  service: EquipmentProjectService;
  heading?: string;
  /**
   * Готовый снимок для prerender/acceptance: без него экран загружает workspace
   * из service в useEffect. Snapshot никогда не выдаётся за живые данные —
   * всё, что меняет состояние, всё равно идёт через service.
   */
  initialWorkspace?: EquipmentWorkspace;
  initialScope?: EquipmentWorkspaceScope;
  initialCatalogs?: EquipmentSourceCatalog[];
  initialWorkspaceView?: "procurement" | "sources";
  /** Снимок admin-строк каталогу для read-only Source configuration. */
  initialSourcesWorkspace?: EquipmentCatalogWorkspace;
  /** Полный admin-сервис источников; с ним Source configuration работает на запись. */
  sourcesService?: EquipmentSourcesService;
}

export function EquipmentProcurementView({ heading, initialCatalogs, initialScope, initialSourcesWorkspace, initialWorkspace, initialWorkspaceView, service, sourcesService }: EquipmentProcurementViewProps) {
  const { t } = useI18n();
  const [workspace, setWorkspace] = useState<EquipmentWorkspace | undefined>(initialWorkspace);
  const [scope, setScope] = useState<EquipmentWorkspaceScope | undefined>(initialScope);
  const [catalogs, setCatalogs] = useState<EquipmentSourceCatalog[]>(initialCatalogs ?? []);
  const [activeStage, setActiveStage] = useState<EquipmentStage>("requests");
  const [activeWorkspaceView, setActiveWorkspaceView] = useState<"procurement" | "sources">(initialWorkspaceView ?? "procurement");
  const [error, setError] = useState<string>();
  const [exportData, setExportData] = useState<EquipmentProcurementExport>();
  const [exportBusy, setExportBusy] = useState(false);
  const { busyAction, mutate } = useEquipmentMutation(service, setWorkspace, setError, t);

  useEffect(() => {
    let mounted = true;
    setError(undefined);
    void service.getWorkspace().then((next) => {
      if (mounted) {
        setWorkspace(next);
        setScope(service.scope);
      }
    }).catch((loadError: unknown) => {
      if (mounted) setError(serviceErrorText(loadError, t, "equipment.pageLoadFailed"));
    });
    void service.getSourceCatalogs().then((next) => { if (mounted) setCatalogs(next); }).catch(() => { if (mounted) setError(t("equipment.catalogStatusUnavailable")); });
    return () => { mounted = false; };
  }, [service]);

  if (!workspace) {
    return <main className="equipment-page">{error ? <div className="equipment-error" role="alert"><strong>{t("equipment.pageLoadTitle")}</strong><span>{error}</span></div> : <div className="equipment-loading-state" role="status">{t("equipment.pageLoading")}</div>}</main>;
  }

  async function refreshOffers() {
    setExportData(undefined);
    await mutate("refresh", () => service.refreshOffers());
  }

  async function createRequest(title: string, partNumber: string, quantity: number) {
    setExportData(undefined);
    await mutate("create-request", () => service.createRequest(title, partNumber, quantity));
  }

  async function selectOffer(lineId: string, offerId: string | null) {
    setExportData(undefined);
    await mutate(offerId === null ? `clear-${lineId}` : `select-${offerId}`, () => service.selectOffer(lineId, offerId));
  }

  async function exportSnapshot() {
    setExportBusy(true);
    setError(undefined);
    try {
      const next = await service.buildExport();
      setExportData(next);
      const url = URL.createObjectURL(next.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = next.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (exportError) {
      setError(serviceErrorText(exportError, t, "equipment.exportFailed"));
    } finally {
      setExportBusy(false);
    }
  }

  // Тот же fail-closed предикат, что и requireWritable в project-service: глобальный
  // биндинг без разрешения дочерних правок делает все стадии только для чтения.
  // Неразрешённый scope writability не подтверждает, поэтому тоже read-only.
  const readOnly = scope === undefined || (scope.bindKind === "global" && !scope.childMayEdit);

  return (
    <main className="equipment-page">
      <PageHeader heading={heading ?? t("equipment.title")} workspace={workspace} />
      {error && <div className="equipment-error" role="alert"><strong>{t("equipment.pageActionBlocked")}</strong><span>{error}</span></div>}
      <WorkspaceSnapshotContext blocker={service.blocker} scope={scope} workspace={workspace} />
      <EquipmentWorkspaceNavigation activeView={activeWorkspaceView} onView={setActiveWorkspaceView} sourceCount={catalogs.length} sourceServiceConnected workspace={workspace} />
      {activeWorkspaceView === "sources"
        // С живым admin-сервисом экран сам грузит свежие строки: снимок ему не передаётся,
        // иначе можно показать устаревшую конфигурацию как текущую. Без сервиса — только снимок.
        ? sourcesService
          ? <EquipmentSourcesPage service={sourcesService} />
          : initialSourcesWorkspace
            ? <EquipmentSourcesPage initialWorkspace={initialSourcesWorkspace} service={UNMOUNTED_SOURCES_SERVICE} />
            : <SourceConfigurationView catalogs={catalogs} scope={scope} />
        : <>
        <StageNavigation activeStage={activeStage} onStage={setActiveStage} workspace={workspace} />
        {activeStage === "requests" && <RequestsStage busy={busyAction === "create-request"} onCreate={createRequest} onStage={setActiveStage} projectId={scope?.projectId} readOnly={readOnly} workspace={workspace} />}
        {activeStage === "candidates" && <CandidatesStage workspace={workspace} />}
        {activeStage === "offers" && <OffersStage busyAction={busyAction} onRefresh={() => void refreshOffers()} onSelect={selectOffer} readOnly={readOnly} workspace={workspace} />}
        {activeStage === "procurement" && <ProcurementStage busy={busyAction?.startsWith("procurement-") ?? false} onPatch={(supplier, patch) => void mutate(`procurement-${supplier}`, () => service.updateProcurement(supplier, patch))} readOnly={readOnly} workspace={workspace} />}
        {activeStage === "export" && <ExportStage busy={exportBusy} exportData={exportData} onExport={exportSnapshot} workspace={workspace} />}
      </>}
    </main>
  );
}
