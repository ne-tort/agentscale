import type { ApiClient, JsonObject, ProjectSelection } from "../../api/types";
import { EquipmentServiceError, type EquipmentProjectService, type EquipmentProcurementExport, type EquipmentServiceErrorCode, type EquipmentWorkspaceScope, type EquipmentSourceCatalog } from "./live-contract";
import { calculateProcurement } from "./calculations";

/**
 * Узкий порт вместо всего ApiClient: сервису нужны только чтение scope, чтение/запись
 * module data и вызов module action. ApiClient структурно удовлетворяет этому порту,
 * поэтому продовый вызов не меняется, а тест описывает ровно используемую поверхность.
 */
export type EquipmentApiPort = Pick<
  ApiClient,
  | "getProject"
  | "getSelection"
  | "getSession"
  | "listProjectRuntimeModules"
  | "getProjectRuntimeModuleMeta"
  | "listProjectRuntimeModuleData"
  | "createProjectRuntimeModuleData"
  | "patchProjectRuntimeModuleData"
  | "invokeProjectModuleAction"
  | "downloadProjectModuleFile"
>;
import { emptyEquipmentWorkspace, type EquipmentAuthoritativeOffer, type EquipmentCandidateGroup, type EquipmentWorkspace, type MatchKind, type OfferFreshness, type ProcurementPatch } from "./types";

interface Row { row_id: string; body: Record<string, unknown>; session_id?: string | null; created_at?: string | null; created_by?: string | null }
interface Action { id: string; kind: string; enabled: boolean }
interface Resolved { scope: EquipmentWorkspaceScope; actions: Action[]; catalogs: Row[]; sellers: Row[] }

function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function text(value: unknown): string { return typeof value === "string" ? value.trim() : ""; }
function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}
function tokens(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap((v) => typeof v === "string" && v.trim() ? [v.trim()] : []);
  return typeof value === "string" ? value.split(/[;,\n]/).map((v) => v.trim()).filter(Boolean) : [];
}
function list(value: unknown, label: string): unknown[] {
  const body = record(value) && "body" in value ? value.body : value;
  if (Array.isArray(body)) return body;
  if (record(body) && Array.isArray(body.items)) return body.items;
  throw new EquipmentServiceError("api_contract_invalid", `Equipment ${label} API did not return a rows list.`);
}
function rows(value: unknown, label: string): Row[] {
  return list(value, label).map((item) => {
    if (!record(item) || typeof item.row_id !== "string" || !record(item.body)) throw new EquipmentServiceError("api_contract_invalid", `Equipment ${label} API returned a malformed row.`);
    return { row_id: item.row_id, body: item.body, ...(typeof item.session_id === "string" || item.session_id === null ? { session_id: item.session_id } : {}), ...(typeof item.created_at === "string" || item.created_at === null ? { created_at: item.created_at } : {}), ...(typeof item.created_by === "string" ? { created_by: item.created_by } : {}) };
  });
}
function freshness(catalog?: Row): { state: OfferFreshness; stale: boolean; indexedAt?: string; reason?: string } {
  if (!catalog) return { state: "unknown", stale: false, reason: "Source catalog row could not be joined to this offer." };
  const indexedAt = text(catalog.body.last_indexed_at);
  const when = indexedAt ? Date.parse(indexedAt) : Number.NaN;
  if (catalog.body.status === "error") return { state: "stale", stale: true, ...(Number.isFinite(when) ? { indexedAt } : {}), reason: "The latest catalog indexing attempt failed." };
  if (catalog.body.status !== "ready" || !Number.isFinite(when)) return { state: "unknown", stale: false, reason: "The catalog has no confirmed successful indexing time." };
  if (catalog.body.source_kind !== "remote") return { state: "fresh", stale: false, indexedAt };
  const hours = Math.max(1, num(catalog.body.reindex_interval_hours) ?? 24);
  const stale = Date.now() - when >= hours * 3600000;
  return { state: stale ? "stale" : "fresh", stale, indexedAt, ...(stale ? { reason: `Remote catalog exceeds its ${hours} hour reindex interval.` } : {}) };
}
function kind(value: unknown): MatchKind { return value === "exact" || value === "analog" || value === "doubt" ? value : "doubt"; }
function ensureAction(context: Resolved, kindValue: string): Action {
  const action = context.actions.find((candidate) => candidate.kind === kindValue && candidate.enabled);
  if (!action) throw new EquipmentServiceError("action_unavailable", `No enabled ${kindValue} action is declared by mod_equipment metadata. Ask the module administrator to restore it.`);
  return action;
}

