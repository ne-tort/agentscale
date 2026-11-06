# MIG-EQUIPMENT-API-ADAPTER-01 boundary

Status: typed projections and blocked command envelopes only. The equipment v2 persistence models have no application service use in this checkout, no production `EquipmentApiReadPort` exists, and no FastAPI route/auth composition exposes this adapter. Existing project runtime module routes and equipment actions remain authoritative; this record does not promote reads or enable writes.

## Read contract

`EquipmentApiScope` is constructed from authenticated tenant, resolved SoT owner, project, active chat, and actor identity. Actor kind is optional for general reads and must be `human` for procurement selection aggregation and selection/export command construction. Every `EquipmentApiReadPort` method receives the exact domain scope and chat ID; wrapped responses are checked again for chat/tenant/owner/project equality. Domain rows are validated by `build_projection` before exposure.

| Query | Source and response | Stable order and cursor identity |
|---|---|---|
| `RequestQuery` | Normalized requests with nested normalized lines | request ID; scope + actor + domain digest + request filter |
| `CatalogQuery` | Immutable snapshot metadata, including freshness and artifact provenance | source ID + snapshot ID; scope + full evidence digest + source filter |
| `CatalogOfferQuery` | Accepted snapshot offers with supplier/source/record, original currency/price, freshness, source revision, artifact and provenance digest | snapshot ID + supplier + source + record + offer ID; scope + accepted snapshot evidence + all filters |
| `CandidateQuery` | Normalized candidate groups | line ID + ordinal + group ID; scope + domain digest + line filter |
| `OfferQuery` | Normalized tenders, including tri-state freshness, availability, supplier/source identity | line ID + ordinal + supplier + source + record + tender ID; scope + domain digest + all offer filters |
| `ProcurementQuery` | Current-chat totals derived only from current explicit human selections | supplier ID + currency; scope + actor + domain digest + sorted selection IDs + supplier filter |

Cursors are versioned URL-safe tokens bound to the complete query evidence digest and validated strictly. Page size is 1..200. Snapshot/digest changes invalidate a cursor. Catalog offers only come from accepted snapshots; no live catalog mutation or OpenSearch write is performed. Catalog response revisions are deterministic summaries of included source revisions; item DTOs additionally retain snapshot-specific revision, freshness and digest.

## Shadow compatibility rows

`CompatibilityRow` is an API preview only: `read_only=true`, `session_id=chat_id`, deterministic scope-bound `shadow:` preview ID, `legacy_row_id=null` and `legacy_id_status=unknown`. The preview ID is not an existing module row ID and MUST NOT be used for mutation or persistence. Provenance is explicit and includes source revision, snapshot digest and normalized record/source identity when available.

- `request_lines`: title/quantity/unit/part-number preview. Domain IDs are not legacy UUID row IDs, and nested request pagination differs from legacy independent row pagination.
- `found_groups`: line, part number, match, note and normalized scope/source/supplier/schema evidence. The normalized `aliases` tuple is not classified as legacy `aliases_pn`; `aliases_hash` and `source_hash` are unavailable.
- `found_offers`: source, source-record, supplier ID, source schema, original price/currency, freshness and on-order preview. Typed DTO also carries available quantity; the compatibility row deliberately omits it pending an accepted legacy availability mapping. `is_stale` is true for stale, false for fresh, and null for unknown; seller display name, RUB-converted price and exact legacy row identity are unavailable; `is_best` is always false.
- `procurement`: current-chat totals by supplier and currency from human selections only. Legacy procurement is project-wide across chats and also includes unselected offer counts, supplier margin, delivery, and derived margin totals; those are deliberately omitted. A currency is never mislabeled as RUB.
- `catalog` and `catalog_offers`: new immutable snapshot/offer DTOs, not exact equivalents of the virtual equipment search/OpenSearch row API.

`exact_legacy_equivalent` is false unless parity has been proven (currently no row is marked equivalent). `omitted_fields` lists compatibility losses; unknown identifiers are not replaced with guessed domain IDs. These DTOs must never be persisted as existing module-instance rows.

## Commands and promotion gates

