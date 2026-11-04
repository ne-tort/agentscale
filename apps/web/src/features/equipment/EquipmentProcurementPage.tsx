import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi, useWorkContext } from "../../app/context";
import "./equipment.css";
import {
  calculateProcurement,
  formatEquipmentMoney,
  formatObservedAt,
  matchKindLabel,
} from "./calculations";
import { createEquipmentProjectService, EquipmentServiceError } from "./project-service";
import { type EquipmentProjectService, type EquipmentProcurementExport, type EquipmentWorkspaceScope, type EquipmentSourceCatalog } from "./live-contract";
import { EquipmentSourcesPage, type EquipmentCatalogWorkspace, type EquipmentSourcesService } from "./sources";
import {
  type EquipmentAuthoritativeOffer,
  type EquipmentCandidateGroup,
  type EquipmentRequestLine,
  type EquipmentWorkspace,
  type ProcurementCalculation,
  type ProcurementPatch,
} from "./types";

type EquipmentStage = "requests" | "candidates" | "offers" | "procurement" | "export";

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

const STAGES: Array<{ id: EquipmentStage; label: string }> = [
  { id: "requests", label: "Requests" },
  { id: "candidates", label: "Candidate groups" },
  { id: "offers", label: "Offers & review" },
  { id: "procurement", label: "Procurement / budget" },
  { id: "export", label: "Export" },
];

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

function sourcePriceLabel(offer: EquipmentAuthoritativeOffer): string {
  const { amountOriginal, currency } = offer.price;
  if (amountOriginal === null || !Number.isFinite(amountOriginal)) return "Source amount unavailable";
  if (!currency) return "Source amount recorded · currency unavailable";
  return `${amountOriginal.toLocaleString("en")} ${currency}`;
}

function freshnessLabel(offer: EquipmentAuthoritativeOffer): string {
  if (offer.provenance.isStale) return "Stale";
  if (offer.provenance.freshness === "fresh") return "Fresh";
  return "Freshness unknown";
}

function freshnessTone(offer: EquipmentAuthoritativeOffer): "positive" | "warning" | "danger" {
  if (offer.provenance.isStale) return "danger";
  return offer.provenance.freshness === "fresh" ? "positive" : "warning";
}

function manualOverrideLabels(offer: EquipmentAuthoritativeOffer): string[] {
  const labels = [...(offer.manualOverrides.fields ?? [])];
  if (offer.manualOverrides.priceOriginal) labels.push("source price");
  if (offer.manualOverrides.priceRub) labels.push("RUB price");
  if (offer.manualOverrides.title) labels.push("title");
  if (offer.manualOverrides.supplier) labels.push("supplier");
  return [...new Set(labels)];
}

function selectionBlockReason(offer: EquipmentAuthoritativeOffer): string | undefined {
  if (!offer.platformAuthoritative) return "Only platform-authoritative offers can be selected.";
  if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") return "Fresh platform evidence is required.";
  if (!offer.provenance.sourceRef?.trim()) return "The source catalog row could not be joined to this offer.";
  if (!offer.provenance.sourceIndexAt || !Number.isFinite(Date.parse(offer.provenance.sourceIndexAt))) return "The source catalog has no usable index timestamp.";
  if (offer.price.amountOriginal !== null && (!Number.isFinite(offer.price.amountOriginal) || offer.price.amountOriginal < 0)) return "The original source amount is invalid.";
  if (offer.price.amountRub !== null && (!Number.isFinite(offer.price.amountRub) || offer.price.amountRub < 0)) return "The RUB conversion is invalid.";
  return undefined;
}

function useEquipmentMutation(
  service: EquipmentProjectService,
  setWorkspace: (workspace: EquipmentWorkspace) => void,
  setError: (error: string | undefined) => void,
) {
  const [busyAction, setBusyAction] = useState<string>();
  async function mutate(action: string, operation: () => Promise<EquipmentWorkspace>): Promise<void> {
    setBusyAction(action);
    setError(undefined);
    try {
      setWorkspace(await operation());
    } catch (error) {
      setError(error instanceof EquipmentServiceError ? error.message : error instanceof Error ? error.message : "Equipment operation failed.");
    } finally {
      setBusyAction(undefined);
    }
  }
  return { busyAction, mutate };
}

