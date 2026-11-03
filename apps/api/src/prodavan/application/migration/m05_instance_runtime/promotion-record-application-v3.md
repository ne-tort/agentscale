# MIG-M05-02 application promotion record v4

Record ID: `MIG-M05-02:blocked:instance-runtime-fencing-contract-v4:2026-11-03`
Supersedes implementation status: `MIG-M05-02:blocked:instance-runtime-fencing-contract-v3:2026-11-03`
Status: `blocked`; application contracts and unexecuted acceptance tests only.
Partition: `unbounded / none admitted`.
Decision: `do not import, publish, resolve, promote, or cut over`.
Canonical positive writes: `false`.
Legacy runtime: `read/write owner`.

## Inspected contracts

- M04 scope: `m04_scope/scope_migration.py` exposes active-only scope resolution and trusted transaction ports; this task did not find a trusted persistence adapter or admitted bounded partition.
- M04E: `m04e_definition_version/backfill.py`, `repository.py`, and `readiness.py` expose definition/version evidence and a handoff marker; readiness remains `UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED = False`, and the checked promotion record has no admitted source rows/partition.
- M05-01: `infrastructure/persistence/models/plugin_instance_registry.py` declares exact instance identity, lifecycle, reciprocal staged registry links, `runtime_generation`, and `writer_generation`. The model is not proof of installed constraints, validated FKs, RLS, or a trusted CAS adapter; it was not edited.
- Subject/session: authenticated subject source is `domain/identity/types.py::Principal.sub`. `infrastructure/persistence/models/agent.py::AgentSessionRow` binds a session to `project_id` but has no subject/tenant ownership field. No trusted subject-to-session ownership adapter/evidence was found; M05 therefore requires an explicit server-side proof port and remains blocked until supplied.
- Runtime: project runtime revisions use hydrate/bridge generations; these are not proof of plugin-instance runtime/writer generation ownership. No durable M05 generation/CAS adapter was found.

## Delivered in this application slice

- Snapshot-pinned resumable import contracts, exact instance resolver and lifecycle/generation service remain application-port only.
- Import proof now requires active M04 installation scope, exact active plugin-instance scope, exact M04E definition/version/artifact, authenticated tenant/subject/session match, and a separate active subject/session evidence digest. Missing evidence cannot yield an imported binding.
- Unsupported source lifecycle/availability values quarantine instead of throwing into a positive path. Duplicate exact instance identities remain non-positive.
- Quarantine retry re-reads one exact row from the immutable snapshot, requires the original digest and quarantine record, keeps retry attempts append-only/contiguous, uses expected-attempt CAS and atomic redacted audit through ports, and cannot replace the original mapping. M06 handoff validates and digests retry history before accounting a resolved retry.
- Runtime restart requests compare both generations and request exactly `runtime_generation + 1` and `writer_generation + 1`; writer fencing is an exact expected-generation CAS. Missing/stale workers deny. No adapter implements these contracts.
- Source identity width is reconciled to the registered `m05_plugin_instances.source_id String(80)`: candidate intake, checkpoint cursor, import/retry records, retry history validation, and serialized M06 row/retry evidence all accept at most 80 characters, preserve the exact source ID verbatim, and fail closed above 80. The source uniqueness key remains the full `(source_kind, source_id, schema_version)` tuple; no normalization or truncation was added.
- M06 record is evidence-only. It requires source accounting, final delta, parity, upstream evidence, zero unresolved mappings/bindings, exact retry evidence, and a drained matching legacy fence. `legacy_read_write_owner=legacy` and `canonical_positive_writes=false` remain explicit.

## Gate and counts

- M04/M04E partition-bound gate evidence and persisted `MIG-M04E-02` marker: `NOT_AVAILABLE`.
- Source snapshot/schema/digest and source inventory: `NOT_ACQUIRED`; no source rows were read.
- Imported / replayed / quarantined / conflicted source counts: `NOT_SAMPLED` (not zero-count evidence).
- Target digest/checkpoint, exact reciprocal FK validation, RLS review, atomic persistence/audit, runtime CAS, writer CAS, and writer-fence evidence: `NOT_AVAILABLE`.
- Subject/session ownership evidence and session adapter: `NOT_AVAILABLE`.
- Runtime/writer generations: `NOT_PROVISIONED / NOT_MUTATED`.
- Source ID width acceptance: authored for lengths 40, 41, and 80, with exact replay/order/checkpoint preservation; length 81 is rejected before persistence or retry lookup. These are unexecuted acceptance artifacts, not inventory evidence.
- M06 handoff: `BLOCKED`; no promotion marker was persisted and no canonical positive write/cutover is authorized.

## Required unexecuted acceptance scenarios

The task-owned `test_runtime_acceptance.py` covers same-digest replay, changed-digest conflict, source checkpoint replay/conflict, source-ID boundaries at 40/41/80/81, verbatim max-width replay/order/checkpoint and serialized handoff evidence, missing and inactive subject/session proof, stale runtime and writer generations, restart increment and zero-row CAS denial, duplicate exact instances, unsupported lifecycle, cross-tenant denial, quarantine retry and digest conflict, and M06 handoff with retry evidence. `acceptance_blocked.md` records the broader source, adapter, RLS, concurrency, and rollback matrix. These are acceptance specifications only; no tests were run.

## Freeze, rollback, and next gate

No ORM/model, DDL, source data, database, environment, runtime, or canonical state was changed. No import, production wiring, cutover, or cleanup occurred. Rollback is a no-op. Legacy remains authoritative. On a future partition failure, stop only that partition, preserve original mappings/checkpoints/quarantine/retry/audit evidence, deny stale workers, and repair forward with a newer generation; never restore an old worker or delete evidence.

Promotion remains blocked until reviewed M04/M04E partition evidence and release readiness are available; the M05-01 key and reciprocal FKs/RLS are validated; trusted session ownership is evidenced; durable transaction-local proof, immutable mapping/checkpoint, quarantine/retry/audit and database-side runtime/writer CAS adapters exist; and final-delta, parity, and legacy-writer-fence receipts are supplied. The immutable promotion port must persist the M06 handoff before it can be accepted for review. The source-ID width blocker is resolved at the application-contract layer only; no positive path is enabled or gate widened by this record.

Verification freeze honored: no tests, build, compile, lint, format, or git checks were run. Acceptance artifacts remain intentionally unexecuted and the promotion status remains `blocked`.