Typed create-request, create-candidate, select-offer, refresh-catalog, refresh-offers, and export-procurement commands produce a versioned JSON-ready `EquipmentCommandEnvelope` with authenticated actor, scoped command ID, stable idempotency key, payload digest, optional expected source/snapshot/selection revisions and registry evidence. Replaying the same scoped command ID has the same key; a durable sink must reject key reuse with a changed payload digest. Selection and export intents require a human actor plus explicit human approval. Selection also requires expected projection digest and source revision for a future CAS.

`execute_command` only returns `accepted=false`, `state=blocked`, and `write_enabled=false`; it never calls the optional intent port. Required gates explicitly include canonical equipment registry evidence/digest, expected source and snapshot revision, accepted source snapshot binding, durable idempotency, and a compare-and-swap write adapter. Selection additionally requires authenticated human selection, approval, immutable audit and supersession CAS; export requires current selection-set evidence and human approval. Missing gates are surfaced by name. No procurement submission is represented.

## Existing API and remaining wiring

Project equipment reads and individual row CRUD use `/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}` and its POST/PATCH/DELETE siblings with `X-Prodavan-Session-Id`; the service resolves the bound SoT instance and enforces project ACL and chat scope. Pod MCP CRUD uses the distinct `/projects/{project_id}/modules/{module_id}/data/{table_slug}` route and bridge scope. Neither row API provides adapter command idempotency or an equipment snapshot CAS transaction.

The supported agent request path writes `request_lines` and `found_groups`. `found_offers` are materialized by `EquipmentPipelineService` from catalog search; direct generic writes are not evidence-backed offers. The human UI action route is `/cabinets/{cabinet_id}/modules/{module_id}/actions/{action_id}/invoke`; `data.select_row` updates selection flags and the parent request line, then triggers budget synchronization. This existing selection operation has no atomic freshness/availability recheck, immutable decision audit or migration idempotency/supersession transaction. `equipment.budget_export` renders current `budget_lines`, stores an XLSX document through `DocumentsService`, and returns a FileRef; it is not the typed-selection export boundary.

These endpoints and actions remain authoritative; this adapter adds no route registration or authorization dependency. An integration owner must supply authenticated tenant/owner/chat resolution, canonical registry and source identity evidence, a consistent production read port, and durable selection/export CAS/idempotency/artifact adapters before the typed service can participate in a user journey. Read previews stay separate from route mutation IDs.

Tests under `tests/migration/equipment_api_adapter/` are acceptance specification only and were intentionally not executed under the migration verification freeze.

## Promotion record

- Record ID: `MIG-EQUIPMENT-API-ADAPTER-RECOVERY-02:blocked:legacy-operation-boundary:2026-11-03`
- Status: blocked; shadow API contract only
- Canonical registry/scope evidence: not supplied; no canonical read or write owner has been proven
- Source snapshot/revision: deterministic fixtures only; no live source acquired
- Legacy ID mapping/parity: unknown; preview IDs explicitly synthetic
- Route/source/persistence wiring: absent; existing routes and action handlers remain unchanged
- `canonical_positive_writes`: false
- `legacy_row_writes`: false
- `human_selection_enabled`: false; only a blocked intent can be prepared
- `procurement_export_enabled`: false
- Promotion/cutover authorized: false
- Decision owner/reviewer: migration coordinator; pending
- Tests/build/compile/lint/format/git checks: prohibited by task freeze and not run

Promotion remains blocked until canonical tenant/owner/project and equipment-registry evidence is accepted; stable legacy ID mapping and compatibility parity are proven; a scoped read-port implementation and authorization owner are reviewed; durable idempotency/audit and expected-revision CAS adapters exist; stale/unknown freshness policy is approved; explicit approval is recorded; and tests are authorized and pass. No legacy route, module, DB/Commerce behavior, DDL, live write, or cutover was changed.

## Rollback

No route was switched, no source or canonical state was written, and no legacy row or projection was mutated; rollback is a no-op. Keep the existing equipment routes, pipeline, persistence and read owner authoritative. If a later shadow integration is enabled and fails, disable only that adapter route/consumer, preserve all legacy behavior and evidence, discard only unpromoted shadow responses/intents, and resume the prior read path. Never translate preview IDs into guessed legacy IDs, replay blocked intents as writes, or roll back through DDL/live data changes.

This is a blocked recovery record, not a promotion, production readiness claim, source inventory, or cutover authorization. Any changed decision requires a superseding record linked to this ID.
