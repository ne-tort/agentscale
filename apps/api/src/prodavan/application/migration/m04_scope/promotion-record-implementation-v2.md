# MIG-M04-02 blocked implementation record v2

Record ID: `MIG-M04-02:blocked:application-contract-v2:2026-11-02`
Supersedes: `MIG-M04-02:blocked:application-contract-v1:2026-11-01`
Status: `blocked`; application contracts and static acceptance scenarios only.
Partition: `none admitted`.
Decision: keep legacy owner; do not backfill, enable resolver, or promote.

## Evidence

- Source snapshot/schema/digest: `NOT_ACQUIRED / NOT_INVENTORIED / NOT_AVAILABLE`.
- Source and target counts/digests: `UNKNOWN / NOT_AVAILABLE`; no source row was read or imported.
- Current/persisted checkpoint: `NOT_AVAILABLE`; this task defines a stream identity and atomic CAS port contract but no checkpoint store.
- Exactly-one active typed scope and tenant/cabinet/project compatibility: `ABSENT`.
- Active cabinet-company membership/project tuple evidence: `ABSENT`.
- Transaction-local RLS, ENABLE/FORCE policies, privileges and append-only audit enforcement: `ABSENT`.
- Writer generation/fencing evidence: `NOT_PROVISIONED`.
- Correlation: `MIG-M04-02:blocked:application-contract-v2:2026-11-02`.
- Reviewer: pending coordinator review.

## Deliverables in this task zone

- `scope_migration.py`: single-row mapping coordinator and trusted transaction port contract; source digest covers source identity, source/canonical schema versions, lifecycle, payload digest and supplied source anchors; checkpoint binds tenant, stream/snapshot/schema and canonical schema with source snapshot digest lineage; anchor-bound parent cardinality/lifecycle evidence; redacted quarantine DTO for source IDs 41-128 without truncation into typed provenance; active-only `ResolveScopePort`; plugin scope deferred. No adapter is wired.
- `__init__.py`: exports for task-local contracts.
- `tests/migration/m04_scope/acceptance_fixtures.py`: typed source/anchor/checkpoint fixtures only; no fake persistence adapter.
- `tests/migration/m04_scope/test_acceptance_contract.py`: static acceptance cases for fail-closed wiring, lifecycle/cross-scope classification, cardinality, checkpoint identity, plugin deferral and source ID width. Not executed.
- `tests/migration/m04_scope/acceptance_matrix.md`: updated to list contract-only cases and remaining runtime wiring gaps.

## Dependencies and blockers

Owner-registry ORM declarations and model registration are readable, but this zone has no concrete transaction adapter, checkpoint persistence owner, or active resolver binding. No Alembic scope/owner revision was found. No source snapshot or parent proof is supplied. The typed provenance/quarantine contracts cap `source_id` at 40 while owner mapping evidence columns allow 128; the M04 quarantine DTO preserves 41-128 IDs but cannot be claimed durable until a persistence adapter stores it. RLS and restricted append-only privileges remain unverified.

The canonical source-mapping key is `(source_kind, source_id, canonical_schema_version)`, matching the owner mapping uniqueness contract. `source_schema_version` is retained in provenance/digest: if it changes under the same canonical mapping key, the port must return conflict/quarantine rather than create a second mapping.

## Promotion and rollback

Promotion gate: `BLOCKED`. Legacy remains read/write authority; `canonical_positive_writes = false`. Do not admit a partition until M04-01 foundations, bounded source snapshot/digest, exact active tenant/cabinet/project membership and tuple proof, a durable atomic mapper/checkpoint/audit adapter, reviewed RLS/privileges, and zero unresolved required parent mappings exist. Plugin-instance resolution remains deferred until M05 exact instance identity and reciprocal validated relations.

No rows were imported and no DB state changed, so no data rollback is required. After any future import failure, halt only the affected partition, preserve mapping/quarantine/audit/checkpoint, and repair forward while legacy remains authoritative. No dual-owner cutover is authorized by this record.

Verification freeze honored: acceptance tests were authored but not executed. No test/build/lint/compile/format/git checks, DDL, migration, cutover, commit or push was run.