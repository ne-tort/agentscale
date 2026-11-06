# MIG-M05-PERSISTENCE-GATE-ACCEPTANCE-01 promotion record v4

Record ID: `MIG-M05-PERSISTENCE-GATE-ACCEPTANCE-01:blocked:runtime-writer-cas-v4:2026-11-03`
Supersedes application contract record: `MIG-M05-02:blocked:instance-runtime-fencing-contract-v3:2026-11-03`
Status: `blocked`; static database acceptance requirements and a test-owned SQLite fixture are authored, intentionally unrun.
Partition: `unbounded / none admitted`.
Decision: `do not import, publish, resolve, advance generations, promote, or cut over`.
Canonical positive writes: `false`.
Legacy runtime: `read/write owner`.

## Scope and evidence boundary

The M05 application contracts in `runtime_service.py` define snapshot-pinned import/checkpoint ports, exact identity proof, subject/session proof, lifecycle operations, runtime-generation advancement and writer-generation fencing. They require the persistence adapter to re-prove trusted identity in one transaction, enforce database-side compare-and-set (CAS), append redacted audit atomically, and deny stale workers. Those are port contracts, not evidence that a database implementation exists.

`PluginInstanceRegistryRow` is registered in `infrastructure/persistence/models/__init__.py`. Its staged model has exact instance identity `(plugin_instance_id, definition_id, version_id, tenant_id, scope_kind, scope_id)`, runtime and writer generations, verbatim source fields and unique source identity `(source_kind, source_id, source_schema_version)`. Its definition/version/artifact and reciprocal scope-registry links are model metadata only. ORM registration does not prove an installed schema, append-only enforcement for bulk SQL, validated constraints, RLS, or a trusted transaction adapter. No ORM/API/runtime source was changed.

The new SQLAlchemy SQLite acceptance adapter lives only under `tests/migration/m05_instance_runtime/persistence/`. It creates disposable fixture tables, executes concrete SQLAlchemy `UPDATE ... WHERE` CAS and transaction rollback cases, and is not a production adapter. It has no PostgreSQL RLS, M04/M04E transaction proof, production registry lookup, Alembic schema, live data, or production wiring. A passing fixture would be local contract evidence only, not a promotion gate receipt.

## Exact Alembic inventory finding

No M05 Alembic revision ID has been assigned or found: there is no M05 migration file in the inspected `apps/api/alembic/versions/` inventory and no revision defining `m05_plugin_instances`, `m05_plugin_instance_registry_links`, or `m05_plugin_instance_promotion_evidence`. The latest inspected revision file is `2026110202_agent_control_plane_persistence.py` (`revision = "2026110202"`, `down_revision = "2026110201"`). The absence is a missing revision, not a reserved revision number; v4 does not invent one. No Alembic command was run.

The registered target model also is not backed by discovered migration-owned storage for M05 import mappings, resumable checkpoint/cursor state, quarantine and retry history, or runtime audit. `M05PluginInstancePromotionEvidenceRow` is likewise model metadata without a target revision. Consequently, none of these storage objects, constraints, policies, triggers/privileges, or rows may be treated as installed.

## Production database acceptance gates

All of the following require an actual PostgreSQL adapter/schema, reviewed SQL and database-backed acceptance against the installed target. The SQLite test fixture does not satisfy them.

