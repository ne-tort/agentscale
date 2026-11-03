# MIG-RES-RUNTIME-PERSISTENCE-01 promotion record

Status: **BLOCKED / not promotable**. This records schema-backed application persistence only; it is not authorization to write runtime state in a shared environment or replace the legacy owner.

## Scope and inspected callers

- New adapter: `SqlAlchemyRuntimeRevisionRepository` in `application/migration/runtime_materialization/sqlalchemy_repository.py`.
- Existing source contract: `RuntimeRevisionRepository`, `ProjectRuntimeRevision`, `CapabilityBundle`, and `MaterializationEntry` in `application/runtime_revision/interfaces.py`.
- Existing registered ORM: `CapabilityBundleRow`, `ProjectRuntimeRevisionRow`, `MaterializationEntryRow` in `infrastructure/persistence/models/runtime_revisions.py`, exported by `models/__init__.py`.
- Exact project scope uses `RuntimeApiProjectScope` and SQL joins on `ProjectRow(id, company_id, cabinet_id)` and `ProjectPodRow(id, project_id)`. Session ownership follows `async_sessionmaker[AsyncSession]` and repository-owned `session.begin()` conventions.
- Caller scan: `project_runtime_reconcile/service.py` consumes only `ProjectRuntimeReadPort`, whose latest-revision method is project-only; no production or migration caller constructs `RuntimeRevisionRepository`, `RuntimeMaterializationService`, or this new adapter. `runtime_api/coordinator.py` declares abstract scope/state-machine/executor ports only. No integration was made because there is no real call site inside the owned zone.

## Durable capabilities implemented

- Scoped SQL reads for revision by ID/latest and ordered materialization-entry listing.
- Immutable desired snapshot insert with bundle digest/manifest validation, project revision uniqueness handling, and exact snapshot replay/conflict result.
- A plan-claim transaction using project/Pod scope proof, a database-side expected-state `UPDATE` predicate, schema-backed per-entry idempotency keys, operation digest and source provenance. Existing entry generations are checked with a conditional zero-row CAS; the schema has no resource-wide generation row, so successful initial plan generation is stored on inserted entries, not advanced from a resource-level CAS.
- Exact same persisted materialization plan replay; different or partial key collisions conflict. The current schema cannot persist a second attempt for the same revision, so changed plans on a revision with rows are rejected as `revision_materialization_snapshot_immutable`; retry needs a newly planned revision until attempt history exists.
- `finish_failed` can persist `failed` state and partial entry outcomes only, with safe diagnostics and same-session audit participant. No ready method exists.
- Apply claim requires explicit `DependencyEvidence` and aware time, matching pinned bundle/runtime image digest, non-revoked non-expired bridge lease reference+generation, projected secret references, contract-ready marker, and run/approval/event links. This checks supplied evidence shape only; it is not independent issuer, revocation, Pod, or provenance verification.
- Audit rollback is represented by an injected `TransactionalRuntimeAuditPort` using the same SQLAlchemy session. The behavioral fixture inserts a local audit row and injects a failure after insertion to assert transaction rollback. No production audit model or writer was added.

## Unsupported schema and caller gates

- `ProjectRuntimeRevisionRow` has no durable state-machine attempt/receipt table or revision-level runtime materialization generation / writer fence. `MaterializationEntryRow.generation` is per path and cannot fence an empty plan or provide resource-wide CAS. Unique revision/idempotency rows do not preserve multiple attempts. Adapter rejects empty plan generations.
- `MaterializationEntryRow` has a unique `(revision_id, idempotency_key)` constraint but no plan/attempt result payload, fence token, retry count, or immutable attempt history. It supports exact plan replay only, not a complete durable `RuntimeRevisionStorePort` implementation (`load_state`, `find_attempt`, `claim`, `finish`, applied-history lookup).
- No runtime audit ORM table or concrete trusted writer exists. Atomic audit is only structurally supported when a caller supplies a same-session writer; production audit durability, append-only privileges, retention/taxonomy and correlation ownership remain unimplemented.
- The adapter exposes scope-required repository methods and intentionally is not a drop-in implementation of the unscoped `RuntimeRevisionRepository` protocol in `application/runtime_revision/interfaces.py`; that protocol cannot prove company/cabinet/Pod scope. No real caller exists to consume a scoped replacement contract.
- No real call site currently wires the adapter or atomic executor; no route/Pod operation integration is authorized or added.
- No independently trusted bridge lease/revocation validator or image digest provenance verifier is available in this adapter. Evidence checks do not prove those external facts.
- `agent_runs`, durable approval, ordered event trace contracts are incomplete. Required IDs are checked for presence but not resolved to authoritative records here.
- Tenant convention found in platform code is exact company/project/actor scope checks. No transaction-local `app.tenant_id` setter or runtime-table RLS policy is installed for these ORM tables. Adapter validates company/cabinet/project/Pod but cannot claim RLS isolation proof.
- Legacy runtime/project lifecycle remains authoritative. Do not treat `applying`, persisted materialization entries, or evidence objects as applied/ready proof.

## Acceptance artifacts and readiness

Authored, intentionally unexecuted tests in `test_sqlalchemy_repository.py` cover revision/entry round-trip, exact idempotent plan replay and changed digest conflict, stale generation zero-row conflict, audit failure transaction rollback, partial apply persisted as failed, and missing evidence fail-closed. Existing state-machine acceptance fixtures remain as separate pure/fake-port coverage.

Readiness: **blocked** for runtime ownership, positive apply, adapter wiring, and promotion. Static source review only; no production persistence, DDL, database calls, or positive Pod write occurred.

## Rollback and gate result

Before any future runtime ownership change, disable adapter construction/callers and keep legacy project/runtime readers and writers authoritative. Preserve materialization rows and diagnostics as evidence; do not delete or rewrite them to simulate rollback. After a generation fence exists, rollback must be a new explicit forward revision, not restoration of stale rows/workers. Current adapter has no operational rollback executor and cannot assert a previous-ready revision from its own persistence.

Required next gates: runtime receipt/attempt and resource fence schema; append-only audit schema plus trusted same-transaction writer; transaction-local tenant/RLS context; authoritative lease/image/run/approval/event evidence adapters; a real existing application call site within approved ownership; independent source review and all deferred tests after coordinator lifts migration freeze.

Tests, builds, lint, compile, format, git checks, database DDL/data writes, commits and pushes were intentionally not run. The `send_message` parent-report tool was not exposed in this subagent session; this promotion record is the durable report artifact, and the final response carries the same summary.
