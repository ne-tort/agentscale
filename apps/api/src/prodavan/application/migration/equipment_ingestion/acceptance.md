# MIG-EQ-01 acceptance contract

Status: specified; not executed under migration verification freeze.

MIG-EQ-SOURCE-INGESTION-03 source-handoff analysis: blocked. The actual legacy
`mod_equipment.catalogs` import journey and the required route/queue/persistence
handoff are recorded in [source-handoff.md](source-handoff.md). Keep its current
Celery indexer/OpenSearch read owner authoritative; do not infer canonical
source/supplier identity or dual-index.

## Source and identity

- Source adapters provide stable source kind/id/schema identity, bounded cursor pages, and raw bytes with an explicitly verified digest; both record count and artifact bytes are bounded before evidence commit.
- Canonical tenant and owner identity plus verified supplier identity are mandatory. Incomplete or ambiguous identity blocks before positive append; no ID-prefix, display-name, URL, brand, or payload inference.
- Resume is bound to the same run/source/supplier identity. A changed identity or cursor/checkpoint CAS conflict stops the run without overwriting evidence.
- The legacy catalog path is not an accepted adapter until its module owner and file-version/remote source are resolved to canonical tenant/owner/source and an explicit verified supplier mapping; seller labels and `src_hash` are insufficient.
- A source handoff must reuse the existing equipment catalog Celery worker/queue and current user import routes after a durable run/outbox commit; it must not introduce a parallel indexer or duplicate legacy catalog indexing.

## Evidence and normalization

- Every page is committed through one CAS transaction: immutable raw bytes/artifact, that page's snapshot, run counters and the advanced checkpoint either all persist or none do. A checkpoint never advances on a raw/snapshot/run failure.
- Each snapshot contains only the accepted offers from its own page. Observed, accepted and quarantined row counts remain separate; malformed rows retain redacted row digest/error code while their bytes stay in raw evidence.
- Offer normalizers validate an uppercase three-letter currency token, finite nonnegative decimal prices, supported availability (including rejection of `on_order`), deterministic offer identity, source/supplier separation and artifact/snapshot/source-record provenance. A downstream ISO 4217 registry may apply stricter policy without inferring a currency.
- Exact page replay is idempotent. Reusing the same run/cursor/artifact identity with changed bytes or normalized content is an immutable collision and cannot overwrite accepted evidence.

## Freshness and projection

- Source failure is recorded separately from immutable last-good snapshots; failure markers make stale freshness observable without rewriting accepted snapshot history.
- Projection is a derived, versioned, deterministic rebuild from accepted immutable snapshots with a digest of the complete scoped input set. Publish atomically compares both projection generation and that input digest; a newly accepted snapshot fences out stale rebuilds. Projection failure does not mutate source evidence.
- Rebuild replay produces equivalent output for same snapshot set and contract version; a stale generation cannot publish after a newer accepted snapshot.

## Promotion gate / rollback

- No promotion without accepted canonical ownership/source/supplier mappings, bounded inventory, persistence/idempotency/CAS and immutable-retention evidence, explicit freshness policy, projection fencing, complete acceptance evidence and coordinator approval.
- Until then legacy/current source and read owner remain unchanged; canonical positive writes and cutover stay disabled.
- On later partition failure, halt only that partition, retain evidence, serve last-good as stale, and repair/rebuild forward; no destructive rollback of artifact/snapshot history.

Fixtures in `apps/api/tests/migration/equipment_ingestion/` are the executable acceptance specification and remain unrun until freeze is explicitly lifted.