1. **Schema/revision and exact keys:** author and review a new Alembic revision from the registered models and required M05 operational tables. Verify exact-instance uniqueness, the full source key without truncation/normalization, nonnegative generations, typed `plugin_instance` scope shape, and exact definition/version/artifact anchors after migration. Prove upgrade and schema inspection on an isolated PostgreSQL database before any partition is admitted.
2. **Composite FK validation and deletion behavior:** validate both reciprocal M05 directions for tenant, cabinet and project tuples: `m05_plugin_instances(scope_registry_id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]]) -> scope_registry(id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]])`, and the inverse `scope_registry(id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]]) -> m05_plugin_instances(scope_registry_id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]])`. The ORM stages these six links as deferred `RESTRICT`; database installation and validation evidence is absent. Verify exact tuple mismatch, missing parent, duplicate target, tenant/cabinet/project mismatch and deferred-constraint commit rejection. Verify deletion is restricted in both directions and cannot orphan a positive instance; explicitly review interaction with upstream scope cascades. Do not infer cascade behavior from declarations or an SQLite schema.
3. **RLS and roles:** enable and force tenant RLS on every M05 mapping, checkpoint, registry, quarantine/retry, audit and promotion-evidence relation. Verify transaction-local tenant context is set before access, missing context denies, cross-tenant reads and writes deny, policies cover both `USING` and `WITH CHECK`, and the runtime role is not `BYPASSRLS`/owner-equivalent. Bind reviewed policy/role evidence to the exact database/schema revision and transaction adapter.
4. **Immutable source identity:** enforce immutability below ORM level for canonical and source identity, source schema, source digest and accepted first mapping. A repeated exact `(tenant, definition, source_kind, source_id, source_schema_version)` with the same digest replays the original identity; changed digest or identity becomes conflict/quarantine without overwrite. Review privileges/triggers for UPDATE/DELETE through ORM, bulk SQL, raw SQL and maintenance roles. Preserve source IDs byte-for-byte; no trimming, case-folding, derivation or truncation.
5. **Durable import/checkpoint/audit storage:** provide migration-owned mapping, snapshot checkpoint, quarantine and append-only retry-attempt tables, plus a redacted append-only audit sink. Commit mapping/replay/conflict evidence, checkpoint CAS and audit in one transaction. Key the cursor by tenant/definition, snapshot ID, schema version and source digest. Resume only from the durable cursor; reject ahead/stale callers, changed snapshot/schema/digest, non-monotonic cursor, duplicate source identity and over-limit page. On mapping, checkpoint, proof or audit failure, rollback every positive effect. Preserve first mapping and quarantine/retry history.
6. **Trusted subject/session and identity proof:** derive tenant, subject and session from authenticated server context, not a request or source row. In the same database transaction prove an active subject and active session bound to that tenant/subject, then re-prove active M04 installation scope, exact active `plugin_instance` scope and registry relation, M04E definition/version/artifact equality, lifecycle and complete identity. The current `AgentSessionRow` has no subject/tenant owner fields; a trusted ownership adapter/evidence source is still required.
7. **Writer CAS:** perform one database-side `UPDATE` whose predicate matches the complete immutable instance identity and current `runtime_generation`, `writer_generation`, published lifecycle, authenticated tenant/session binding, and transaction-local M04/M04E/registry proof. Advance writer generation exactly one. An affected-row count of zero is a stale-worker denial; it must not mutate identity/runtime/lifecycle or emit a success audit. Concurrent transactions must prove only one winner. Audit denial/advanced outcome must be atomically consistent with affected rows.
8. **Runtime generation CAS:** restart/replacement must compare both expected generations and full identity, set `runtime_generation = expected + 1` and `writer_generation = expected + 1` exactly, and require active positive gates. Stale runtime or writer epoch, wrong instance, skipped/no-op/backward epoch, inactive lifecycle or zero rows denies. Verify concurrent workers and malformed returned-row rollback in PostgreSQL.
9. **Audit atomicity and redaction:** audit event/outcome/reason/correlation and numeric generations share the CAS/import transaction. Inject failure after target mutation and before audit persistence; database readback must show the complete transaction rolled back. Verify zero-row denial never persists `advanced`. Exclude raw payload, credentials/tokens, subject/session/tenant/scope labels, and full identity from audit.
10. **Promotion readiness:** record installed revision, validated constraints, RLS/role review, immutable storage controls, source snapshot identity, durable checkpoint, row counts/digests, runtime/writer CAS concurrency and stale-denial evidence, audit rollback evidence, and explicit M04/M04E gate handoffs. Any missing item keeps the partition blocked. M06 handoff is evidence-only; legacy remains owner and `canonical_positive_writes=false`.

## Test-owned SQLite acceptance fixture

`tests/migration/m05_instance_runtime/persistence/sqlite_acceptance.py` and `tests/migration/m05_instance_runtime/persistence/test_sqlalchemy_acceptance.py` define an executable, unrun contract adapter and cases. The fixture creates only `test_m05_*` tables and SQLite-only test triggers in ephemeral in-memory SQLite. Its cases cover:

