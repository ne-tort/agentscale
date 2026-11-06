# Equipment persistence adapter contract

**Status:** migration design contract; implementation is deferred to a reviewed persistence wave.

**Date:** 2026-11-01

**Scope:** the adapter boundary between the T06 equipment visibility/matching application code and the `equipment_v2` persistence model. This document defines visibility, evidence, matching, ownership anchors, association backfill, selected-offer membership, CAS/idempotency, and OpenSearch projection semantics. It does not change ORM models, Alembic revisions, routes, core product documentation, or runtime data.

## 1. Purpose and ownership

The persistence adapter is the only boundary allowed to translate `equipment_v2` rows into the read-only contracts consumed by equipment visibility and matching. It may persist validated commands and migration evidence through a repository-owned transaction, but it does not become a second policy owner.

The ownership chain is explicit:

```text
tenant/company → cabinet → project → request → request line → candidate group
                                                     ↘ tender
catalog source → ingestion run → snapshot → offer → candidate-group association
```

The normalized PostgreSQL rows and immutable source artifacts are the source of truth. OpenSearch is a rebuildable search projection. Legacy JSONB arrays and legacy module rows remain compatibility inputs until the bounded cutover and rollback window have completed.

The adapter has four responsibilities:

1. resolve server-side visibility and ownership evidence for a requested snapshot, offer, candidate group, request, or selected offer;
2. apply matching and selection commands only after the same evidence snapshot has passed the T06 policy and human-approval checks;
3. import association data from JSONB with deterministic validation, resumability, and durable defect evidence;
4. publish a versioned OpenSearch projection from committed snapshots, with an explicit projection marker and retry-safe identity.

It does not infer tenant, project, supplier, source, candidate membership, approval, price, or availability from an ID prefix, an untrusted payload, or a current search result.

## 2. Existing application contract to preserve

The current T06 visibility policy is persistence-neutral and fail-closed. The adapter must preserve these semantics:

- snapshot visibility requires exact `tenant_id` and `project_id`, a timezone-aware `captured_at`, a non-empty source chain, and freshness relative to the server-observed clock;
- materialized offers must belong to the authorized snapshot, tenant/project, and allowed supplier set; their source provenance must equal the snapshot provenance;
- a selected offer must resolve to the same candidate group, be visible to the project supplier scope, have proven candidate-group membership, and have provenance equal to both the offer and snapshot;
- a missing, stale, future-dated, cross-scope, source-mismatched, supplier-invisible, or membership-unproven row is deny/quarantine evidence and cannot enter a positive write;
- candidate groups express matching intent. They cannot author authoritative offer price, currency, stock, source, or supplier facts;
- selection and procurement recalculation require the existing human approval boundary; an agent command alone never authorizes either operation;
- `verify`/read paths have no append side effect. A trusted append and a quarantine append are separate outcomes.

The adapter must return the exact resolved identity and evidence snapshot used by the policy. A downstream call must not re-resolve “latest” rows independently and silently combine data from different snapshots.

## 3. Canonical adapter envelope

Every adapter read or write uses an immutable `EquipmentPersistenceEvidence` envelope. The concrete Python type may be introduced in a later implementation, but its fields and invariants are fixed here.

Required identity and provenance fields:

| Field | Requirement |
|---|---|
| `evidence_id` | Stable immutable identity for one observed adapter snapshot; reused only with the same digest. |
| `tenant_id` | Canonical tenant/company key resolved from the ownership registry; never copied from an offer or JSON payload as proof. |
| `cabinet_id` | Required when the project is cabinet-owned or supplier visibility is cabinet-bound; exact parent anchor. |
| `project_id` | Required for project-scoped request, matching, selection, tender, and materialization operations. |
| `owner_id` / `owner_kind` | Explicit owner partition and typed scope; `visibility_key` is an additional policy key. |
| `supplier_key` | Canonical supplier identity; present in source chain and promotion/quarantine partition. |
| `source_id` | Stable source identity. It is distinct from supplier identity. |
| `ingestion_run_id` | Exact ingestion run that produced the snapshot. |
| `artifact_id` | Immutable raw artifact or source object identity; no credentials or secrets. |
| `snapshot_id` | Exact catalog snapshot used by matching/materialization. |
| `snapshot_content_hash` | Digest of the immutable snapshot content. |
| `offer_id` / `candidate_group_id` | Exact row identities where the operation concerns an offer/group. |
| `source_chain` | Non-empty ordered provenance chain including supplier/source/run/artifact evidence. |
| `provenance_digest` | Canonical digest over the chain and relevant source identities. |
| `observed_at` / `checkpoint` | Server-observed time and resumable source checkpoint. |
| `writer_generation` | Positive fencing epoch for the affected tenant/project/source partition. |
| `operation_id` / `operation_digest` | Retry identity and canonical command digest. |
| `correlation_id` | Correlates append, quarantine, audit, backfill, and projection records. |

