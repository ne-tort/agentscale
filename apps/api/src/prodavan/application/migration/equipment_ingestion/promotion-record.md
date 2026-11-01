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

1. Upstream canonical owner/tenant/project identity is incomplete or unproven. Source and supplier identity are required; ingestion blocks before positive artifacts/snapshots when identity is missing. Owner/source mappings must not be inferred from labels, URLs, IDs, or payload claims.
2. No bounded source partition, source schema inventory, immutable object-store adapter, transactional evidence store/CAS adapter, freshness policy thresholds, or projection rebuild/publish adapter has been supplied. This task defines ports/contracts only; no database, ORM, routes, OpenSearch wiring, or schema change is included.
3. No accepted upstream migration gate/handoff evidence or coordinator promotion decision is present.
4. Acceptance fixtures exist but were intentionally not executed under migration verification freeze.

Promotion remains blocked until canonical identity mappings and partition ownership are evidenced; source schema and supplier identity are reviewed; persistence proves immutable artifact retention, idempotency/checkpoint CAS and append-only audit; freshness thresholds/last-good policy and projection generation fencing are agreed; acceptance tests are explicitly authorized and pass; and coordinator records a superseding promotion decision. No source rows or payloads were acquired in this task.

## Rollback

No source data was read and no canonical state or projection was written; rollback is a no-op. Keep current/legacy source and read owner unchanged. If a later enabled partition fails, stop ingestion/projection publication only for that partition, preserve immutable artifacts, runs, snapshots, checkpoints and audit evidence, mark last-good stale, and continue serving the last accepted snapshot. Repair forward and rebuild projection from accepted snapshots. Never delete or rewrite raw artifacts/snapshot history as rollback.

This is an immutable blocked preflight, not a promotion, source inventory, production run, schema readiness claim, or cutover authorization. Corrections require a superseding record linked to this ID.