- exact identity and both generations in writer/runtime CAS; exact +1 advancement; stale zero-row denial leaves target unchanged;
- tenant, authenticated subject, active session and session binding mismatch denial;
- exact source replay preserving the first mapping, changed source digest or identity conflict, DB-trigger rejection of source-identity mutation and mapping update/delete, and verbatim 80-character source ID;
- checkpoint resume bound to snapshot ID/schema/digest, strict source cursor ordering, stale checkpoint denial and snapshot mismatch denial;
- mapping/checkpoint/audit transaction rollback and runtime CAS/audit rollback when the fake audit sink fails.

This suite deliberately does not model RLS, real M04/M04E proof, the reciprocal composite FK/cascade graph, production deletion restrictions, PostgreSQL isolation/races, Alembic upgrade, target ORM storage, production subject-session ownership, or promotion readiness. Its use of SQLAlchemy/SQLite is solely a test-owned fixture; it must never be wired into application runtime.

## M04 / M04E handoffs

- **M04-01 / M04-02:** inspected promotion records remain `blocked`, with no admitted bounded partition, source snapshot, accepted mapping evidence, validated typed/composite key/RLS evidence, or writer fencing. M04 requires active typed-parent and membership/project compatibility proof plus durable transaction-local tenant/RLS adapter. Its plugin-instance mapping branch remains deferred pending this M05 gate. No M04 handoff receipt exists for this partition.
- **M04E-01 / M04E-02:** inspected release readiness remains false/blocked; no admitted source rows/partition, durable definition/version/artifact evidence bundle or persisted `MIG-M04E-02` handoff marker was found. M05 must receive partition-bound M04E proof of exact tenant/app definition, same-definition version, immutable artifact and completed version checkpoint, correlated with M04 gate digests. No marker/proof is assumed from the application fixture.
- **M05 to M06:** unavailable until all upstream gates and database acceptance evidence pass, exact source accounting and final-delta/parity checkpoints reconcile, quarantine/conflict/unresolved counts are zero, and legacy writer fence is drained at the recorded generation. Even a later ready M05 handoff does not grant positive canonical writes or authorize cutover.

## Current gate counts and blockers

- M05 Alembic revision: `ABSENT / NOT ASSIGNED`; latest inspected revision is `2026110202`.
- Installed M05 tables, mapping/checkpoint/quarantine/retry/audit storage: `NOT FOUND / NOT VERIFIED`.
- Composite FK installation/validation and cascade/deletion review: `NOT AVAILABLE`.
- M05 RLS policies, forced-RLS/role review and transaction-local tenant context: `ABSENT`.
- Durable database-side mapping/checkpoint/runtime/writer CAS adapter and audit sink: `ABSENT`.
- Database-level source identity append-only control: `ABSENT`.
- Trusted subject/session ownership proof: `NOT AVAILABLE`.
- M04/M04E bounded partition evidence and persisted handoff: `NOT AVAILABLE`.
- Source inventory, snapshot/digests, accepted/quarantined counts, target digest and generation receipts: `NOT ACQUIRED`.
- SQLite acceptance cases: `AUTHORED / NOT RUN`; they are not production evidence.
- Promotion and canonical writes: `BLOCKED / FALSE`; legacy remains read/write owner.

## Rollback and freeze

No source rows, target database, schema, environment, runtime or canonical state was changed. The SQLite fixture only defines an ephemeral schema when its tests are explicitly executed; the suite was not executed. No DDL, Alembic, live DB, import, publish, resolve, generation mutation, cutover, test, build, compile, explicit lint/format command or Git command was run. The file-write helper automatically reported lint status for Python edits; this was not separately invoked. No commit or push was made.

Rollback for this contract-only work is a no-op. On any future partition failure, stop that partition, preserve first mappings/checkpoints/quarantine/retry/audit evidence and legacy ownership, deny stale writers, and repair forward with a newer generation. Never delete evidence, restore a stale writer, or enable a positive path as fallback.

## Next gate

A production owner must supply the missing M05 migration revision and migration-owned storage, then implement a trusted PostgreSQL transaction adapter with forced RLS, validated reciprocal composite FKs and deletion behavior, DB-enforced source immutability, durable mapping/checkpoint/quarantine/retry/audit, active subject-session ownership proof, and exact runtime/writer CAS. After that, obtain independently reviewed M04/M04E partition-bound gates and persisted handoff marker, run approved PostgreSQL acceptance/concurrency/rollback/RLS tests on an isolated database, reconcile a bounded source snapshot and final-delta/parity/legacy-fence evidence, and append a new promotion record. Until those dependencies exist and are reviewed, M05 remains blocked.