The envelope is complete only when parent and child identities agree. A duplicated `tenant_id` on a child row is an index/RLS aid; parent `EXISTS` checks and exact composite compatibility remain mandatory.

The adapter must preserve redacted evidence only. Raw source payloads, credentials, remote DSNs, tokens, and secret references never cross this contract or enter audit/projection documents.

## 4. Ownership and visibility anchors

### 4.1 Tenant, cabinet, and project

The current `equipment_v2` models use string `tenant_id` fields and do not yet carry canonical company FKs. `project_id` is absent from the current ORM. Until a preparatory schema/backfill revision has established the anchors, the adapter may operate in shadow/read evidence mode only; it must not claim project-scoped positive parity.

The target ownership rules are:

- `tenant_id` resolves to the canonical company/tenant anchor selected for the migration. The repository must use one vocabulary consistently in RLS and FKs; it cannot mix `company_id` and `tenant_id` as interchangeable values.
- `cabinet_id` resolves through the typed cabinet anchor and must belong to the same tenant. A project must belong to the same cabinet and tenant.
- `project_id` is the partition boundary for requests, request lines, candidate groups, tenders, selections, and procurement operations. Project equality is checked against the server-resolved owner, never against client assertions.
- request lines, candidate groups, tenders, and association rows inherit the request/tender project and tenant. Scope changes after creation are blocked or performed as one reviewed transactional rebind; silent child rewrites are forbidden.
- shared catalog policy is explicit. The recommended MVP makes catalog ingestion/snapshot/offer rows tenant-scoped with `project_id = NULL`, then checks project access through a supplier/source visibility policy. A project-local catalog is a separate model and must not be simulated by copying a project ID into an association row.
- the adapter rejects a request whose project anchor is missing, ambiguous, archived, or incompatible with its tenant/cabinet. It returns a classified quarantine result rather than guessing a project.

RLS for the future tables must use transaction-local context (`app.tenant_id`, and where applicable `app.cabinet_id`, `app.user_id`, `app.role`) plus parent `EXISTS` predicates. Missing or contradictory context is deny. The adapter does not treat a copied scope column or OpenSearch filter as an ownership proof.

### 4.2 Supplier and source chain

`supplier_key` is a stable canonical supplier identity, not a display name. Supplier visibility is checked against the project/cabinet policy and against the exact source chain. The following must all hold before an offer is returned as visible:

1. the offer belongs to the requested snapshot;
2. snapshot → ingestion run has the same tenant and source ownership;
3. the source chain contains the supplier, source, run, and artifact identities;
4. the supplier is allowed by the server-resolved project policy;
5. the provenance digest matches the stored chain and the evidence envelope.

A source outage, missing artifact, source/tenant mismatch, or supplier mismatch keeps the prior committed snapshot as historical evidence and yields deny/quarantine for the new observation. It does not broaden visibility to another supplier or “latest” snapshot.

## 5. ORM mapping and required target invariants

The current ORM in `equipment_v2.py` provides these rows and relationships:

- ingestion run → snapshot is `RESTRICT`;
- snapshot → offer is currently `CASCADE`;
- request → request line, request line → candidate group, and request → budget allocation are derived-child relationships;
- tender → request is `RESTRICT`;
- candidate group has scalar `selected_offer_id` and JSONB `offer_ids`;
- tender has JSONB `candidate_group_ids`.

The adapter must treat current ORM relationships as an incomplete foundation, not as proof of full ownership. Before project-scoped cutover, the target schema must add canonical scope columns/keys, named composite constraints, and parent compatibility checks for the following:

| Relation | Required proof |
|---|---|
| snapshot → ingestion run | exact run FK, tenant/source equality, immutable content hash; run deletion is restricted while provenance is retained |
| offer → snapshot | exact snapshot FK, supplier/source chain equality, tenant visibility; offer facts are immutable per snapshot |
| request → tenant/cabinet/project | canonical parent anchors and RLS parent proof |
| request line → request | request FK plus inherited tenant/project equality |
| candidate group → request line | request-line FK plus inherited request/tenant/project equality |
| tender → request | request FK plus exact tenant/project equality |
| group offer association → group/offer | scoped group FK, offer source-chain/tenant visibility, and membership uniqueness |
| tender group association → tender/group | scoped FKs plus same-request invariant through the group’s request line |
| selected offer → group | scalar FK plus membership in the normalized group-offer association, checked in one transaction |

