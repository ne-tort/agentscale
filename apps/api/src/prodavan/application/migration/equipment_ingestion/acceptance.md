# MIG-EQ-01 acceptance contract

Status: specified; not executed under migration verification freeze.

## Source and identity

- Source adapters provide stable source kind/id/schema identity and bounded cursor pages; acquisition limits are enforced.
- Canonical tenant and owner identity plus verified supplier identity are mandatory. Incomplete or ambiguous identity blocks before positive append; no ID-prefix, display-name, URL, brand, or payload inference.
- Resume is bound to the same run/source/supplier identity. A changed identity or cursor/checkpoint CAS conflict stops the run without overwriting evidence.

## Evidence and normalization

- Every page's raw bytes are stored by immutable artifact reference and content digest before normalized rows can be accepted.
- Snapshot and offer identities retain tenant, owner, source, supplier, run, artifact, schema, source revision, observed time, and provenance digest.
- Normalization identity disagreement and unsupported/ambiguous source semantics are quarantined or blocked; never silently mapped to positive offers.
- Replays are idempotent for identical digest; same evidence identity with different bytes/content is conflict and cannot replace the first evidence.

## Freshness and projection

- Failed/blocked source refresh does not delete or overwrite last-good accepted snapshots; retained data reports stale. With no accepted history freshness is unknown, never fresh-empty.
- Projection is a derived, versioned, deterministic rebuild from accepted immutable snapshots, with source snapshot digest and generation-fenced publish contract. Projection failure does not mutate source evidence.
- Rebuild replay produces equivalent output for same snapshot set and contract version; a stale generation cannot publish after a newer accepted snapshot.

## Promotion gate / rollback

- No promotion without accepted canonical ownership/source/supplier mappings, bounded inventory, persistence/idempotency/CAS and immutable-retention evidence, explicit freshness policy, projection fencing, complete acceptance evidence and coordinator approval.
- Until then legacy/current source and read owner remain unchanged; canonical positive writes and cutover stay disabled.
- On later partition failure, halt only that partition, retain evidence, serve last-good as stale, and repair/rebuild forward; no destructive rollback of artifact/snapshot history.

Fixtures in `apps/api/tests/migration/equipment_ingestion/` are the executable acceptance specification and remain unrun until freeze is explicitly lifted.
