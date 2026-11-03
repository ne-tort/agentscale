# MIG-EQ-01 promotion record

- Record ID: `MIG-EQ-01:blocked:preflight:2026-11-01`
- Task: `MIG-EQ-01`
- Status: `blocked`
- Partition: `none admitted; tenant/owner/source/supplier inventory absent`
- Source snapshot/schema/digest: `not acquired / not inventoried / unavailable`
- Run/checkpoint: `contract implemented; no source run executed`
- Raw artifact/snapshot/offer counts: `0 observed; no source acquisition performed`
- Canonical read/write owner: `legacy/current equipment source remains authoritative`
- Projection owner: `none promoted; target projection contract only`
- `canonical_positive_writes`: `false`
- Promotion/cutover authorized: `false`
- Actor: `delegated implementation agent`
- Decision owner: `migration coordinator`
- Reviewer/signature: `pending`

## Implemented deliverables

- Source adapter protocol, bounded page acquisition, resumable run/checkpoint contract, and identity conflict handling.
- Immutable raw artifact digest/reference contract and normalized catalog snapshots/offers preserving tenant, owner, source, supplier, run, artifact, schema, observed time and provenance digest.
- Freshness/last-good failure contract and versioned deterministic rebuildable projection metadata. Search/index projection is not source-of-truth.
- Acceptance fixtures and contract test under the exclusive migration test zone. They have not been run.

## Blockers and gate

1. Upstream canonical owner/tenant/project identity is incomplete or unproven. Source and verified supplier identity are required; ingestion blocks before positive artifacts/snapshots when identity is missing. Owner/source mappings must not be inferred from labels, URLs, IDs, or payload claims.
2. The generic `application/equipment_ingestion` contracts/service and `equipment_v2_*` ORM foundation exist, but no production source/run repository, module-catalog adapter, checkpoint/CAS evidence store, immutable raw-object bridge, concrete API route, or Celery task wiring exists. The generic source kinds do not implement legacy local-file or remote PostgreSQL catalogs and do not bind their owner/project and supplier identity to the canonical contract. No accepted source partition/schema inventory, freshness thresholds, or projection publisher has been supplied.
3. `equipment_sources` is referenced in migration target documentation but has no implemented source registry API/model in this worktree. The actual legacy import and required no-second-worker handoff are recorded in `source-handoff.md`; legacy catalogs and OpenSearch remain authoritative.
4. No accepted upstream migration gate/handoff evidence or coordinator promotion decision is present.
5. Acceptance fixtures exist but were intentionally not executed under migration verification freeze.

Promotion remains blocked until canonical identity mappings and partition ownership are evidenced; source schema and supplier identity are reviewed; persistence proves immutable artifact retention, idempotency/checkpoint CAS and append-only audit; freshness thresholds/last-good policy and projection generation fencing are agreed; the current catalog route and registered Celery job have an accepted durable handoff; acceptance tests are explicitly authorized and pass; and coordinator records a superseding promotion decision. No source rows or payloads were acquired in this task.

## Rollback

No source data was read and no canonical state or projection was written; rollback is a no-op. Keep current/legacy source and read owner unchanged. If a later enabled partition fails, stop ingestion/projection publication only for that partition, preserve immutable artifacts, runs, snapshots, checkpoints and audit evidence, mark last-good stale, and continue serving the last accepted snapshot. Repair forward and rebuild projection from accepted snapshots. Never delete or rewrite raw artifacts/snapshot history as rollback.

This is an immutable blocked preflight, not a promotion, source inventory, production run, schema readiness claim, or cutover authorization. Corrections require a superseding record linked to this ID.