Owner/security evidence uses `RESTRICT`, archive transitions, or optional `SET NULL` historical links. `CASCADE` is limited to intentionally derived operational children and must not erase source, selection, audit, quarantine, or promotion evidence. A catalog snapshot containing referenced offers cannot be deleted merely to hide a source outage; it becomes stale/archived according to the lifecycle policy.

## 6. Read and write adapter ports

The persistence implementation must expose narrow ports equivalent to the following. Names are illustrative; semantics are normative.

### Read ports

- `resolve_snapshot(evidence_context, snapshot_id) -> SnapshotEvidence | Missing/Conflict`
- `resolve_offer(evidence_context, offer_id) -> OfferEvidence | Missing/Conflict`
- `resolve_candidate_group(evidence_context, candidate_group_id) -> CandidateGroupEvidence`
- `list_group_offers(evidence_context, candidate_group_id) -> ordered OfferEvidence`
- `prove_offer_membership(evidence_context, candidate_group_id, offer_id) -> MembershipProof`
- `resolve_selected_offer(evidence_context, candidate_group_id) -> SelectionEvidence | NoSelection`
- `read_projection_marker(tenant_id, project_id, source_id) -> ProjectionMarker`

Each result includes the exact evidence ID, source/snapshot digest, checkpoint, owner anchors, and observed writer generation. A missing result is distinct from an unclassified persistence failure; failures affecting promotion are durably classified by the quarantine path.

### Command ports

- `backfill_group_offers(batch, expected_writer_generation, operation_id)`;
- `backfill_tender_groups(batch, expected_writer_generation, operation_id)`;
- `save_candidate_group(command, expected_writer_generation, operation_id)`;
- `select_offer(command, expected_writer_generation, operation_id)`;
- `recalculate_procurement(command, expected_writer_generation, operation_id)`;
- `record_projection_marker(marker, expected_writer_generation, operation_id)`.

No command accepts a client-supplied tenant/project/supplier as the only proof. The repository resolves and validates those anchors in the transaction before mutation.

## 7. Association tables and JSONB backfill

The JSONB fields are compatibility representations, not referentially constrained canonical storage:

- `equipment_v2_candidate_groups.offer_ids` becomes `equipment_v2_candidate_group_offers`;
- `equipment_v2_tenders.candidate_group_ids` becomes `equipment_v2_tender_candidate_groups`.

The target association rows retain `tenant_id`, `project_id`, parent ID, target ID, positive `ordinal`, creation metadata, and named scoped FKs. Primary keys prevent duplicate pairs; deferred unique keys on `(parent_id, ordinal)` preserve deterministic ordering during transactional reorder. The selected state is not duplicated as a boolean association flag.

### Backfill protocol

Backfill is a versioned, bounded, resumable job in the normal Alembic/application delivery path. It is never an ad hoc shared-environment SQL operation.

Before the first insert for a partition, the job records:

- source row count and batch checkpoint;
- null/non-array/non-string JSON defects;
- duplicate IDs and duplicate ordinals;
- missing target IDs and orphan targets;
- tenant/project/source-chain mismatches;
- same-request violations for tender membership;
- selected-offer values that are not group members;
- source digest, target digest, operation ID, correlation ID, and writer generation.

For each parent, the job normalizes only string IDs, trims them, preserves first occurrence order, and rejects duplicates as defects. It does not coerce malformed JSON to an empty list. A missing referenced offer/group is a referential defect, not an empty association. Defects are fixed, quarantined, or explicitly accepted by policy; they are never silently skipped.

Insertion is idempotent by `(source_kind, source_parent_id, source_payload_digest, schema_version)` and the canonical parent/target pair. A retry may acknowledge an existing row only when its tenant/project, ordinal, target identity, and source digest are identical. An existing pair with a different digest or ordinal is a conflict and remains visible as quarantine evidence. `ON CONFLICT DO NOTHING` alone is insufficient and must not hide malformed or conflicting source data.

Backfill acceptance requires:

- normalized source count equals association count per parent;
- ordered source IDs equal ordered association IDs;
- no duplicate pair or ordinal and no orphan association;
- every non-null selected offer is a member of its group;
- tender group membership has the same request, tenant, and project;
- all source defects have a durable disposition;
- a rerun produces zero additional rows and no ordinal drift.