function PageHeader({ workspace, heading }: { workspace: EquipmentWorkspace; heading: string }) {
  const calculation = calculateProcurement(workspace);
  return (
    <>
      <div className="equipment-page-header">
        <div><p className="equipment-eyebrow">Data / equipment</p><h1>{heading}</h1><p className="equipment-lede">Requests, candidate groups, platform offers, human selection, procurement, and budget export.</p></div>
        <div className="equipment-header-facts"><StatusBadge tone="positive">Authenticated project API</StatusBadge><span>Snapshot {workspace.revision}</span><span>{calculation.totals.selectedLineCount}/{calculation.totals.requestLineCount} lines selected</span></div>
      </div>
      <div className="equipment-boundary-banner" role="note"><div className="equipment-boundary-mark" aria-hidden="true">i</div><div><strong>Authority boundary</strong><p><span>Agent intent</span> creates request lines and candidate groups. <span>Platform materialization</span> owns offer facts. Human selection uses the module's server-side select-row action.</p></div></div>
    </>
  );
}

function StageNavigation({ activeStage, workspace, onStage }: { activeStage: EquipmentStage; workspace: EquipmentWorkspace; onStage: (stage: EquipmentStage) => void }) {
  const calculation = calculateProcurement(workspace);
  const counts: Record<EquipmentStage, number> = {
    requests: workspace.requestLines.length,
    candidates: workspace.candidateGroups.length,
    offers: workspace.offers.length,
    procurement: calculation.totals.selectedLineCount,
    export: calculation.blockers.length,
  };
  return (
    <nav className="equipment-stage-navigation" aria-label="Equipment procurement stages">
      {STAGES.map((stage, index) => (
        <button
          className={`equipment-stage-tab ${activeStage === stage.id ? "equipment-stage-active" : ""}`}
          key={stage.id}
          onClick={() => onStage(stage.id)}
          type="button"
        >
          <span className="equipment-stage-index">0{index + 1}</span>
          <span>{stage.label}</span>
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
  const calculation = calculateProcurement(workspace);
  return (
    <nav className="equipment-workspace-navigation" aria-label="Equipment workspace areas">
      <button aria-current={activeView === "procurement" ? "page" : undefined} onClick={() => onView("procurement")} type="button">
        <strong>Procurement workflow</strong>
        <small>Requests · candidates · offers · budget · export</small>
        <span>{calculation.totals.selectedLineCount}/{calculation.totals.requestLineCount} selected</span>
      </button>
      <button aria-current={activeView === "sources" ? "page" : undefined} onClick={() => onView("sources")} type="button">
        <strong>Source configuration</strong>
        <small>Admin catalogs and index freshness</small>
        <span>{sourceCount === undefined ? sourceServiceConnected ? "API service supplied" : "Not supplied" : `${sourceCount} catalogs`}</span>
      </button>
    </nav>
  );
}

function WorkspaceSnapshotContext({ workspace, scope, blocker }: { workspace: EquipmentWorkspace; scope?: EquipmentWorkspaceScope; blocker?: string }) {
  const calculation = calculateProcurement(workspace);
  const freshness = workspace.offers.reduce((counts, offer) => {
    if (offer.provenance.isStale || offer.provenance.freshness === "stale") counts.stale += 1;
    else if (offer.provenance.freshness === "fresh") counts.fresh += 1;
    else counts.unknown += 1;
    return counts;
  }, { fresh: 0, stale: 0, unknown: 0 });
  return (
    <>
      <section className="equipment-workspace-context" aria-label="Project scope and offer freshness">
        <div><span>Project</span><strong>{scope?.projectName ?? "Resolving project"}</strong><small>{scope?.projectId ?? "Project ID unavailable"} · cabinet {scope?.cabinetId ?? "unresolved"}</small></div>
        <div><span>Active chat</span><strong>{scope?.sessionTitle ?? "Not resolved"}</strong><small>{scope?.sessionId ?? "Open and select a chat for this project"}</small></div>
        <div><span>Equipment binding</span><strong>{scope ? `${scope.bindKind} · ${scope.childMayEdit ? "editable" : "read-only"}` : "Not resolved"}</strong><small>Project rows are read through the bound module's server-side scope.</small></div>
        <div><span>Selections / budget</span><strong>{calculation.totals.selectedLineCount}/{calculation.totals.requestLineCount} selected · {calculation.totals.totalRub === null ? "Unresolved" : formatEquipmentMoney(calculation.totals.totalRub, "RUB")}</strong><small>{freshness.fresh} source-current · {freshness.stale} stale · {freshness.unknown} freshness unknown</small></div>
      </section>
      {blocker && <div className="equipment-boundary-banner" role="status"><strong>Offer refresh blocker</strong><p>{blocker}</p></div>}
    </>
  );
}

function SourceConfigurationView({ scope, catalogs }: { scope?: EquipmentWorkspaceScope; catalogs: EquipmentSourceCatalog[] }) {
  return (
    <section className="equipment-sources-unavailable" aria-label="Source configuration">
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">Project / mod_equipment / catalogs</p><h2>Source configuration</h2></div><StatusBadge tone="warning">Read-only source status</StatusBadge></div>
      <p>These rows come from the authenticated project module data API. Catalog indexing is a module action; this view does not start or modify ingestion.</p>
      <section className="equipment-source-snapshot" aria-label="Project equipment catalogs"><div className="equipment-source-snapshot-heading"><strong>Catalogs</strong><span>{catalogs.length} project-bound sources</span></div>
        {!catalogs.length ? <p>No project equipment catalog rows are configured.</p> : <ul>{catalogs.map((catalog) => <li key={catalog.id}><div><strong>{catalog.name}</strong><small>{catalog.id} · {catalog.kind} · {catalog.status}{catalog.paused ? " · paused" : ""}</small></div><span>{catalog.freshness}{catalog.lastIndexedAt ? ` · indexed ${formatObservedAt(catalog.lastIndexedAt)}` : " · no successful index time"}{catalog.freshnessReason ? ` · ${catalog.freshnessReason}` : ""}</span></li>)}</ul>}
      </section>
      {scope && <div className="equipment-source-contract-note" role="note"><strong>Catalog editing</strong><span>Use the existing project module view at <Link to={`/projects/${encodeURIComponent(scope.projectId)}/modules/mod_equipment/catalogs_settings`}>catalogs_settings</Link>. Project module binding must be writable for row changes.</span></div>}
      <div className="equipment-source-contract-note" role="note"><strong>Source versus offer provenance</strong><span>Catalog index time is available and used for source freshness; the current found_offers schema has no item-level observed_at, ingestion-run, or snapshot digest fields.</span></div>
    </section>
  );
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
  const selectedOffer = offers.find((offer) => offer.id === line.selectedOfferId);
  return (
    <article className="equipment-request-card">
      <div className="equipment-card-topline">
        <span className="equipment-line-number">Line {line.id}</span>
        <StatusBadge tone={line.authoredBy === "agent" ? "intent" : line.authoredBy === "unknown" ? "warning" : "neutral"}>
          {line.authoredBy === "agent" ? "Agent request" : line.authoredBy === "import" ? "Imported request" : line.authoredBy === "unknown" ? "Author not recorded" : "User request"}
        </StatusBadge>
        {line.selectedOfferId ? <StatusBadge tone="positive">Selection recorded</StatusBadge> : <StatusBadge tone="warning">Needs selection</StatusBadge>}
      </div>
      <div className="equipment-request-card-main">
        <div>
          <h3>{line.title}</h3>
          <p className="equipment-muted">{line.partNumber ? `P/N ${line.partNumber}` : "No part number supplied"} · quantity {line.quantity}</p>
          {line.notes && <p className="equipment-card-note">{line.notes}</p>}
        </div>
        <div className="equipment-request-card-stats">
          <div><strong>{groups.length}</strong><span>candidate groups</span></div>
          <div><strong>{offers.length}</strong><span>platform offers</span></div>
        </div>
      </div>
      <div className="equipment-request-card-footer">
        <div className="equipment-selection-summary">
          <span className="equipment-muted">Selected offer</span>
          <strong>{selectedOffer ? `${selectedOffer.provenance.supplier} · ${selectedOffer.title}` : "None — selection is explicit"}</strong>
        </div>
        <div className="equipment-inline-actions">
          <button className="equipment-secondary-button" onClick={() => onStage("candidates")} type="button">Review intent</button>
          <button className="equipment-primary-button" onClick={() => onStage("offers")} type="button">Review offers <span aria-hidden="true">→</span></button>
        </div>
      </div>
    </article>
  );
}

function RequestsStage({ workspace, onStage, onCreate, busy, readOnly, projectId }: { workspace: EquipmentWorkspace; onStage: (stage: EquipmentStage) => void; onCreate: (title: string, partNumber: string, quantity: number) => void; busy: boolean; readOnly: boolean; projectId?: string }) {
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
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">01 / customer need</p><h2>Requests</h2><p className="equipment-muted">Rows are saved to the active chat's request_lines table. Agent-created groups and server-materialized offers remain separate.</p></div><StatusBadge tone="neutral">{workspace.requestLines.length} lines</StatusBadge></div>
      <form className="equipment-request-create" onSubmit={submit}>
        <label>Product / requirement<input disabled={readOnly} value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
        <label>Part number<input disabled={readOnly} value={partNumber} onChange={(event) => setPartNumber(event.target.value)} /></label>
        <label>Quantity<input disabled={readOnly} min="0.01" step="any" type="number" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
        <button className="equipment-primary-button" disabled={busy || readOnly} type="submit">{busy ? "Saving…" : readOnly ? "Read-only binding" : "Add request line"}</button>
      </form>
      {readOnly && <div className="equipment-source-contract-note"><strong>Project binding is read-only</strong><span>Enable child edits or change the module to a local binding before saving request or selection changes.</span></div>}
      {workspace.requestLines.length === 0 ? <EmptyState title="No request lines in this chat" detail="Create a request line here or add it through the existing equipment MCP tools." /> : <div className="equipment-card-list">{workspace.requestLines.map((line) => <RequestLineCard groups={workspace.candidateGroups.filter((group) => group.lineId === line.id)} key={line.id} line={line} offers={workspace.offers.filter((offer) => offer.lineId === line.id)} onStage={onStage} />)}</div>}
    </section>
  );
}

function CandidateGroupCard({ group, line, offerCount, projectId }: { group: EquipmentCandidateGroup; line?: EquipmentRequestLine; offerCount: number; projectId?: string }) {
  return (
    <article className="equipment-candidate-card">
      <div className="equipment-card-topline">
        <StatusBadge tone={matchTone(group.matchKind)}>{matchKindLabel(group.matchKind)}</StatusBadge>
        <StatusBadge tone="intent">Agent intent</StatusBadge>
        <span className="equipment-card-id">{group.id}</span>
      </div>
      <h3>{line?.title ?? "Request line unavailable"}</h3>
      <p className="equipment-muted">Line {group.lineId} · {offerCount} materialized offer{offerCount === 1 ? "" : "s"}</p>
      <div className="equipment-candidate-keys">
        {group.partNumber && <span>P/N {group.partNumber}</span>}
        {group.aliasesPn.map((alias) => <span key={`pn-${alias}`}>alias {alias}</span>)}
        {group.aliasesHash.map((hash) => <span key={`hash-${hash}`}>hash {hash}</span>)}
      </div>
      {group.note && <p className="equipment-card-note">{group.note}</p>}
      <div className="equipment-intent-callout"><strong>Intent only</strong><span>Supplier, source, currency, stock, and price must come from platform materialization.</span></div>
      {!offerCount && projectId && <div className="equipment-source-contract-note"><strong>No offer materialized</strong><span>Send the existing equipment matching request in <Link to={`/projects/${encodeURIComponent(projectId)}/chat`}>this project chat</Link> to create or refresh candidate intent. This web view does not invent an agent action.</span></div>}
    </article>
  );
}

function CandidatesStage({ workspace, projectId }: { workspace: EquipmentWorkspace; projectId?: string }) {
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading">
        <div><p className="equipment-eyebrow">02 / agent reasoning</p><h2>Candidate groups</h2><p className="equipment-muted">Exact, analog, and doubt remain visible as matching intent. This view never treats a candidate key as a price or offer.</p></div>
        <StatusBadge tone="intent">Agent-authored</StatusBadge>
      </div>
      {workspace.candidateGroups.length === 0 ? <><EmptyState title="No candidate groups loaded" detail="Candidate groups are agent-authored intent, not browser-created rows. Send an equipment matching request in this project's chat to create groups." />{projectId && <Link to={`/projects/${encodeURIComponent(projectId)}/chat`}>Open project chat</Link>}</> : (
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
  const overrides = manualOverrideLabels(offer);
  const blockReason = selectionBlockReason(offer);
  const selectable = blockReason === undefined;
  return (
    <article className={`equipment-offer-card ${selected ? "equipment-offer-selected" : ""} ${offer.provenance.isStale ? "equipment-offer-stale" : ""}`}>
      <div className="equipment-offer-header">
        <div>
          <div className="equipment-card-topline">
            <StatusBadge tone={matchTone(matchKind)}>{matchKindLabel(matchKind)}</StatusBadge>
            <StatusBadge tone={offer.platformAuthoritative ? "positive" : "warning"}>{offer.platformAuthoritative ? "Platform-authoritative" : "Unverified row"}</StatusBadge>
            <StatusBadge tone={freshnessTone(offer)}>{freshnessLabel(offer)}</StatusBadge>
            {offer.provenance.isStale && <StatusBadge tone="danger">Price held</StatusBadge>}
          </div>
          <h3>{offer.title}</h3>
          <p className="equipment-muted">{offer.sku ? `SKU ${offer.sku}` : "SKU unavailable"} · offer {offer.id}</p>
        </div>
        <div className="equipment-offer-price-block">
          <strong>{offer.price.amountRub === null ? "Unresolved" : formatEquipmentMoney(offer.price.amountRub, "RUB")}</strong>
          <span>{sourcePriceLabel(offer)}</span>
        </div>
      </div>
      <div className="equipment-offer-facts">
        <div><span>Supplier</span><strong>{offer.provenance.supplier || "Unavailable"}</strong></div>
        <div><span>Source</span><strong>{offer.provenance.source || "Unavailable"}</strong>{offer.provenance.sourceRef && <small>{offer.provenance.sourceRef}</small>}</div>
        <div><span>Catalog indexed</span><strong>{formatObservedAt(offer.provenance.sourceIndexAt)}</strong><small>Source index time, not an item-level observation.</small></div>
        <div><span>Offer observed</span><strong>{formatObservedAt(offer.provenance.observedAt)}</strong></div>
        <div><span>Availability</span><strong>{offer.available ?? "Unknown"}</strong></div>
      </div>
      {offer.provenance.staleReason && <p className="equipment-offer-warning"><strong>Stale reason:</strong> {offer.provenance.staleReason}</p>}
      <div className="equipment-offer-footer">
        <div className="equipment-override-list">
          <span className="equipment-muted">Manual overrides</span>
          {overrides.length === 0 ? <span className="equipment-override-none">None</span> : overrides.map((override) => <span className="equipment-override-chip" key={override}>{override}</span>)}
        </div>
        <div className="equipment-inline-actions">
          {selected && <StatusBadge tone="positive">Selected for this line</StatusBadge>}
          {selected ? (
            <button className="equipment-secondary-button" disabled={busy || readOnly} onClick={() => onSelect(null)} type="button">Clear selection</button>
          ) : (
            <button className="equipment-primary-button" disabled={!selectable || busy || readOnly} onClick={() => onSelect(offer.id)} title={readOnly ? "This project module binding is read-only" : selectable ? "Record one human selection for this request line" : blockReason} type="button">
              {readOnly ? "Read-only binding" : selectable ? "Select this offer" : "Selection blocked"}
            </button>
          )}
        </div>
      </div>
      {!selectable && !selected && <p className="equipment-offer-warning">Selection blocked: {blockReason}</p>}
    </article>
  );
}

function OffersStage({ workspace, busyAction, onRefresh, onSelect, readOnly }: { workspace: EquipmentWorkspace; busyAction?: string; onRefresh: () => void; onSelect: (lineId: string, offerId: string | null) => void; readOnly: boolean }) {
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading">
        <div><p className="equipment-eyebrow">03 / platform materialization</p><h2>Offers & review</h2><p className="equipment-muted">Every amount is shown with source currency and source-index freshness. Stale offers retain their last materialized price.</p></div>
        <div className="equipment-inline-actions"><StatusBadge tone="positive">{workspace.offers.filter((offer) => offer.platformAuthoritative).length} platform offers</StatusBadge><button className="equipment-secondary-button" disabled={busyAction === "refresh"} onClick={onRefresh} type="button">{busyAction === "refresh" ? "Refreshing…" : "Refresh offers"}</button></div>
      </div>
      {workspace.requestLines.length === 0 ? <EmptyState title="Nothing to review" detail="Load typed request lines first. The feature does not invent offers, prices, sources, or endpoint responses." /> : workspace.requestLines.map((line) => {
        const offers = workspace.offers.filter((offer) => offer.lineId === line.id);
        return (
          <section className="equipment-line-review" key={line.id}>
            <div className="equipment-line-review-heading">
              <div><span className="equipment-line-number">Line {line.id}</span><h3>{line.title}</h3><p className="equipment-muted">Quantity {line.quantity} · one selection maximum</p></div>
              {line.selectedOfferId ? <StatusBadge tone="positive">Selection recorded</StatusBadge> : <StatusBadge tone="warning">No selection</StatusBadge>}
            </div>
            {offers.length === 0 ? <EmptyState title="No materialized offers" detail="Candidate intent has not produced a platform-authoritative offer for this line." /> : <div className="equipment-offer-list">{offers.map((offer) => <OfferCard busy={busyAction === `select-${offer.id}` || busyAction === `clear-${line.id}`} key={offer.id} matchKind={workspace.candidateGroups.find((group) => group.id === offer.groupId)?.matchKind ?? "doubt"} offer={offer} readOnly={readOnly} selected={line.selectedOfferId === offer.id} onSelect={(offerId) => onSelect(line.id, offerId)} />)}</div>}
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
  if (calculation.blockers.length === 0) return <div className="equipment-clear-state"><span aria-hidden="true">✓</span><strong>No calculation blockers in the active-chat snapshot.</strong></div>;
  return <div className="equipment-blocker-list"><div className="equipment-blocker-heading"><strong>{calculation.blockers.length} review blocker{calculation.blockers.length === 1 ? "" : "s"}</strong><span>Totals remain unresolved where evidence is incomplete.</span></div>{calculation.blockers.map((blocker, index) => <div className="equipment-blocker" key={`${blocker.code}-${blocker.lineId ?? blocker.supplier ?? "global"}-${index}`}><span className="equipment-blocker-code">{blocker.code.replaceAll("_", " ")}</span><span>{blocker.message}</span></div>)}</div>;
}

function SupplierBudgetCard({ setting, busy, readOnly, onPatch }: { setting: EquipmentWorkspace["procurementSettings"][number]; busy: boolean; readOnly: boolean; onPatch: (patch: ProcurementPatch) => void }) {
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
      <div className="equipment-supplier-heading"><div><p className="equipment-eyebrow">Project supplier</p><h3>{setting.supplier}</h3></div><StatusBadge tone="neutral">{setting.projectSelectedCount ?? 0} selected project-wide</StatusBadge></div>
      <div className="equipment-supplier-totals"><div><span>Project procurement sum</span><strong>{setting.projectSubtotalRub === null || setting.projectSubtotalRub === undefined ? "Unavailable" : formatEquipmentMoney(setting.projectSubtotalRub, "RUB")}</strong></div><div><span>Project margin</span><strong>{setting.projectMarginRub === null || setting.projectMarginRub === undefined ? "Unavailable" : formatEquipmentMoney(setting.projectMarginRub, "RUB")}</strong></div><div><span>Project total</span><strong>{setting.projectTotalRub === null || setting.projectTotalRub === undefined ? "Unavailable" : formatEquipmentMoney(setting.projectTotalRub, "RUB")}</strong></div></div>
      <div className="equipment-supplier-controls">
        <label>Margin % <input disabled={readOnly} inputMode="decimal" min="0" step="0.1" type="number" value={marginDraft} onChange={(event) => setMarginDraft(event.target.value)} /></label>
        <label className="equipment-toggle-label"><input checked={includeDeliveryDraft} disabled={readOnly} onChange={(event) => setIncludeDeliveryDraft(event.target.checked)} type="checkbox" /> Include delivery</label>
        <label>Delivery ₽ <input disabled={!includeDeliveryDraft || readOnly} inputMode="decimal" min="0" step="0.01" type="number" value={deliveryDraft} onChange={(event) => setDeliveryDraft(event.target.value)} placeholder="Use supplier setting" /></label>
        <button className="equipment-secondary-button" disabled={busy || readOnly} onClick={applyDraft} type="button">{readOnly ? "Read-only binding" : "Apply inputs"}</button>
      </div>
      <p className="equipment-muted equipment-control-note">Changes patch the existing project procurement row; the backend trigger applies supplier margin and recalculates aggregates.</p>
    </article>
  );
}

function ProcurementStage({ workspace, busy, readOnly, onPatch }: { workspace: EquipmentWorkspace; busy: boolean; readOnly: boolean; onPatch: (supplier: string, patch: ProcurementPatch) => void }) {
  const totals = workspace.projectTotals;
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">04 / server procurement aggregate</p><h2>Procurement / budget</h2><p className="equipment-muted">The procurement table is project-wide across chats. Current-chat budget lines are shown separately; procurement inputs update server aggregate rows.</p></div><StatusBadge tone="neutral">Project scope</StatusBadge></div>
      {workspace.projectTotals && <div className="equipment-budget-metrics"><BudgetMetric detail={`${workspace.projectTotals.selectedCount} selections across project chats`} label="Project procurement sum" value={formatEquipmentMoney(workspace.projectTotals.subtotalRub, "RUB")} /><BudgetMetric detail="Backend materialized project row" label="Project total with margin" value={formatEquipmentMoney(workspace.projectTotals.totalRub, "RUB")} /><BudgetMetric detail="Project aggregate" label="Project margin" value={formatEquipmentMoney(workspace.projectTotals.marginRub, "RUB")} /><BudgetMetric detail="Active chat only" label="Selected lines" value={`${calculateProcurement(workspace).totals.selectedLineCount}/${workspace.requestLines.length}`} /></div>}
      {!workspace.projectTotals && <div className="equipment-source-contract-note" role="note"><strong>Project aggregate unavailable</strong><span>The project-wide procurement rows have missing numeric totals. Refresh offers to rematerialize procurement after selections or pricing change.</span></div>}
      {workspace.procurementSettings.length > 0 && <div className="equipment-supplier-list">{workspace.procurementSettings.map((setting) => <SupplierBudgetCard busy={busy} key={setting.supplier} onPatch={(patch) => onPatch(setting.supplier, patch)} readOnly={readOnly} setting={setting} />)}</div>}
      <section className="equipment-line-review"><div className="equipment-line-review-heading"><div><h3>Active chat budget snapshot</h3><p className="equipment-muted">Read-only server budget_lines rows for the selected chat. Export uses the same backend snapshot.</p></div></div>
        {workspace.serverBudgetLines?.length ? <ul className="equipment-budget-line-list">{workspace.serverBudgetLines.map((line) => <li key={line.lineId}><strong>{workspace.requestLines.find((request) => request.id === line.lineId)?.title ?? line.lineId}</strong><span>{line.seller || "Supplier unresolved"} · {line.quantity} × {line.priceRub === null ? "price unresolved" : formatEquipmentMoney(line.priceRub, "RUB")} · markup {line.markupPct}%</span></li>)}</ul> : <EmptyState title="No budget lines for this chat" detail="The backend budget sync has not produced a snapshot for the active chat yet." />}
      </section>
      {calculateProcurement(workspace).blockers.length > 0 && <BlockerList calculation={calculateProcurement(workspace)} />}
    </section>
  );
}

function ExportStage({ workspace, exportData, busy, onExport }: { workspace: EquipmentWorkspace; exportData?: EquipmentProcurementExport; busy: boolean; onExport: () => void }) {
  const calculation = calculateProcurement(workspace);
  const hasBlockingSelections = calculation.blockers.some((blocker) => blocker.code !== "missing_observed_at");
  return (
    <section className="equipment-stage-panel">
      <div className="equipment-section-heading"><div><p className="equipment-eyebrow">05 / backend workbook</p><h2>Export</h2><p className="equipment-muted">The existing budget_export action creates an XLSX content asset, which is downloaded through the content file API.</p></div><StatusBadge tone="neutral">XLSX</StatusBadge></div>
      <div className="equipment-export-layout"><div className="equipment-export-copy"><div className="equipment-export-icon" aria-hidden="true">↓</div><h3>Budget and commercial proposal</h3><p className="equipment-muted">Exports the active chat budget snapshot using the configured template (or backend fallback). Unresolved selections, prices, freshness, or delivery block the request.</p><div className="equipment-export-meta"><span>{workspace.requestLines.length} active-chat request lines</span><span>{calculation.totals.selectedLineCount} selections</span><span>{calculation.blockers.length} provenance/calculation notices</span></div><button className="equipment-primary-button" disabled={busy || workspace.requestLines.length === 0 || hasBlockingSelections} onClick={onExport} type="button">{busy ? "Preparing XLSX…" : "Create & download XLSX"}</button></div><div className="equipment-export-preview"><div className="equipment-preview-heading"><strong>{exportData ? exportData.filename : "No workbook downloaded"}</strong>{exportData && <StatusBadge tone="positive">{exportData.rows} rows · {exportData.mimeType}</StatusBadge>}</div>{exportData ? <p className="equipment-preview-empty">Backend workbook downloaded from the project content file reference.</p> : <div className="equipment-preview-empty">No local CSV preview is used for the procurement export.</div>}</div></div>
      {calculation.blockers.some((blocker) => blocker.code === "missing_observed_at") && <div className="equipment-export-warning"><strong>Backend provenance gap</strong><span>Materialized offer rows do not carry item-level observed time. Catalog index time is shown separately and is not presented as an offer observation timestamp.</span></div>}
      {calculation.blockers.length > 0 && <BlockerList calculation={calculation} />}
    </section>
  );
}

export function EquipmentProcurementPage({
  service: injectedService,
  heading = "Equipment procurement",
  initialWorkspace,
  initialScope,
  initialCatalogs,
  initialWorkspaceView,
  initialSourcesWorkspace,
  sourcesService,
}: EquipmentProcurementPageProps) {
  const api = useApi();
  const work = useWorkContext();
  const projectId = work.projectId;
  const service = useMemo(() => injectedService ?? (projectId ? createEquipmentProjectService(api, projectId) : undefined), [api, injectedService, projectId]);
  if (!projectId || !service) {
    return <main className="equipment-page"><div className="equipment-scope-blocker" role="alert"><strong>Project scope unavailable</strong><p>Open equipment from a loaded project. This workspace does not infer cabinet, project, or chat scope.</p></div></main>;
  }
  return <EquipmentProcurementView
    catalogs={initialCatalogs}
    heading={heading}
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
  heading: string;
  /**
   * Готовый снимок для prerender/acceptance: без него экран загружает workspace
   * из service в useEffect. Snapshot никогда не выдаётся за живые данные —
   * всё, что меняет состояние, всё равно идёт через service.
   */
  initialWorkspace?: EquipmentWorkspace;
  initialScope?: EquipmentWorkspaceScope;
  initialCatalogs?: EquipmentSourceCatalog[];
}

export function EquipmentProcurementView({ service, heading, initialWorkspace, initialScope, initialCatalogs }: EquipmentProcurementViewProps) {
  const [workspace, setWorkspace] = useState<EquipmentWorkspace | undefined>(initialWorkspace);
  const [scope, setScope] = useState<EquipmentWorkspaceScope | undefined>(initialScope);
  const [catalogs, setCatalogs] = useState<EquipmentSourceCatalog[]>(initialCatalogs ?? []);
  const [activeStage, setActiveStage] = useState<EquipmentStage>("requests");
  const [activeWorkspaceView, setActiveWorkspaceView] = useState<"procurement" | "sources">("procurement");
  const [error, setError] = useState<string>();
  const [exportData, setExportData] = useState<EquipmentProcurementExport>();
  const [exportBusy, setExportBusy] = useState(false);
  const { busyAction, mutate } = useEquipmentMutation(service, setWorkspace, setError);

  useEffect(() => {
    let mounted = true;
    setError(undefined);
    void service.getWorkspace().then((next) => {
      if (mounted) {
        setWorkspace(next);
        setScope(service.scope);
      }
    }).catch((loadError: unknown) => {
      if (mounted) setError(loadError instanceof Error ? loadError.message : "Equipment workspace failed to load.");
    });
    void service.getSourceCatalogs().then((next) => { if (mounted) setCatalogs(next); }).catch((catalogError: unknown) => { if (mounted) setError(catalogError instanceof Error ? `Catalog status unavailable: ${catalogError.message}` : "Catalog status unavailable."); });
    return () => { mounted = false; };
  }, [service]);

  if (!workspace) {
    return <main className="equipment-page">{error ? <div className="equipment-error" role="alert"><strong>Equipment workspace unavailable</strong><span>{error}</span></div> : <div className="equipment-loading-state" role="status">Loading project equipment data…</div>}</main>;
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
      setError(exportError instanceof Error ? exportError.message : "Equipment export failed.");
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
      <PageHeader heading={heading} workspace={workspace} />
      {error && <div className="equipment-error" role="alert"><strong>Action blocked</strong><span>{error}</span></div>}
      <WorkspaceSnapshotContext blocker={service.blocker} scope={scope} workspace={workspace} />
      <EquipmentWorkspaceNavigation activeView={activeWorkspaceView} onView={setActiveWorkspaceView} sourceCount={catalogs.length} sourceServiceConnected workspace={workspace} />
      {activeWorkspaceView === "sources" ? <SourceConfigurationView catalogs={catalogs} scope={scope} /> : <>
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
