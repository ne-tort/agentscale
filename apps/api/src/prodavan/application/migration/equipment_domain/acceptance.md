# MIG-EQ-02 acceptance contract

Status: specified; fixtures intentionally not executed under migration verification freeze.

## Typed domain and identity

- Requests, request lines, candidate groups, tenders, and budget projections are immutable typed records with explicit IDs and ownership/source evidence. Budget totals derive from line quantity and tender unit price without collapsing supplier/source identity.
- Request/line/group/tender links are checked for existence and tenant/owner/source/supplier scope before any positive projection is exposed.
- Tender identity includes supplier, source, and source-record identity. Part number is a matching attribute only: equal P/N under different suppliers or sources remains separate.
- Missing or ambiguous owner/source/supplier evidence is fail-closed. No tenant, owner, supplier, source, or relation is inferred from labels, P/N, IDs, brand, or JSONB payload claims.

## JSONB association backfill

- Backfill accepts only an ordered JSON array of nonempty relation IDs and a stable owner ID.
- One typed edge is emitted per array entry, with its original ordinal. Order and duplicate occurrences are preserved; no sorting, deduplication, or guessed relation is allowed.
- The original JSONB row is retained on the association record as evidence. Invalid shapes block the affected backfill rather than partially guessing.
- Persistence adapter must make writes idempotent and resumable and must fence publication against concurrent source changes before promotion.

## Scoped deterministic projection

- Projection inputs are ordered deterministically by stable IDs/ordinals and hashed using a canonical serialization; equivalent input permutations yield the same output and digest.
- Duplicate primary identities, missing references, cross-scope links, incomplete evidence, and duplicate supplier/source/source-record identity block the projection.
- A blocked projection exposes no positive request/line/group/tender payload.
- Projection is derived and rebuildable; it is not an authoritative write model and does not make OpenSearch or a budget sheet the source of truth.

## Human decisions and export

- Candidate discovery/projection never selects a tender. Selection requires a human actor, explicit line and tender, and nonempty reason.
- Procurement export can be prepared only from an accepted scoped projection and explicit human selections matching the actor and line; it contains selected tender IDs only and does not choose a best offer.
- External procurement side effects, final budget workbook generation, persistence, API routes, and UI integration are intentionally not performed by this contract.

## Promotion and rollback

- No promotion without canonical tenant/owner/source/supplier mappings; JSONB inventory and preservation proof; bounded partition; append/idempotency/CAS evidence; accepted scoped projection and deterministic replay; human audit and export authorization; acceptance suite run and reviewed; coordinator approval.
- Until then canonical positive writes, selection persistence, procurement export side effects, and cutover stay disabled; existing equipment read/write owner remains authoritative.
- On failure, halt only the affected partition, retain source/association evidence, keep the previous accepted projection/read owner, and repair/rebuild forward. Do not destructively rewrite original JSONB or accepted evidence.

Fixtures in `apps/api/tests/migration/equipment_domain/` are the executable acceptance specification and remain unrun until the migration coordinator explicitly lifts the verification freeze.