### Expand/contract sequence

1. Create empty association structures and migration audit instrumentation; JSONB remains authoritative.
2. Backfill in bounded parent-ID batches, retaining checkpoints and source JSONB.
3. Enable dual-write in one transaction; association rows are canonical for ordering and JSONB is generated as a mirror.
4. Shadow-read both forms and emit mismatch evidence; do not choose the first response.
5. Cut reads to associations only after the scoped observation window is clean.
6. Translate compatibility writes into association commands; reject direct canonical JSONB mutation.
7. Retain JSONB through the rollback window; removal is a later reviewed contract migration.

A single bad tenant/project partition pauses only that partition. Legacy reads/writes remain authoritative until its promotion record proves clean parity and writer fencing.

## 8. Selected-offer membership and approval

`selected_offer_id = NULL` means that no human selection exists. It is never interpreted as the first, cheapest, newest, or otherwise preferred offer.

A selection command must atomically:

1. resolve the server-side request, line, candidate group, tenant, cabinet, and project;
2. resolve the exact offer and snapshot;
3. prove `offer_id` is present in `candidate_group_offers` for that group;
4. verify offer/snapshot/source/supplier visibility and immutable provenance digest;
5. validate the human `approval_id` and command correlation;
6. compare the expected `writer_generation` and current selection version;
7. update the scalar `selected_offer_id` and append selection evidence/audit in the same transaction.

The database may use a scoped composite FK for the offer, but set membership requires a deferred constraint trigger or the repository transaction invariant. A plain FK to `catalog_offers` is insufficient.

Selection is an explicit human-approved transition. Candidate-group creation, matching suggestions, OpenSearch hits, or an agent-authored offer ID cannot set `selected_offer_id`. A stale selection request returns a typed CAS conflict; it does not select the latest offer automatically. A selected offer that later becomes stale remains historical evidence; new procurement calculations must fail closed until a fresh approved selection exists.

## 9. CAS, idempotency, and transaction boundary

All positive mutations and all evidence associated with them use one repository-owned transaction. The transaction includes:

- server-resolved tenant/cabinet/project/supplier/source anchors;
- exact snapshot/offer/group identity and provenance digest;
- expected writer generation and selection/request version;
- the business mutation (association, candidate group, selection, or projection marker);
- append-only audit/quarantine/mapping evidence required by the operation;
- the new operation checkpoint and correlation.

The mutation succeeds only when the complete expected identity and generation match. A stale generation, archived owner, changed snapshot digest, or cross-scope relation yields zero-row/no-side-effects and a durable fencing/conflict classification. No local retry may increment a generation or substitute the latest row.

Idempotency keys are partitioned by operation type and source identity. A repeated request with the same operation ID and identical canonical digest returns the original committed result/evidence ID. Reuse with a different digest, owner, snapshot, approval, or writer generation is an immutable conflict; it never overwrites the first result.

Unknown commit outcomes are treated as indeterminate. The adapter must read by operation identity and digest before retrying. It must not issue a compensating mutation, duplicate selection, or advance the projection marker without that reconciliation. If the result cannot be reconciled, the partition remains deny/quarantine and the legacy owner is retained.

Trusted and quarantine records have separate append paths and storage policies. Quarantine cannot be consumed as a verified matching, selection, procurement, or promotion result. Audit failure aborts the enclosing positive mutation.

## 10. OpenSearch projection contract

OpenSearch contains a derived catalog search projection. It is never the source of truth for tenant/project ownership, price history, source provenance, candidate membership, selected offer, or authorization.

The projection input is a committed `equipment_v2` snapshot plus its offers and source evidence. A projection marker in PostgreSQL records at least:

- tenant/company, optional cabinet, and project/shared-catalog policy;
- source ID, ingestion run, snapshot ID and snapshot content hash;
- projection/index identity, mapping version and alias/index name;
- operation ID, source checkpoint, document count and source digest;
- lifecycle (`queued|indexing|ready|error|stale`), heartbeat, completion time, and error classification;
- current writer generation and correlation/evidence ID.

A projection worker follows source-first semantics:

1. resolve and lock the committed source snapshot and projection operation identity;
2. open/read the source and establish count/checkpoint before touching the previous index;
3. create a new versioned physical index or safely prepare the target alias;
4. publish documents with deterministic IDs derived from snapshot/source-row identity and canonical supplier/offer identity;
5. persist progress after bounded chunks and retain the previous ready projection until the new one is complete;
6. atomically record the ready marker only after document count, source digest, mapping version, and visibility scope match;
7. on failure, mark the new attempt `error`, keep the last valid projection marker/index, and retain the database snapshot as valid evidence.