function requireWritable(context: Resolved): void {
  if (context.scope.bindKind === "global" && !context.scope.childMayEdit) {
    throw new EquipmentServiceError("module_read_only", "Equipment is globally bound read-only here. Enable child editing or switch to a local binding before changing requests, selections, or procurement.");
  }
}

export function createEquipmentProjectService(api: EquipmentApiPort, projectId: string): EquipmentProjectService {
  let context: Resolved | undefined;
  let resolving: Promise<Resolved> | undefined;
  let blocker: string | undefined;
  let revision = 0;

  async function resolve(force = false): Promise<Resolved> {
    if (force) context = undefined;
    if (context) return context;
    if (resolving) return resolving;
    resolving = (async () => {
      const project = await api.getProject(projectId);
      const cabinetId = text(project.cabinet_id);
      if (!cabinetId) throw new EquipmentServiceError("scope_unavailable", "The project API did not confirm cabinet scope.");
      const selected: ProjectSelection = await api.getSelection(cabinetId);
      if (selected.project_id !== projectId) throw new EquipmentServiceError("scope_unavailable", "This is not the persisted selected project for this cabinet. Select the project again from the cabinet workspace.");
      const sessionId = text(selected.chat_session_id);
      if (!sessionId) throw new EquipmentServiceError("scope_unavailable", "No active chat is selected. Open a project chat before loading chat-scoped request lines, offers, and budget.");
      const session = await api.getSession(projectId, sessionId);
      if (session.project_id !== projectId) throw new EquipmentServiceError("scope_unavailable", "The persisted selected chat does not belong to this project.");
      const modules = await api.listProjectRuntimeModules(projectId);
      const module = modules.items.find((item) => (item.module_id ?? item.id) === "mod_equipment");
      if (!module) throw new EquipmentServiceError("module_unavailable", "The mod_equipment module is not bound to this project. Bind it in project module settings before using procurement.");
      if (module.bind_kind !== "local" && module.bind_kind !== "global") throw new EquipmentServiceError("api_contract_invalid", "Equipment module binding kind is missing from the project module response.");
      if (typeof module.child_may_edit !== "boolean") throw new EquipmentServiceError("api_contract_invalid", "Equipment child edit permission is missing from the project module response.");
      const [meta, catalogData, sellerData] = await Promise.all([
        api.getProjectRuntimeModuleMeta(projectId, "mod_equipment", "actions"),
        api.listProjectRuntimeModuleData(projectId, "mod_equipment", "catalogs", { chats: "all" }, sessionId),
        api.listProjectRuntimeModuleData(projectId, "mod_equipment", "trusted_sellers", { chats: "all" }, sessionId),
      ]);
      const actions = list(meta, "actions metadata").flatMap((item) => record(item) && typeof item.id === "string" && typeof item.kind === "string" ? [{ id: item.id, kind: item.kind, enabled: item.enabled !== false }] : []);
      context = {
        scope: { projectId, projectName: text(project.name) || projectId, cabinetId, sessionId, sessionTitle: text(session.title) || sessionId, bindKind: module.bind_kind, childMayEdit: module.child_may_edit },
        actions,
        catalogs: rows(catalogData, "catalogs"),
        sellers: rows(sellerData, "trusted sellers"),
      };
      return context;
    })();
    try { return await resolving; }
    catch (error) { context = undefined; throw error; }
    finally { resolving = undefined; }
  }

  async function invoke(kindValue: string, rowId?: string): Promise<JsonObject> {
    const resolved = await resolve();
    const action = ensureAction(resolved, kindValue);
    return api.invokeProjectModuleAction(resolved.scope.cabinetId, resolved.scope.projectId, "mod_equipment", action.id, rowId ? { row_id: rowId } : {}, resolved.scope.sessionId);
  }

  async function load(runPipeline: boolean): Promise<EquipmentWorkspace> {
    const resolved = await resolve(true);
    if (runPipeline) {
      try { await invoke("equipment.pipeline"); blocker = undefined; }
      catch (error) { blocker = `Offer refresh failed: ${error instanceof Error ? error.message : "equipment.pipeline is unavailable"}`; }
    }
    const sessionId = resolved.scope.sessionId;
    const allChats = { chats: "all" as const };
    const [lineData, groupData, offerData, budgetData, procurementData] = await Promise.all([
      api.listProjectRuntimeModuleData(projectId, "mod_equipment", "request_lines", allChats, sessionId),
      api.listProjectRuntimeModuleData(projectId, "mod_equipment", "found_groups", allChats, sessionId),
      api.listProjectRuntimeModuleData(projectId, "mod_equipment", "found_offers", allChats, sessionId),
      api.listProjectRuntimeModuleData(projectId, "mod_equipment", "budget_lines", allChats, sessionId),
      api.listProjectRuntimeModuleData(projectId, "mod_equipment", "procurement", { chats: "all" }, sessionId),
    ]);
    const requestLines = rows(lineData, "request lines").filter((row) => row.session_id === sessionId).map((row) => ({ id: row.row_id, title: text(row.body.title) || "Untitled request line", ...(text(row.body.part_number) ? { partNumber: text(row.body.part_number) } : {}), quantity: num(row.body.qty ?? row.body.quantity) ?? Number.NaN, ...(text(row.body.note ?? row.body.notes) ? { notes: text(row.body.note ?? row.body.notes) } : {}), authoredBy: "unknown" as const, selectedOfferId: typeof row.body.selected_offer_id === "string" ? row.body.selected_offer_id : null, ...(row.session_id ? { sessionId: row.session_id } : {}) }));
    const groupRows = rows(groupData, "candidate groups").filter((row) => row.session_id === sessionId);
    const groups: EquipmentCandidateGroup[] = groupRows.flatMap((row) => {
      const lineId = text(row.body.line_id);
      return lineId ? [{ id: row.row_id, lineId, matchKind: kind(row.body.match_kind), ...(text(row.body.part_number) ? { partNumber: text(row.body.part_number) } : {}), aliasesPn: tokens(row.body.aliases_pn), aliasesHash: tokens(row.body.aliases_hash), ...(text(row.body.note) ? { note: text(row.body.note) } : {}), authoredBy: "agent", ...(row.created_at ? { createdAt: row.created_at } : {}) }] : [];
    });
    const catalogById = new Map(resolved.catalogs.map((row) => [row.row_id, row]));
    const groupIds = new Set(groups.map((group) => group.id));
    const offers: EquipmentAuthoritativeOffer[] = rows(offerData, "materialized offers").filter((row) => row.session_id === sessionId).flatMap((row) => {
      const lineId = text(row.body.line_id);
      const groupId = text(row.body.group_id);
      if (!lineId || !groupId || !groupIds.has(groupId)) return [];
      const catalogId = text(row.body.catalog_id);
      const catalog = catalogById.get(catalogId);
      const freshnessInfo = freshness(catalog);
      const seller = text(row.body.seller);
      const manual = record(row.body.manual) ? row.body.manual : {};
      return [{
        id: row.row_id, lineId, groupId, title: text(row.body.title) || "Untitled offer",
        ...(text(row.body.part_number) ? { sku: text(row.body.part_number) } : {}),
        price: { amountOriginal: num(row.body.price_orig), currency: text(row.body.currency) || null, amountRub: num(row.body.price) },
        provenance: { supplier: seller, source: text(catalog?.body.name) || text(row.body.source_catalog) || "Equipment catalog", ...(catalogId ? { sourceId: catalogId, sourceRef: text(row.body.src_hash) || catalogId } : {}), ...(freshnessInfo.indexedAt ? { sourceIndexAt: freshnessInfo.indexedAt } : {}), sourceKind: "catalog", freshness: freshnessInfo.state, isStale: row.body.is_stale === true || freshnessInfo.stale, ...(freshnessInfo.reason ? { staleReason: freshnessInfo.reason } : {}) },
        available: row.body.in_stock === true ? "in_stock" as const : "unknown" as const,
        ...(num(row.body.lead_time) !== null ? { leadTimeDays: num(row.body.lead_time) } : {}),
        platformAuthoritative: Boolean(seller && groupId && lineId && text(row.body.src_hash)),
        manualOverrides: { priceOriginal: Boolean(manual.price_orig || manual.currency), priceRub: Boolean(manual.price || manual.currency), title: Boolean(manual.title), supplier: Boolean(manual.seller), fields: Object.keys(manual) },
      }];
    });
    const supplierPolicies = resolved.sellers.flatMap((row) => {
      const supplier = text(row.body.name);
      return supplier ? [{ supplier, verified: row.body.is_verified === true, ...(num(row.body.margin_pct) !== null ? { marginPct: num(row.body.margin_pct)! } : {}), deliveryRub: num(row.body.delivery_rub) }] : [];
    });
    const procurementRows = rows(procurementData, "procurement aggregates");
    const procurementSettings = procurementRows.flatMap((row) => {
      const supplier = text(row.body.seller);
      if (!supplier) return [];
      const policy = supplierPolicies.find((item) => item.supplier.toLowerCase() === supplier.toLowerCase());
      return [{ rowId: row.row_id, supplier, projectSubtotalRub: num(row.body.sum_rub), projectTotalRub: num(row.body.sum_with_margin_rub), projectMarginRub: num(row.body.sum_margin_rub), projectSelectedCount: num(row.body.selected_count) ?? 0, marginPct: num(row.body.margin_pct) ?? policy?.marginPct ?? 10, marginSource: policy?.marginPct === undefined ? "default" as const : "seller" as const, includeDelivery: row.body.include_delivery !== false, deliveryRub: num(row.body.delivery_rub) ?? policy?.deliveryRub ?? null, manualFields: [] }];
    });
    const budgetRows = rows(budgetData, "budget lines").filter((row) => row.session_id === sessionId);
    const budgetByLine = new Map(budgetRows.map((row) => [text(row.body.line_id), row]));
    const serverBudgetLines = requestLines.flatMap((line) => {
      const row = budgetByLine.get(line.id);
      return row ? [{ lineId: line.id, seller: text(row.body.seller), quantity: num(row.body.qty) ?? line.quantity, priceRub: num(row.body.price_in), markupPct: (num(row.body.markup) ?? 0) * 100 }] : [];
    });
    const projectTotalRows = procurementRows.flatMap((row) => {
      const subtotalRub = num(row.body.sum_rub);
      const totalRub = num(row.body.sum_with_margin_rub);
      const marginRub = num(row.body.sum_margin_rub);
      if (subtotalRub === null || totalRub === null || marginRub === null) return [];
      return [{ subtotalRub, totalRub, marginRub, selectedCount: num(row.body.selected_count) ?? 0 }];
    });
    const projectTotals = projectTotalRows.length > 0 && projectTotalRows.length === procurementRows.filter((row) => text(row.body.seller)).length
      ? projectTotalRows.reduce((total, item) => ({ subtotalRub: total.subtotalRub + item.subtotalRub, totalRub: total.totalRub + item.totalRub, marginRub: total.marginRub + item.marginRub, selectedCount: total.selectedCount + item.selectedCount }), { subtotalRub: 0, totalRub: 0, marginRub: 0, selectedCount: 0 })
      : undefined;
    revision += 1;
    return { ...emptyEquipmentWorkspace(), revision, requestLines, candidateGroups: groups, offers, supplierPolicies, procurementSettings, serverBudgetLines, ...(projectTotals ? { projectTotals } : {}), defaultMarginPct: 10 };
  }

  return {
    get scope() { return context?.scope; },
    get blocker() { return blocker; },
    getWorkspace: () => load(true),
    async getSourceCatalogs() {
      const resolved = await resolve(true);
      return resolved.catalogs.map((row) => {
        const state = freshness(row);
        return {
          id: row.row_id,
          name: text(row.body.name) || "Unnamed catalog",
          kind: row.body.source_kind === "local" || row.body.source_kind === "remote" ? row.body.source_kind : "unknown",
          status: text(row.body.status) || "unknown",
          paused: row.body.paused === true,
          ...(state.indexedAt ? { lastIndexedAt: state.indexedAt } : {}),
          freshness: state.state,
          ...(state.reason ? { freshnessReason: state.reason } : {}),
        };
      });
    },
    async createRequest(title, partNumber, quantity) {
      const resolved = await resolve(true);
      requireWritable(resolved);
      if (!title.trim() || !Number.isFinite(quantity) || quantity <= 0) throw new EquipmentServiceError("api_contract_invalid", "Request title and a positive quantity are required.");
      await api.createProjectRuntimeModuleData(projectId, "mod_equipment", "request_lines", { title: title.trim(), ...(partNumber.trim() ? { part_number: partNumber.trim() } : {}), qty: quantity } as JsonObject, resolved.scope.sessionId);
      return load(false);
    },
    refreshOffers: () => load(true),
    async selectOffer(lineId, offerId) {
      const workspace = await load(false);
      const resolved = await resolve();
      requireWritable(resolved);
      const line = workspace.requestLines.find((item) => item.id === lineId);
      if (!line) throw new EquipmentServiceError("line_not_found", `Request line ${lineId} is not in the selected chat.`);
      if (offerId !== null) {
        const offer = workspace.offers.find((item) => item.id === offerId);
        if (!offer) throw new EquipmentServiceError("offer_not_found", `Offer ${offerId} was not found in this project workspace.`);
        if (offer.lineId !== lineId || !workspace.candidateGroups.some((group) => group.id === offer.groupId && group.lineId === lineId)) throw new EquipmentServiceError("offer_line_mismatch", "An offer can only be selected for its own request line and candidate group.");
        if (!offer.platformAuthoritative || !offer.provenance.sourceRef || !offer.provenance.sourceIndexAt) throw new EquipmentServiceError("offer_not_authoritative", "The offer must join to a source catalog with a confirmed index timestamp before selection.");
        if (offer.provenance.isStale || offer.provenance.freshness !== "fresh") throw new EquipmentServiceError("stale_offer_blocked", "This offer is stale or its source freshness is unknown. Refresh the source catalog and offers before selection.");
      }
      const selected = line.selectedOfferId ? workspace.offers.find((item) => item.id === line.selectedOfferId) : undefined;
      const target = offerId ?? selected?.id;
      if (!target) return workspace;
      await invoke("data.select_row", target);
      return load(false);
    },
    async updateProcurement(supplier, patch) {
      const workspace = await load(false);
      const resolved = await resolve();
      requireWritable(resolved);
      const setting = workspace.procurementSettings.find((item) => item.supplier === supplier);
      if (!setting?.rowId) throw new EquipmentServiceError("line_not_found", `No server procurement row exists for ${supplier}. Refresh offers and confirm the supplier is enabled.`);
      if (patch.marginPct !== undefined && (!Number.isFinite(patch.marginPct) || patch.marginPct < 0 || patch.marginPct > 1000)) throw new EquipmentServiceError("invalid_margin", "Margin must be between 0 and 1000 percent.");
      if (patch.deliveryRub !== undefined && patch.deliveryRub !== null && (!Number.isFinite(patch.deliveryRub) || patch.deliveryRub < 0)) throw new EquipmentServiceError("invalid_delivery", "Delivery must be empty or a non-negative amount.");
      const body: Record<string, unknown> = {};
      if (patch.marginPct !== undefined) body.margin_pct = patch.marginPct;
      if (patch.includeDelivery !== undefined) body.include_delivery = patch.includeDelivery;
      if (patch.deliveryRub !== undefined) body.delivery_rub = patch.deliveryRub;
      await api.patchProjectRuntimeModuleData(projectId, "mod_equipment", "procurement", setting.rowId, body as JsonObject, resolved.scope.sessionId);
      return load(false);
    },
    async buildExport() {
      const workspace = await load(false);
      const resolved = await resolve();
      const blockers = calculateProcurement(workspace).blockers.filter((item) => item.code !== "missing_observed_at");
      if (blockers.length) throw new EquipmentServiceError("export_unavailable", `The backend XLSX export is blocked by ${blockers.length} unresolved active-chat issue${blockers.length === 1 ? "" : "s"} for ${resolved.scope.sessionTitle}. Resolve selection, freshness, price, and delivery first. Item-level offer observation time is missing from the server schema and remains a provenance gap.`);
      const result = await invoke("equipment.budget_export");
      const ref = record(result.file_ref) ? result.file_ref : undefined;
      if (!ref || typeof ref.asset_id !== "string" || !ref.asset_id) throw new EquipmentServiceError("export_unavailable", "The backend budget export did not return a content file reference.");
      const file = await api.downloadProjectModuleFile({ asset_id: ref.asset_id, ...(typeof ref.version_id === "string" ? { version_id: ref.version_id } : {}), ...(typeof ref.blob_version_id === "string" ? { blob_version_id: ref.blob_version_id } : {}), ...(typeof ref.storage_key === "string" || ref.storage_key === null ? { storage_key: ref.storage_key } : {}), ...(typeof ref.filename === "string" ? { filename: ref.filename } : {}) });
      return { filename: file.filename || (typeof ref.filename === "string" ? ref.filename : "budget.xlsx"), mimeType: file.contentType || "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", blob: file.blob, rows: num(result.rows) ?? 0 };
    },
  };
}

export { EquipmentServiceError };
export type { EquipmentServiceErrorCode };
