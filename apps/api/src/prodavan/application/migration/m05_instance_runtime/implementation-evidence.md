# MIG-M05-02 implementation evidence

Task: `MIG-M05-02`
Status: `implementation contracts and acceptance suite authored; operational gate blocked`

## Implemented in this slice

- Added M05 application contracts for partition-bound M04 typed scope, M04E definition/version export and M05 runtime/schema readiness. The upstream gate is bound to digest-linked evidence, exact partition and source snapshot; missing upstream gates block before candidate reads or positive operations.
- Added exact instance identity, including distinct installation scope and required `plugin_instance` scope; resumable source snapshot/checkpoint models; bounded import orchestration; same-digest replay; changed-digest conflict; quarantine for missing/mismatched parent, reciprocal plugin scope, definition/version/artifact, subject/session or generation proof.
- Defined `InstanceImportStore` contract for transaction-local context, parent/definition/version/artifact re-proof, immutable mapping, checkpoint CAS and redacted audit append in one atomic persistence unit. No durable adapter is present.
- Added exact resolver, lifecycle transition, runtime-generation and writer-generation CAS ports. Positive resolve/publish requires authenticated tenant/subject/session binding, active installation and plugin-instance registry proofs, M04E exact definition/version/artifact proof, active lifecycle/runtime and exact generations. Revoked/quarantined states are terminal; runtime restart advances runtime and writer generations together.
- Added M06 handoff record builder with source accounting, final-delta checkpoint, parity evidence, legacy-writer fence, zero unresolved mapping/binding gate, gate evidence, actor/decision/correlation/generation fields. Canonical positive writes remain explicitly false; legacy remains read/write owner.
- Tightened upstream readiness to require the complete M04/M04E evidence set: exact gate set and digest links, completed M04E version-phase checkpoint, accepted definition/version proof rows, canonical identity digest, persisted `MIG-M04E-02` marker, one active scope, and shared correlation/writer lineage. Missing or malformed marker/proof evidence blocks before a candidate read.
- Pinned import replay/conflict lookups to `(tenant_id, app_definition_id, source_kind, source_id, schema_version)`, rejected invalid/ahead/mismatched durable cursors, duplicate page identities and duplicate exact instance identities, and added redacted blocked audit for cursor failures.
- Added application-side full identity proof before generation CAS while retaining the trusted adapter requirement for transaction-local RLS, affected-row CAS and atomic audit rollback. Writer/runtime operations require authenticated subject/session, exact registry relation, definition/version/artifact proof and monotonic next generations.
- Added acceptance scenarios in `test_runtime_acceptance.py` and the detailed frozen matrix in `acceptance_blocked.md`.

## Dependencies and blockers

- M04 scope foundation and parent/membership/RLS validation are not proven for a bounded partition; its existing plugin-instance branch remains deferred until exact M05 instance key and reciprocal registry link validation.
- M04E source export in the inspected baseline has blocked promotion record and no admitted source rows. There is no verified partition-bound `MIG-M04-01` + `MIG-M04E-01` evidence bundle to consume.
- No source snapshot, target database evidence schema, ORM/DDL, trusted transaction adapter, RLS policy evidence, database CAS adapter, reciprocal FK validation record, audit sink guarantee, runtime owner, exact inventory, final-delta/parity evidence, or legacy writer fence was supplied.
- Consequently no import was run, no instance was admitted/published/resolved, and no M06 promotion/cutover is authorized. Application ports are contracts and do not prove production implementation.

## Files

- `apps/api/src/prodavan/application/migration/m05_instance_runtime/runtime_service.py`
- `apps/api/src/prodavan/application/migration/m05_instance_runtime/__init__.py`
- `apps/api/src/prodavan/application/migration/m05_instance_runtime/implementation-evidence.md`
- `apps/api/tests/migration/m05_instance_runtime/test_runtime_acceptance.py`
- `apps/api/tests/migration/m05_instance_runtime/acceptance_blocked.md`
- Removed obsolete shallow fixture `apps/api/tests/migration/m05_instance_runtime/test_acceptance_fixtures.py`; replacement suite covers application acceptance scenarios.
- The original `promotion-record.md` remains an immutable blocked preflight and is not represented as a fresh M06 promotion.

## Rollback and freeze

No source rows, shared state, database, ORM, schema, runtime or canonical writer were touched. Rollback is no-op. For any future partition import failure: halt only that partition, preserve checkpoints/mappings/quarantine/audit, leave legacy authoritative, and repair forward; never erase evidence or cut over by fallback. Tests, builds, compilation, lint, formatting and git checks were intentionally not run under the migration verification freeze.
