# MIG-M04-SCOPE-PERSISTENCE-01 promotion handoff

Status: `blocked`; `canonical_positive_writes=false`; legacy scope ownership remains authoritative. Record: `MIG-M04-SCOPE-PERSISTENCE-01:blocked:2026-11-03:missing-db-gates`.

## Scope and files

Owned implementation: `apps/api/src/prodavan/infrastructure/persistence/migration_scope/adapter.py`. Owned acceptance tests: `apps/api/tests/migration/m04_scope/persistence/test_sqlalchemy_adapter.py`. No coordinator, shared route, ORM, Alembic revision, live database, or data was changed. The adapter is not bound in a composition root; production coordinator/resolver wiring remains absent.

## Inspected evidence

- `models/owner_registry.py`: typed tenant/cabinet/project anchor, scope registry, source mapping, quarantine, and audit row declarations exist. `models/__init__.py` imports/exports those owner-registry ORM classes. This is metadata only, not installed schema evidence.
- `models/owner_registry.py`: `ScopeOwnerMappingRow` unique key is `(source_kind, source_id, schema_version)` and stores `source_payload_digest`, but has no `canonical_schema_version`. The first mapping cannot establish canonical-schema replay identity from that row alone.
- No M04 checkpoint ORM/table or durable key is declared. `ScopeRegistryRow.checkpoint` and mapping/quarantine checkpoint strings are not a checkpoint cursor/CAS model. There is no source snapshot ID/digest, stream identity, expected sequence, or writer generation cursor row.
- No Alembic revision containing `scope_registry`, `scope_owner_mappings`, or `scope_owner_audit` was found in the inspected revisions. No executable/current-database schema was queried.
- `ScopeOwnerAuditRow` has no tenant ID or source mapping FK and no database event idempotency key. `ScopeOwnerQuarantineRow` has optional resolved tenant but no mandatory tenant owner FK. Existing ORM metadata does not encode append-only mutation denial, RLS, `ENABLE/FORCE`, or table grants. Tenant isolation and immutable audit therefore cannot be asserted from ORM metadata.
- `MembershipRow` is unique by `(company_id, employee_id)` but has no `status` or `deleted_at`. It does not itself prove active membership lifecycle. `EmployeeRow` has status/deleted state, but joining it does not prove a lifecycle for the membership relationship.
- `CabinetCompanyGrantRow` has a unique cabinet/company key and `status`; `CabinetInstanceRow` lifecycle and owner/grant-scope alternatives require reviewed policy before treating exactly one active company grant as the universal membership rule.
- `ProjectRow` has separate `company_id` and `cabinet_id` FKs. Its status includes deleted/paused/etc., and independent FKs do not prove a supported, exact project/cabinet/company policy. Project access also considers cabinet assignments, restricted-project assignments and owner/grant policy.
- `database.py` is a standard async SQLAlchemy session factory with no RLS/context setup. Existing RLS strings in `canonical_grants.py` are M06 DDL metadata for different tables, not installed M04 policy evidence.
- `MigrationEvidenceContext.transaction_id` has no enforced database transaction identity format. This adapter opens its own transaction and reads PostgreSQL `txid_current()`; no owned integration boundary supplies a binding from the context ID to that DB transaction ID.

## Adapter behavior and wiring

The SQLAlchemy adapter owns a PostgreSQL transaction, sets tenant/cabinet/project/user/role/writer-generation GUCs using transaction-local `set_config(..., true)`, reads them back with `row_security`, and clears them before exit. Before any M04 evidence read/write it checks relation existence, RLS enable/force, policy shape/digests against a supplied security-review manifest, selected uniqueness constraints, role bypass/superuser state, and audit table privileges. The default manifest is absent.

The persistent write/resolution path is deliberately not activated. Schema/review gates are unconditionally blocked by missing M04 promotion/privilege review, canonical-schema mapping lineage, and checkpoint schema. `load_checkpoint` raises a specific blocked gate instead of treating absent storage as an initial cursor. Mapping classification returns `BLOCKED` without advancing the checkpoint; active resolution returns `None`; neither can return an active scope or enable positive writes. No fallback or broad privilege is provided.

The adapter module is importable directly but no coordinator/resolver composition root imports or constructs it. Production wiring status: `NOT WIRED / NOT PROMOTABLE`.

## Remaining gates before implementing positive persistence

1. Register reviewed Alembic schema for the owner registry and confirm installed relation/constraint lineage without changing an applied revision.
2. Add a checkpoint table and contract that persists full tenant/stream/snapshot/source-schema/canonical-schema/digest/writer-generation identity with expected sequence/cursor CAS and fencing.
3. Persist `canonical_schema_version` on source mapping evidence or an independently reviewed immutable equivalent; define how schema conflicts are replayed when the first outcome is quarantine.
4. Supply reviewed PostgreSQL policies for every owner/evidence table, covering tenant predicates, exact typed parent and active membership, `USING`, `WITH CHECK`, no-context denial, `ENABLE/FORCE`, and a non-BYPASSRLS non-superuser runtime role. Record exact catalog policy digests and reviewed role identity.
5. Supply least-privilege grants for mapping/quarantine/checkpoint and append-only audit. Audit requires database-enforced update/delete denial and an immutable, idempotently addressable append event; RLS must scope quarantine/audit even when no canonical registry row exists.
6. Resolve actor membership lifecycle and cardinality from authoritative identity tables; do not infer activity from relationship existence.
7. Resolve cabinet access policy (active grant vs owner/all-scope/employee assignment) and project eligibility/restricted assignment semantics, with same-transaction cardinality and parent tuple checks. Define locking/isolation against concurrent membership or parent changes.
8. Bind `MigrationEvidenceContext.transaction_id` to the DB transaction actually used for GUCs, parents, membership, registry, mapping, audit and checkpoint, or revise the owned transaction contract before emitting transaction proof.
9. Bind the reviewed adapter in the coordinator and resolver composition root after independent code/schema/RLS review; retain plugin-instance deny and legacy owner.
10. Provide an accepted bounded source snapshot and partition-level M04-01/M04-02 gate evidence before admitting any row.

## Acceptance and rollback

`test_sqlalchemy_adapter.py` specifies context-local set/read/clear, absent checkpoint failure, absent RLS/audit denial, no DML/no active result, source identity and checkpoint-successor validation, and RLS-manifest/policy-shape rejection. These are authored acceptance scenarios, not execution evidence.

No target rows or external state changed, so rollback is a no-op. Preserve any future mappings, quarantine, audit and checkpoint evidence; on a failed partition stop processing and repair forward. Legacy remains the only read/write authority. No promotion, cutover or positive writer is authorized.

Verification freeze honored: tests, build, compile, formatting, migrations, DDL/live writes and git commands were not intentionally invoked. The file-write and patch helpers automatically reported their lint hook status on source/test writes; no explicit lint command was run. That automatic tool behavior is recorded as a process caveat, not as accepted verification.