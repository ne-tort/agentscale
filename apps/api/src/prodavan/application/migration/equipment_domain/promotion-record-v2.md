# MIG-EQ-02 promotion record v2

- Record ID: `MIG-EQ-02:blocked:transaction-fencing-contract:2026-11-03`
- Supersedes: `MIG-EQ-02:blocked:preflight:2026-11-01`
- Task: `MIG-EQ-02`
- Status: `blocked`
- Partition: `none admitted; canonical tenant/owner/source/supplier inventory absent`
- Source snapshot/schema/digest: `not acquired / not inventoried / unavailable`
- Run/checkpoint: `typed workflow, transaction ports and acceptance scenarios authored; no source read/backfill/projection run executed`
- JSONB association rows/edges: `0 observed; no source acquisition performed`
- Typed requests/lines/groups/tenders: `fixtures only; no source data or canonical writes`
- Canonical read/write owner: `legacy/current equipment source remains authoritative`
- `canonical_positive_writes`: `false`
- `human_selection_enabled`: `false`
- `procurement_export_enabled`: `false`
- Promotion/cutover authorized: `false`
- Decision owner: `migration coordinator`
- Reviewer/signature: `pending`

## Increment

- Added generation- and digest-fenced human-selection and prepared-export transaction ports with scoped idempotency key, payload digest, exact replay/conflict outcomes, and explicit unknown-commit reconciliation. The application service returns blocked when no transaction adapter is supplied.
- Selection evidence carries manual-override and supplier/source/source-record/schema provenance. Export rows preserve that provenance, source revision/generation and exact Decimal amounts; export review checks freshness, availability, line selection, source generation/revision, digest, and currency rounding.
- Added acceptance scenarios for duplicate and cross-tenant rows, unavailable/stale offers, membership, manual override, total reconciliation, supersession CAS, duplicate export/replay, changed payload, and failure after commit.
- The ports are contracts only. There is no durable adapter, no API route or UI join, no artifact-store transaction, and no supplier submission path.

## Vertical handoff blocker

The current user journey remains in the legacy equipment path: `data.select_row` mutates `found_offers.is_selected` and `request_lines.selected_offer_id`; `EquipmentPipelineService` rebuilds `budget_lines` and project-wide `procurement`; `ModuleActionExecutor` renders current budget rows into stored XLSX/PDF. No adapter joins these legacy route/persistence semantics to the new domain transaction ports. Handoff owner: the equipment API/persistence integration owner responsible for route composition root, scoped legacy-row transaction adapter, and durable procurement artifact persistence.

No canonical owner/source/supplier mapping, bounded JSONB inventory, durable CAS/idempotency/audit adapter, or accepted coordinator handoff was supplied. These are required before promotion. Tests/build/compile/lint/format/Git checks remain intentionally unrun under the migration verification freeze.

## Rollback

No source data or durable transaction state was written. Rollback is a no-op. Keep legacy read/write ownership unchanged; do not enable canonical positive writes or cutover. Preserve this record and its predecessor as blocked evidence.
