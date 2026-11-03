# MIG-M06-02 promotion record

- Record ID: `MIG-M06-02:unpartitioned:blocked:2026-11-03`
- Status: `blocked`; shadow comparison implementation is not promotion evidence
- Partition key: `(tenant_id, app_definition_id)`; no source partition was supplied or admitted
- Source model: snapshots legacy `AppGrant` and `CapabilityGrant`; `grantset-v1`; no inventory or snapshot digest supplied
- Mapping model: `m06-grant-mapping-v3` / `operation-constraints-v1`; only explicit bounded capability operations/resources/domains map; unsupported semantics quarantine
- M04 scope/RLS: no accepted partition-bound marker or production application-port binding
- M04E definition/version: no accepted partition-bound marker; existing resolver remains fail-closed on unconfirmed upstream proof
- M05 instance/runtime: no accepted partition-bound marker or production runtime evidence binding
- M06 schema/writer: no accepted schema evidence, tenant-bound writer epoch, or production handoff resolver
- Evidence persistence: atomic `commit_evidence` and global source-key `find_mapping` are contracts only; no durable adapter, RLS/uniqueness proof, or append-only quarantine/audit store is bound
- Legacy comparison: read-only adapter projects current gateway repository, scope and quota decisions; it does not invoke the executor. No production gateway composition hook is present
- Canonical comparison: read-only grant-list port projection; no production persistence binding. Canonical grants do not authorize or mutate requests in M06
- Parity: no observations, mismatch counts, source/canonical/binding digests, or observation window supplied; not evaluated
- Decision owner and canonical read owner: `legacy`
- `canonical_positive_writes`: `false`
- Promotion/cutover authorized: `false`
- Verification: acceptance behavior tests authored but not run. Tests, build, compile, lint, format, and Git checks remain frozen by task instruction
- DDL, live data, cutover, and environment changes: not performed

## Gate Decision

No partition is promotable. M06 returns no shadow observation or evidence write when transaction-local RLS, exact accepted M04/M04E/M05/M06 handoff, or atomic persistence binding is missing. Identity resolution composes the existing authenticated-company, M04 scope, M04E definition/version, and M05 exact-instance/runtime facades; their upstream blockers remain decisive. Legacy remains authoritative for authorization.

Before promotion review, supply and bind accepted partition-specific M04 scope/RLS, M04E immutable definition/version, M05 exact instance/runtime generation, M06 schema, final-delta checkpoint, and transaction-bound evidence-store proofs. Then run a bounded observation window and reconcile unknown mappings, stale generations, allow/deny and reason mismatches, and complete audit evidence. No counts are inferred in this record.

## Rollback

No production composition, target schema, source data, or durable evidence was changed. Keep the affected partition on legacy reads, decisions, and writes. Preserve any future first mapping, conflict, quarantine, audit, and shadow records; repair forward and repeat bounded parity. There is no cutover or rollback command encoded by this record.
