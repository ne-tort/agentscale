# MIG-M06-02 promotion record

- Record ID: `MIG-M06-ADAPTER-FIX:unpartitioned:blocked:2026-11-04`
- Status: `blocked`; the shadow adapter, durable evidence store and bounded parity runner are
  authored and self-consistent, but no partition evidence exists and no promotion is authorized
- Partition key: `(tenant_id, app_definition_id)`; no source partition was supplied or admitted
- Source model: snapshots legacy `AppGrant` and `CapabilityGrant`; `grantset-v1`; no inventory or
  snapshot digest supplied
- Mapping model: `m06-grant-mapping-v4` / `operation-constraints-v1` /
  `capability-method-mapping-v1`; only explicitly approved capability operations, resources,
  domains and method→operation pairs map; everything else quarantines with a stable reason code
- M04 scope/RLS: no accepted partition-bound marker or production application-port binding
- M04E definition/version: no accepted partition-bound marker; the resolver stays fail-closed on
  unconfirmed upstream proof
- M05 instance/runtime: no accepted partition-bound marker or production runtime evidence binding
- M06 schema/writer: no accepted schema evidence, tenant-bound writer epoch, or production handoff
  resolver binding
- Evidence persistence: `SqlCanonicalEvidenceStore` implements the atomic
  `commit_evidence` unit (mapping, quarantine, shadow, audit) behind a tenant-bound
  transaction-local RLS/role readback and a writer-epoch CAS; `find_mapping` and
  `load_observations` are the read surfaces. Not constructed by any production composition root
  yet, and no RLS policy, append-only privilege or uniqueness review has been accepted
- Legacy comparison: the read-only adapter projects the current gateway repository, scope and
  quota decisions; it never invokes the executor. No production gateway composition hook exists
- Canonical comparison: read-only grant-list projection with exact binding, lifecycle and
  constraint checks; canonical grants never authorize or mutate a request in M06
- Parity: `BoundedParityRunner` walks one partition in a stable order under an explicit
  observation budget and page budget, resumes from a stable checkpoint cursor, and emits a report
  whose JSON export is byte-identical for identical evidence. Run keys are deterministic;
  identical content replays, changed content conflicts, and a partial traversal is never recorded.
  No observations, window, snapshot digest or writer epoch were supplied, so no partition was
  evaluated and no counts are claimed here
- Decision owner and canonical read owner: `legacy`
- `canonical_positive_writes`: `false`
- Promotion/cutover authorized: `false`
- Verification: acceptance behavior tests authored but not run. Tests, build, compile, lint,
  format, and Git checks remain frozen by task instruction
- DDL, live data, cutover, and environment changes: not performed

## What this revision proves (code level only)

1. The adapter, evidence store and parity runner form a closed, importable package: every module
   parses, the package imports, and all 74 exported names resolve.
2. The adapter's shadow path fails closed rather than degrading. It returns no observation and
   writes no evidence when the context is unauthenticated or unbound, the RLS context disagrees
   with the authenticated principal, the accepted partition handoff is missing or mismatched, the
   instance/version/runtime/writer generation is stale, the request identity digest differs from
   the server-resolved one, or the method vocabulary is not approved.
3. Negative paths are explicit evidence, not silence: unknown capability, unsupported capability
   operation, unapproved method, ambiguous capability entry, future-dated source, and
   `expires_at <= issued_at` quarantine; changed source digest, changed canonical digest and
   cross-tenant replay conflict while preserving the first mapping; quarantined evidence is
   repaired forward; expired/revoked grants are recorded as explicit known deny.
4. Replay is idempotent on identity: an exact `(source_kind, source_id, schema_version,
   source_digest)` replay appends shadow evidence only and never rewrites the mapping row, while a
   changed digest or binding is a conflict.
5. The parity report cannot transfer ownership: `decision_owner`/`canonical_read_owner` must be
   `legacy`, `canonical_positive_writes` must be `false`, the report digest is recomputed over its
   canonical content on construction, and unexplained mismatches are listed as digests only.
6. Bounded traversal is enforced in code, not by convention: a page that exceeds the requested
   limit, a source that never completes a truncated window, a non-monotonic cursor, duplicate
   observation evidence and a missing cursor each block the run; a partition narrowed to a version
   or instance that shadow observations cannot prove is refused before any read.

## Remaining blockers (each needs an owner decision, not more code in this zone)

- No accepted M04/M04E/M05/M06 partition-bound handoff evidence: the promotion gates for every
  upstream slice are open, so any run is blocked with `accepted_upstream_and_atomic_evidence_handoffs`.
- No production composition root or route: `build_shadow_gateway` has no caller, and no API
  handler was changed. Binding it is a cutover decision, not an adapter change.
- Durable store review outstanding: RLS policy for the mapping/audit rows, append-only privileges,
  and the `(source_kind, source_id, canonical_schema_version)` uniqueness contract have not been
  accepted against a live schema.
- Report persistence in production is unimplemented: `InMemoryParityReportStore` is a deterministic
  double for tests only and must never be composed in a production path; a durable run-key
  registry still has to be written and reviewed.
- Partition narrowing by `app_version_id`/`plugin_instance_id` is refused by the runner because
  shadow evidence carries no such anchor; either the evidence schema gains the anchor or parity
  stays computed at `(tenant_id, app_definition_id)` granularity.
- No observation window, source snapshot digest, final-delta checkpoint or reconciliation of
  unknown mappings has ever been produced for a real partition.

## Gate Decision

No partition is promotable. M06 returns no shadow observation and writes no evidence when
transaction-local RLS, the exact accepted upstream handoff, or the atomic persistence binding is
missing. Identity resolution composes the authenticated-company, M04 scope, M04E definition/version
and M05 exact-instance facades; their upstream blockers remain decisive. Legacy remains
authoritative for authorization, reads and writes.

Before promotion review, supply and bind accepted partition-specific M04 scope/RLS, M04E immutable
definition/version, M05 exact instance/runtime generation, M06 schema, final-delta checkpoint and
transaction-bound evidence-store proofs. Then run a bounded observation window and reconcile
unknown mappings, stale generations, allow/deny and reason mismatches, and complete audit evidence.
No counts are inferred in this record.

## Rollback

No production composition, target schema, source data, or durable evidence was changed. Keep the
affected partition on legacy reads, decisions, and writes. Preserve any future first mapping,
conflict, quarantine, audit, and shadow records; repair forward and repeat bounded parity. There is
no cutover or rollback command encoded by this record.
