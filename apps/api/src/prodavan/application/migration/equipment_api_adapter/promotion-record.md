# MIG-EQUIPMENT-API-ADAPTER-RECOVERY-02 promotion record

- Record ID: `MIG-EQUIPMENT-API-ADAPTER-RECOVERY-02:blocked:legacy-operation-boundary:2026-11-03`
- Supersedes: `MIG-EQUIPMENT-API-ADAPTER-RECOVERY-01:blocked:preflight`
- Task: `MIG-EQ-API-02`
- Status: `blocked`; typed projections and command envelope remain shadow-only
- Partition: `none admitted; authenticated tenant/SoT-owner/project/chat resolver is not wired`
- Source snapshot/revision and registry digest: `not acquired; fixture evidence only`
- Legacy row identity/parity: `unknown; shadow IDs are synthetic and non-mutable`
- `canonical_positive_writes`: `false`
- `human_selection_enabled`: `false` in this adapter
- `procurement_export_enabled`: `false` in this adapter
- Promotion/cutover authorized: `false`
- Decision owner/reviewer: migration coordinator; pending

## Confirmed existing journey

- Project equipment rows are exposed by `/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}`. `ProjectRuntimeModuleService` checks project access and module binding/SoT, applies chat scope, validates rows and commits each CRUD operation. Request lines and candidate groups are supported agent inputs; they do not make offers authoritative.
- Pod module data routes are separately scoped to a verified project bridge. MCP writes request lines and found groups; platform `EquipmentPipelineService` owns materialization of `found_offers` from catalog search, trusted-seller policy, source hashes and FX price fields.
- Human UI selection uses the cabinet module-action route and `ModuleActionExecutor._select_row`: it updates `found_offers.is_selected`, writes `request_lines.selected_offer_id`, and runs budget sync. This action does not atomically re-read freshness/availability or provide migration transaction idempotency, immutable audit, supersession CAS or unknown-commit reconciliation.
- `equipment.budget_export` consumes current `budget_lines`, persists an XLSX document with `DocumentsService`, and returns a FileRef. It is a usable legacy export, not an export of a fenced typed human-selection set.

## Adapter result and blockers

- Adapter queries and compatibility DTOs remain fixture/port-driven; no production `EquipmentApiReadPort` implementation or route composition/auth scope resolver exists.
- `equipment_v2` SQLAlchemy models are defined but have no application service use in this checkout. Their request/line records do not encode the needed owner/project/chat provenance. They cannot serve as a compatible persistence adapter without new design and authorized schema work.
- Existing JSON module-row service is durable CRUD but does not expose an equipment transaction that atomically fences domain revision, offer freshness/availability, explicit selection, audit, idempotency and export artifact receipt. Generic row writes cannot substitute for this boundary.
- Existing offers retain legacy catalog/seller/hash/price fields, but reviewed canonical supplier/source/record/schema mapping to typed v2 identities is absent. Same P/N must remain distinct by supplier and source; no inference from labels is accepted.
- Request/group commands, selection, refresh and typed export therefore remain blocked. No command is dispatched, no module row is written, and no FileRef is synthesized. A durable scoped adapter and accepted identity mapping are required before real API composition can be enabled.

## Acceptance and verification

- Added acceptance cases for request/group projections and blocked create commands, owner mismatch, same P/N across suppliers/sources, stale/unknown/insufficient availability, selection provenance and offer-group membership, missing export FileRef, and unknown transaction outcome.
- Tests/build/compile/format/Git checks were not run under the freeze. The edit tool reported its automatic lightweight lint/syntax result for modified Python files; no standalone lint command was run.

## Delivered contract

- Scoped request, catalog snapshot, catalog offer, candidate, offer and procurement query projections.
- Deterministic ordering, strict snapshot/query-bound pagination cursors, source revisions, snapshot digests, freshness and ingestion provenance.
- Read-only compatibility previews with scope-bound synthetic IDs, explicit unknown legacy IDs, tri-state freshness and loss fields.
- Human-only selection/export command construction and v1 idempotent command envelopes carrying actor, payload digest and expected revision evidence.
- All command execution stays blocked until canonical equipment registry evidence, expected source/snapshot state, CAS and durable idempotency gates are supplied.
- Promotion and rollback behavior are documented; no routes, legacy modules, database/Commerce behavior, DDL, live writes or cutover were changed.

## Blockers before promotion

1. Canonical tenant/owner/project identity and equipment-registry evidence, including an accepted registry digest, are not supplied.
2. There is no concrete scoped read port, route/auth dependency, legacy identifier mapping, compatibility proof, durable idempotency/audit store or expected-revision CAS writer.
3. Freshness thresholds and the authorization/ownership boundary for the future shadow route are not approved.
4. Acceptance tests and formal checks are frozen by task instruction and have not run. A coordinator must explicitly authorize verification after migration review.

Promotion requires all blockers above to be evidenced, reviewed and accepted in a superseding decision record. This record is not a production-readiness or cutover approval.

## Rollback

No route, source row, v2 record or artifact was changed. Rollback is a no-op. Keep current project/Pod module routes, pipeline, human selection action and legacy XLSX/PDF export authoritative. Do not map synthetic IDs, enable blocked writes, or perform DDL/live data/cutover work.