The current legacy OpenSearch implementation uses one physical index per catalog row, canonical fields including supplier, price, currency, stock, source hash, and company/project/cabinet metadata, and a stale-index sweep. The future adapter may retain that physical layout during compatibility, but its logical identity must be snapshot/offer based and its marker must be revisioned. A failed or stale projection never marks equipment data ready and never deletes normalized snapshots or raw artifacts.

Search results are advisory candidates. Before matching, selection, or procurement, the adapter re-resolves the exact snapshot/offer and runs the T06 visibility/evidence checks. A search hit cannot prove supplier visibility, candidate membership, selected-offer membership, or current availability.

Projection retries are idempotent by `(source_snapshot_id, mapping_version, projection_operation_id, source_digest)`. A same-identity retry may resume or replace only an incomplete attempt. A digest conflict creates quarantine evidence and leaves the prior ready projection untouched. Physical index cleanup is a later retention operation and cannot be used as rollback.

## 11. Failure, quarantine, and rollback

The adapter classifies at least these outcomes:

- `missing_owner_anchor`, `tenant_mismatch`, `cabinet_mismatch`, `project_mismatch`;
- `supplier_not_visible`, `source_chain_missing`, `source_digest_mismatch`, `snapshot_stale`;
- `offer_not_in_snapshot`, `candidate_membership_unproven`, `selected_offer_not_member`;
- `association_malformed`, `association_duplicate`, `association_orphan`, `same_request_mismatch`;
- `approval_missing`, `writer_generation_stale`, `idempotency_conflict`, `unknown_commit`;
- `projection_source_unavailable`, `projection_digest_conflict`, `projection_publish_failed`.

Every classification affecting promotion or cutover retains source identity, canonical digest, owner anchors, evidence ID, checkpoint, generation, correlation, reason, and observed time. Quarantine is not positive parity and does not authorize a retry as allow.

Before read cutover, disable the new adapter/dual-writer for the affected partition and continue legacy authority. After read cutover, rollback may select the retained JSONB/projection mirror only when its parity marker is clean. In all phases, retain snapshots, raw artifacts, association audit, evidence, and selections; rollback is a forward state transition or projection rebuild, never deletion to conceal a mismatch.

## 12. Acceptance gate for implementation

The persistence adapter is ready for a reviewed implementation only when all of the following are evidenced for the bounded partition:

- canonical tenant/company, cabinet, project, supplier, and source anchors are resolved and parent-compatible;
- RLS/FORCE RLS and parent `EXISTS` checks deny missing, wrong, archived, or cross-scope contexts;
- snapshot, offer, request, line, group, and tender reads return one immutable evidence snapshot with digest and checkpoint;
- all JSONB associations are backfilled with ordered, duplicate-free, same-scope rows and durable defect dispositions;
- selected offers are transactionally proven members of their candidate groups and still require human approval;
- positive mutations use one transaction with complete identity, append-only evidence, idempotency, and writer-generation CAS;
- stale generation, digest conflict, unknown outcome, and quarantine produce no positive side effect;
- OpenSearch is rebuildable from committed snapshots, has a revisioned marker, preserves the last ready projection on failure, and is never treated as authorization truth;
- dual-read parity, source/target counts, ordered arrays, selection membership, ownership, and projection digest are zero-mismatch for the agreed observation window;
- legacy ownership remains fenced only after an immutable promotion record records final delta, writer epoch, rollback window, and reviewer evidence.

This contract intentionally does not add ORM models, Alembic revisions, tests, routes, or changes to existing equipment documents. Tests and formal verification remain deferred under the migration verification freeze.

## Sources reviewed

- `apps/api/src/prodavan/application/equipment_visibility/` T06 contracts, ports, and policy service;
- `apps/api/src/prodavan/application/equipment_matching/` T06 commands, contracts, ports, and service;
- `apps/api/src/prodavan/infrastructure/persistence/models/equipment_v2.py`;
- `apps/api/src/prodavan/application/modules/equipment_catalog_opensearch.py`, `equipment_catalog_reindex_sweep.py`, and `equipment_catalog_search.py`;
- `docs/08-migration/equipment-association-tables.md`;
- `docs/08-migration/equipment-evidence-handoff.md`;
- `docs/08-migration/persistence-target-map.md`;
- `docs/08-migration/implementation-wave-cross-review.md`;
- `docs/08-migration/target-orm-schema-task-map.md`.
