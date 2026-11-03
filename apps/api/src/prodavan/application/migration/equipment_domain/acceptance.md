# MIG-EQ-02 acceptance contract

Status: specified; fixtures intentionally not executed under migration verification freeze.

## Typed domain and identity

- Requests, request lines, candidate groups, tenders, and budget projections are immutable typed records with explicit IDs and ownership/source/project evidence. Budget totals derive from line quantity and tender unit price without collapsing supplier/source identity.
- Request/line/group/tender links are checked for existence and tenant/owner/project/source/supplier scope before any positive projection is exposed. A blocked projection contains no positive rows.
- Tender identity includes project, supplier, source, source-record identity, line and candidate group. Part number is a matching attribute only: equal P/N under different suppliers or sources remains separate; repeated supplier/source/source-record rows within one line/group block. Duplicate primary row identities are blocked.
- Missing or ambiguous owner/source/supplier evidence is fail-closed. No tenant, owner, supplier, source, or relation is inferred from labels, P/N, IDs, brand, or JSONB payload claims.

## JSONB association backfill

- Backfill accepts only an ordered JSON array of nonempty relation IDs and a stable owner ID.
- One typed edge is emitted per array entry, with its original ordinal. Order and duplicate occurrences are preserved; no sorting, deduplication, or guessed relation is allowed.
- The original JSONB row is retained on the association record as evidence. Invalid shapes block the affected backfill rather than partially guessing.
- Application service verifies each target against the exact tenant/owner/project scope and sends source revision + source digest with edges to the port. The port must make writes idempotent/resumable and fence append atomically against concurrent source changes.

## Scoped deterministic projection

- Projection inputs are ordered deterministically by stable IDs/ordinals and hashed using a canonical serialization; equivalent input permutations yield the same output and digest.
- Duplicate primary identities, missing references, cross-scope links, incomplete evidence, and duplicate supplier/source/source-record identity block the projection.
- A blocked projection exposes no positive request/line/group/tender payload.
- Projection is derived and rebuildable; it is not an authoritative write model and does not make OpenSearch or a budget sheet the source of truth.

## Human decisions and export

- Candidate discovery/projection never selects a tender. Service resolves trusted actor kind/scope; only a human actor can select. Selection requires immutable ID, explicit line and tender, current projection digest and nonempty reason. Replacement must explicitly supersede the current selection for that line.
- Stale, unknown-freshness, on-order or insufficient-quantity tenders remain visible for review but cannot be selected or exported. Budget rows mark procurement eligibility explicitly.
- A procurement export preview can be prepared only from a ready scoped projection and explicit current human selections matching actor and line; it contains selected line/source/supplier/quantity/price/totals and never chooses a best offer. `commit_export` is the separate append-through-transaction-port operation and has no external submit behavior.
- A source generation is part of every decision/export fence. A stale generation, changed source revision, changed projection digest, changed active selection set, duplicate active line, or duplicate vendor/source/source-record identity blocks the transaction.
- Human selection carries an explicit manual-override marker and is append-only. Replacing a line choice requires an explicit supersession of the current active selection by the same actor; a retry with the same scoped idempotency key and payload is a replay, while a changed payload is a conflict.
- The export transaction verifies selected line membership and the exact selected-line set, authoritative supplier/source/source-record provenance, availability, exact quantity × unit-price totals, currency totals and scoped export idempotency in one adapter transaction. A failure after the adapter may have committed returns an unknown outcome for reconciliation, never success.
- Concrete persistence/API/UI integration and final workbook generation remain out of scope. If no adapter is wired, the service returns a fail-closed blocked outcome and cannot claim durable selections or exports.

## Promotion and rollback

- No promotion without canonical tenant/owner/source/supplier mappings; JSONB inventory and preservation proof; bounded partition; append/idempotency/CAS evidence; accepted scoped projection and deterministic replay; human audit and export authorization; acceptance suite run and reviewed; coordinator approval.
- Until then canonical positive writes and cutover stay disabled; existing equipment read/write owner remains authoritative. An embedding service must not treat the port contract as production authorization.
- On failure, halt only the affected partition, retain source/association evidence, keep the previous accepted projection/read owner, and repair/rebuild forward. Do not destructively rewrite original JSONB or accepted evidence.

## Transaction scenarios

The task-owned acceptance tests cover duplicate/cross-tenant rows, stale and unknown availability, selected line and candidate-group membership, explicit manual override, quantity/price/total reconciliation, duplicate export idempotency, exact replay, changed-payload conflict, source-generation fencing, and failure-after-commit uncertainty. They are authored but intentionally unrun during the migration verification freeze.

Fixtures in `apps/api/tests/migration/equipment_domain/` are the executable acceptance specification and remain unrun until the migration coordinator explicitly lifts the verification freeze.
