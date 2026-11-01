# MIG-M04-02 implementation promotion record

Record ID: `MIG-M04-02:blocked:application-contract-v1:2026-11-01`
Supersedes preflight record: `MIG-M04-02:blocked:preflight:2026-11-01`
Status: `blocked` for source backfill and active resolution; `implementation-ready-for-review` for fail-closed application contract and acceptance artifacts.
Partition: `unbounded / none admitted`.
Decision: preserve legacy owner; do not run/import source rows, enable database resolver, or promote any partition.

## Dependencies and inspected evidence

Consumed: MIG-M04-01 model foundation in `infrastructure/persistence/models/owner_registry.py` and its promotion record; `scope_registry` contracts/service/ports; `m04-persistence-adapter-contract.md`; `typed-scope-owner-registry.md`; `m04-m06-readiness-matrix.md`; `migration-execution-backlog.md`.

M04-01 supplies model declarations for typed tenant/cabinet/project anchors plus mapping/quarantine/audit foundations, but the model is not registered in `models/__init__.py` (outside this task zone), has no Alembic revision here, and has no installed RLS/privileges. Existing owner sources contain cabinet-company grants and project company/cabinet values, but task input provides no source snapshot, active membership rows, exact-one membership proof, or project compatibility inventory. No M05 identity is required or resolved; plugin scope remains deferred.

## Source/target promotion evidence

- Source snapshot ID/schema/digest: `NOT_ACQUIRED / NOT_INVENTORIED / NOT_AVAILABLE`.
- Source inventory accepted/quarantined counts by kind/reason: `UNKNOWN`; no source rows were read or migrated.
- Target digest: `NOT_AVAILABLE`; no target persistence adapter/database was available.
- Checkpoint: no committed source checkpoint; application contract advances one row only when the trusted transaction port confirms it.
- Exactly-one-active-row evidence: `ABSENT`.
- Membership and parent compatibility evidence: `ABSENT`; no cabinet/company or project/company/cabinet partition is admitted.
- RLS `ENABLE/FORCE`, `USING/WITH CHECK`, transaction-local context and role evidence: `ABSENT`.
- Writer/fencing generation: `NOT_PROVISIONED`; no target writer enabled.
- Actor: delegated implementation agent; exact timestamp/signature: not captured. Decision owner: migration coordinator; reviewer signature pending.
- Correlation: `MIG-M04-02:blocked:application-contract-v1:2026-11-01`.

## Deliverables in this task zone

- `scope_migration.py`: bounded one-row coordinator, stable canonical JSON digest, replay/conflict result vocabulary, caller-supplied snapshot/schema/checkpoint lineage, one-call trusted transaction port, exact context-bound active-only resolver, fail-closed no-port/no-context behavior, source assertion comparison and plugin deny/deferred handling. Accepted results must return transaction-bound parent proof and exact typed canonical identity; source identity/schema/digest/correlation are checked before result acceptance. Changed digest or changed typed identity must conflict without overwriting the first mapping. Sequence-CAS checkpoint plus mapping/quarantine/redacted audit must be atomic; these semantics remain unverified without an adapter.
- `__init__.py`: task-local public exports.
- `tests/migration/m04_scope/acceptance_fixtures.py`: deterministic in-memory transactional fake for unexecuted acceptance scenarios; explicitly not a persistence adapter.
- `tests/migration/m04_scope/test_acceptance_contract.py`: focused async acceptance tests.
- `tests/migration/m04_scope/acceptance_matrix.md`: static coverage and safety boundary, including resume/replay, redacted audit, and returned-evidence mismatch assertions.

## Gates and unresolved blockers

The coordinator makes one atomic persistence-port call per candidate. That port must establish transaction-local context, derive tenant/cabinet/project and active membership proof inside the same transaction, compare source identity/schema/digest before writes, preserve the first mapping on changed-digest conflict, and append mapping/quarantine/audit plus sequence-CAS checkpoint atomically. The port returns the proof used by that transaction; accepted outcome without valid returned proof is rejected by the coordinator. These are required port semantics, not verified DB behavior. `ScopeResolver` requires exact tenant/cabinet/project GUC match and one active row plus proof returned by one transaction; plugin scope always returns deferred.

Blockers to backfill/readiness: (1) no source snapshot/inventory or bounded partition; (2) cabinet/company active membership ambiguity and project parent compatibility have no source proof; (3) no concrete transaction-local RLS persistence adapter, atomic evidence store or DB uniqueness proof; (4) RLS/FORCE policies and restricted/append-only evidence privileges absent; (5) M04-01 model registration/revision is outside this exclusive zone and not ready for executable migration; (6) no source/target digests, accepted/quarantined counts or reviewer signatures. No fix in this zone can safely replace these upstream evidence/persistence requirements.

## Rollback and owner decision

No source rows were imported and no DB writes occurred; no data rollback is required. Preserve both this record and the superseded preflight record. If a later bounded partition fails after import, stop only that partition, leave legacy read/write authority unchanged, preserve mapping/quarantine/audit and checkpoint, and repair forward. Never rewrite scope identity, delete evidence, or switch ownership from this record.

Promotion gate remains `BLOCKED`: do not admit a partition until canonical tenant/typed anchors, source snapshot/digest, active membership and parent compatibility, exact-one active scope evidence, transaction-local tenant context, reviewed RLS/privileges, durable atomic adapter and complete per-partition checkpoints are supplied. `plugin_instance` remains deny/deferred until downstream M05 exact instance and reciprocal FK validation.

Verification freeze honored: acceptance tests and all tests/build/lint/compile/format/migration/git checks were intentionally not run. No source backfill, positive write, shared environment change, commit or push occurred.
