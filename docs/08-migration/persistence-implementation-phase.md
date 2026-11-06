# Persistence implementation phase: bounded plan

Status: migration-stage bounded implementation plan, documentation only. Baseline: 2026-11-01. This document is the next persistence phase after the application adapters. It does not change ORM, application code, core documentation, database state, or shared environments.

The phase joins M04 owner/scope registry, M05 plugin-instance identity and fencing, M06 canonical grants, audit V2, runtime materialization/resources, and automation outbox persistence. The order below is one linear Alembic chain. The M04/M05/M06 labels are bounded domains, not Alembic revision identifiers.

## 1. Status vocabulary and non-goals

Every item is explicitly classified:

- **As-built** means observed in the current worktree and usable only as the legacy/read or compatibility baseline.
- **Target** means the persistence contract this phase will implement through ORM, generated Alembic revisions, repositories, and acceptance artifacts.
- **Not yet implemented** means a required target behavior that must remain disabled until its gate is recorded. A target description is not permission for positive writes, backfill, cutover, or shared-environment DDL.

The verification freeze remains active. Tests, builds, lint, formatting, compilation, Alembic execution, migration execution, environment changes, and git checks are intentionally not run for this planning task.

This phase does not authorize:

- manual DDL, `alembic upgrade`, backfill, cleanup, or production/shared-environment changes;
- canonical positive grant writes before the final partition promotion record;
- dual authorization owners or dual delivery workers for one partition;
- guessing tenant, scope, version, instance, subject, or project from payloads, names, JSONB, or UI state;
- destructive downgrade after tenant data import.

## 2. Current baseline: inspected as-built

### 2.1 ORM and model registration

- `apps/api/src/prodavan/infrastructure/persistence/models/dynamic_apps.py` is the current legacy dynamic-app graph: `dynamic_app_definitions`, `dynamic_app_versions`, `dynamic_app_grants`, and `dynamic_app_instances`. Its stable IDs and owner/scope columns are predominantly `String(80)`; the canonical owner/tenant anchors must be aligned to `String(40)` only where they participate in the target owner/FK contract. Definitions and instances allow only `tenant|cabinet|project`; versions have composite same-definition constraints, but version and owner relations still contain legacy cascades and cycles.
- `apps/api/src/prodavan/infrastructure/persistence/models/dynamic_app_grants.py` defines `dynamic_app_capability_grants`, but it currently has `String(80)` IDs/tenant/scope, a simple `app_version_id -> dynamic_app_versions.id` FK with `CASCADE`, no `capability_key`, no exact instance/subject/session/generation identity, no registry FK, and only a three-shape scope check.
- `apps/api/src/prodavan/infrastructure/persistence/models/runtime_revisions.py` already defines `capability_bundles`, `project_runtime_revisions`, and `materialization_entries`, including revision/path and idempotency uniqueness and fail/apply lifecycle checks. The model lacks the complete target provenance, project desired/applied FK closure, RLS policy, and explicit materialization status mapping required by this phase.
- `apps/api/src/prodavan/infrastructure/persistence/models/resources.py` already defines `resources`, `resource_versions`, and `resource_bindings`. Resource scope and target scope are polymorphic strings; versions/bindings use derived-child cascades; no scope-registry FK or full target binding policy is enforced.
- `apps/api/src/prodavan/infrastructure/persistence/models/agent_control_plane.py` already defines durable `agent_runs` and `approval_requests`, with project/run/session/employee FKs and idempotency uniqueness. They are not yet the complete automation control-plane/outbox ownership boundary.
- `apps/api/src/prodavan/infrastructure/persistence/models/external_api.py` already defines `webhook_subscriptions` and `webhook_deliveries`; deliveries have pending status, lease, attempts, retry time, and worker identity. This is an as-built outgoing webhook outbox, not proof that all automation ingress/task intents or future ownership fencing are persisted.
- `apps/api/src/prodavan/infrastructure/persistence/models/equipment_v2.py` is present and imported by `models/__init__.py`, but its `tenant_id` fields are `String(64)` without canonical company FK. Equipment is outside the positive M04–M06 grant gate; its persistence alignment is a downstream resource/automation dependency where used.
- `apps/api/src/prodavan/infrastructure/persistence/models/__init__.py` imports the current runtime, resource, control-plane, equipment, and dynamic-app rows into `Base.metadata`. Future model modules must be deliberately imported here before autogenerate; import alone does not mean the schema exists.

### 2.2 Existing Alembic baseline

The inspected chain currently ends at `2026103001_b2b_tenders.py`, whose `down_revision` is `2026102301`. Relevant existing revisions include:

- `2026090503_module_instances.py` and `2026090504_backfill_module_instances.py`: an example of staged table creation followed by data backfill; this does not establish the new plugin-instance identity.
- `2026082315_trigger_outbox_lease.py`: lease columns on legacy `project_triggers`; this is not the new automation outbox ownership boundary.
- `2026101501_external_chat_api.py`: creates webhook subscriptions/deliveries with lease/retry semantics.
- `2026090210_session_lifecycle_workspace.py`: current session/workspace compatibility fields, not a runtime revision materialization ledger.
- No inspected revision creates `scope_registry`, canonical owner mapping/quarantine/audit V2, exact plugin-instance registry closure, canonical grant RLS/FORCE policies, runtime resource binding target schema, or the new automation intent/outbox ownership state machine.

The implementation PR must re-confirm the repository head before generating revisions. The names and `down_revision = "2026103001"` below are the bounded planned slots based on this inspected snapshot, not permission to run Alembic or to edit an applied revision.

## 3. Target file and revision map

### 3.1 Future ORM file set

The implementation phase owns these future files, with existing files changed only in the implementation PR that owns the corresponding revision:

| Area | Target ORM files | Repository/application wiring |
|---|---|---|
| M04 owner registry | `apps/api/src/prodavan/infrastructure/persistence/models/owner_registry.py`; definition/version changes in `dynamic_apps.py` | `ScopeRegistryRepository`, `OwnerMappingRepository`, `DefinitionVersionRepository` |
| M05 instance | `apps/api/src/prodavan/infrastructure/persistence/models/plugin_instance_registry.py`; staged changes to `dynamic_apps.py` | `PluginInstanceRepository`, `RuntimeFenceRepository` |
| M06 grants | `apps/api/src/prodavan/infrastructure/persistence/models/canonical_grants.py`; compatibility changes to `dynamic_app_grants.py` only in its implementation PR | `CanonicalGrantRepository`, `GrantMappingRepository`, `GrantQuarantineRepository`, `LegacyGrantProjectionRepository` |
| Audit V2 | `apps/api/src/prodavan/infrastructure/persistence/models/audit_v2.py` | `CapabilityAuditRepository`, `TrustedAuditTransaction`, promotion evidence repository |
| Runtime/resources | existing `runtime_revisions.py` and `resources.py`, plus `materialization_provenance.py` only if a separate immutable child is required | `RuntimeRevisionRepository`, `MaterializationRepository`, `ResourceRepository`, `ResourceBindingRepository` |
| Automation | `apps/api/src/prodavan/infrastructure/persistence/models/automation_outbox.py`; compatibility adapters for existing `external_api.py` and `projects.py` | `AutomationIntentRepository`, `OutboxRepository`, `DeliveryLeaseRepository`, `AutomationPromotionRepository` |

All new model classes must be imported by `models/__init__.py` before model-derived Alembic generation. A model being present in source but absent from `Base.metadata` is a release blocker.

### 3.2 Exact planned Alembic chain

Create new revisions only, in this order, from the confirmed current head. This is the combined persistence implementation chain, not a replacement for the logical order in `persistence-target-map.md`: its runtime/resource work remains parent-before-child (`capability_bundles` -> runtime revisions -> materialization; resource roots/versions -> bindings), and equipment/control-plane work remains separately gated. M04/M05/M06 foundation and owner evidence must not be bypassed by creating a dependent runtime/resource or automation FK early.

| Planned revision | Scope | Positive writes |
|---|---|---|
| `2026110101_persistence_owner_scope_foundation.py` | Align owner columns and create empty `scope_registry`, mapping/quarantine foundation, helper constraints/indexes | Disabled |
| `2026110102_persistence_definition_version_alignment.py` | Definition owner `(id, tenant_id)`, version same-definition keys, artifact/lifecycle checks, staged current-version cycle | Disabled |
| `2026110103_persistence_plugin_instance_foundation.py` | Exact plugin-instance fields, composite unique key, runtime/writer generations, empty instance evidence | Disabled |
| `2026110104_persistence_plugin_instance_fk_close.py` | Named reciprocal registry/instance composite FKs, `use_alter`/late constraints, validation and nullability tightening | Disabled |
| `2026110105_persistence_audit_v2.py` | Full immutable audit V2 envelope, append-only boundary, RLS/FORCE, trusted audit role | Disabled |
| `2026110106_persistence_canonical_grants.py` | Canonical grants, mapping/quarantine completion, composite FKs, capability identity, indexes, checks | Disabled |
| `2026110107_persistence_grants_rls.py` | Grant RLS/FORCE policies, privileges, trusted writer epoch and policy validation inventory | Disabled |
| `2026110108_persistence_runtime_resources_materialization.py` | Runtime revision/materialization provenance and resource roots/versions/bindings target closure | Disabled |
| `2026110109_persistence_automation_outbox.py` | Automation intent/event/outbox/delivery ownership, leases, retry and compatibility projection | Disabled |
| `2026110110_persistence_promotion_records.py` | Immutable per-partition promotion record, final-delta/fencing/parity evidence, ownership state | Disabled |

If the confirmed head differs, preserve this content order and generate new revision IDs from the actual head. Never rewrite or renumber an applied revision. Backfills, constraint validation requiring long data work, and projection rebuilds are separate resumable jobs/commands, not hidden in autogenerate.

## 4. M04: owner registry and type alignment

### Target schema

`scope_registry` has exactly four shapes: `tenant`, `cabinet`, `project`, `plugin_instance`. It stores `scope_kind`, `scope_id`, canonical `tenant_id`, optional cabinet/project/plugin-instance anchors, `owner_state`, source identity, schema version, source payload digest, checkpoint, and timestamps. Enforce unique `(scope_kind, scope_id)` and named shape checks. A generic `(scope_kind, scope_id)` polymorphic FK is prohibited.

Canonical tenant identity is `companies.id`; application vocabulary may call it `tenant_id`. All canonical tenant, typed-owner anchor, and FK-compatible owner columns introduced or aligned in this phase use the exact `String(40)` shape of `companies.id` (and the corresponding `String(40)` cabinet/project anchors). Existing `String(80)`/`String(64)` values are not truncated or implicitly cast: inventory overlength/malformed values first, quarantine them, then perform a data-preserving conversion only for columns that participate in the canonical owner/FK contract. Stable legacy/application IDs that are not canonical owner anchors retain their reviewed physical shape until a separate compatibility decision; they must not be shortened merely to satisfy this phase. Equipment tenant columns remain not-yet-implemented until their source mapping is classified.

Parent compatibility must prove the company/cabinet/project tuple and active membership. A copied tenant or nullable cabinet owner column is not proof. Owner/security evidence uses `RESTRICT` or archive transitions, never cascade deletion.

### Backfill and gate

The M04 job is resumable and idempotent by `(source_kind, source_id, schema_version, source_payload_digest)`, one bounded source row per transaction. Valid retained owners map to active/archived rows. Missing/deleted, malformed/overlength, ambiguous, duplicate/conflicting, or cross-tenant/cross-cabinet rows go to redacted quarantine and append-only audit. Quarantine cannot satisfy ordinary resolution.

The M04 gate requires one active typed row, valid parent compatibility, deterministic definition/version prerequisites, validated RLS/FORCE policies, and zero unresolved required parent mappings for the partition. Plugin-instance resolution and grant positive writes remain disabled.

## 5. M05: plugin-instance identity and fencing

### Target schema

Materialize an exact `plugin_instance_id`; do not substitute `app_id`, `app_version_id`, `scope_id`, or `session_id`. The instance key must prove:

```text
(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)
```

Store immutable artifact digest, exact tenant/scope anchors, subject/session binding where required, lifecycle (`draft|published|disabled|revoked|quarantined`), runtime reference without secrets, heartbeat/start/stop evidence, monotonic `runtime_generation`, and CAS `writer_generation`. Same-definition version and tenant ownership must be composite proofs.

The registry/instance relation is cyclic. Create anchors and the instance unique key first, then add named reciprocal constraints in `2026110104_persistence_plugin_instance_fk_close.py`. SQLAlchemy models use stable named `ForeignKeyConstraint`s and `use_alter=True` where table creation order requires it; Alembic uses explicit follow-up `op.create_foreign_key`. Nullable/deferred linkage is temporary migration staging only and must be tightened/validated before the M05 gate. A `NOT VALID` constraint is incomplete until explicit validation evidence exists.

### Runtime writer boundary

`PluginInstanceRepository` and `RuntimeFenceRepository` must use compare-and-set, not `SELECT` then mutate. Stale runtime generation denies; stale writer generation changes zero rows and appends a fencing event. The trusted transition checks scope, definition/version, artifact, subject/session, lifecycle, lease where applicable, and current generations in one transaction. No fallback instance or app-wide grant is allowed.

The M05 gate requires exact key and both FK directions validated, artifact equality, active typed scope, exact subject/session evidence where policy requires it, current generations, and successful audit. Historical rows lacking proof are draft/disabled/quarantined, never inferred published.

## 6. M06: canonical grants, audit V2, and trusted transaction

### Canonical grant target

`dynamic_app_capability_grants` becomes the sole authorization owner only after bounded partition cutover. `dynamic_app_grants` remains a read-only compatibility projection. The target row includes explicit `app_definition_id`, `app_version_id`, `app_id`, `tenant_id`, typed scope anchors, `capability_key`, subject binding, optional exact plugin instance/session, canonical operations/resources/domains/constraints, artifact/binding/payload/mapping digests, lifecycle, grant generation, writer generation, issue/expiry/revoke identity and provenance.

Required relations are:

- `(app_version_id, app_definition_id) -> dynamic_app_versions(id, app_definition_id)`;
- `(app_definition_id, tenant_id) -> dynamic_app_definitions(id, tenant_id)`;
- exact instance composite FK to the M05 key, `ON DELETE RESTRICT`;
- typed scope/anchor relations through the registry, with plugin-instance shape requiring `scope_id = plugin_instance_id` and installation anchors.

Use `RESTRICT` for authorization/provenance parents. Active uniqueness covers the complete authorization identity and canonical digests; it must not use a moving-clock predicate. Expiry is evaluated at read time. Reissue closes/supersedes history transactionally and increments generation.

### Audit V2

`audit_v2.py` persists a full redacted immutable envelope, not a narrow MCP result alone. It includes event identity/type/time, decision/reason/result/error, tenant/cabinet/project/user/subject/session, definition/version/artifact, scope/instance/runtime/writer/lease, grant/source/mapping/schema identity, capability/payload/argument/snapshot digests, operation/resource/domain, request/trace/span/correlation, MCP profile/server/tool/version, redaction metadata, and duration. Raw arguments, credentials, tokens, payloads, and secrets never persist.

The trusted input is `AuditOutcomeEvidence` or equivalent full evidence plus its derived `RedactedAuditOutcome`. The projection must match event, correlation, result/error, timestamp, and digest fields. Direct narrow append is non-authoritative compatibility telemetry and cannot satisfy M06, promotion, runtime, or outbox gates.

Audit is insert-only: API/application roles have no `UPDATE`/`DELETE`; a deny-by-default trigger or equivalent trusted writer rejects mutation. Corrections are new linked events. Optional target references use `SET NULL` only when genuinely historical/optional; source IDs/digests remain. Audit never cascades away with grants or runtime rows.

### Trusted transaction boundary

Every positive grant issue/revoke/reissue, published instance/runtime transition, materialization ownership transition, automation intent/outbox ownership transition, or promotion finalization must run through one trusted transaction:

1. Resolve authenticated principal and canonical `companies.id`; set transaction-local `app.tenant_id` and optional cabinet/project/user/role, correlation, and writer epoch from server context.
2. CAS/lock the relevant partition or instance writer generation and validate runtime generation/lease.
3. Resolve active typed scope and parent `EXISTS` compatibility, membership, definition/version, lifecycle, and immutable artifact.
4. Resolve exact subject/session/instance when required; reject missing, stale, quarantined, or ambiguous identity.
5. Canonicalize payload and approved mapping version; compute binding, payload, operations/resources/domains/constraints, source, and redacted argument digests.
6. Compare source identity/digest for idempotency. Same identity plus changed digest is conflict/quarantine, never overwrite.
7. Write the eligible target row, mapping/quarantine state, lifecycle/outbox/materialization evidence as applicable, and full audit atomically.
8. Commit only after RLS `USING`/`WITH CHECK`, named FK/check constraints, trusted role privileges, and append-only audit append succeed.

Audit failure rolls back the related positive mutation and returns deny/system failure. RLS context missing, empty, malformed, or contradictory is deny-by-default.

## 7. Resources and materialization persistence

### Target tables and files

The target builds on `runtime_revisions.py` and `resources.py`, with `2026110108` adding only the missing persistence closure:

- `capability_bundles`: immutable canonical manifest snapshot and digest;
- `project_runtime_revisions`: immutable desired/applied revision, project FK, lifecycle, runtime image digest, bridge lease/reference, generation, rollback target, and provenance;
- `materialization_entries`: revision-owned path, operation/idempotency digest, generation, status, content/source provenance, and immutable diagnostics;
- `resources`: stable root and explicit scope ownership;
- `resource_versions`: append-only immutable payload/checksum/status;
- `resource_bindings`: explicit target scope/session/project selection and pinned version identity;
- optional `materialization_provenance` child only if the existing JSON provenance cannot carry an immutable, queryable manifest. It must be created after its parent and use `RESTRICT` for audit/provenance links.

Use `CASCADE` only for intentionally derived children such as materialization entries and resource versions under a resource root. Published payloads and snapshots are immutable; rollback changes a pointer/binding to a prior version and never rewrites or deletes evidence. Add exact version FK only after the resource/version cycle is staged; do not use `version_number` alone as referential proof. Polymorphic resource scope requires the M04 registry or a documented policy-only boundary; a misleading FK to one owner table is prohibited.

Materialization states must preserve domain meaning: `planned`, `staged`, `applying`, `applied`, `failed`, `skipped` are either represented explicitly or mapped by a reviewed adapter with no silent coercion. Partial apply is `failed`, never `ready`. A failed apply retains provenance and diagnostics; rollback selects a prior `ready` revision through a forward command. Literal Pod secrets are forbidden; persist only short-lived bridge lease/projected-secret references and image digests.

RLS resolves runtime/materialization ownership through `project_id -> projects` (or a denormalized key only with parent `EXISTS` proof). Resource RLS must resolve through active scope registry. Target readers/writers are not enabled until resource binding ambiguity and secret-material checks pass.

## 8. Automation and outbox persistence

### As-built boundary

`webhook_deliveries` is an existing transactional outgoing-delivery table with retry and lease columns. `project_triggers` has legacy outbox-lite lease fields and is a separate trigger queue, not an alias for webhook delivery. Neither legacy lease shape has the target fencing generation/CAS boundary. These remain compatibility inputs/outputs until an explicit ownership checkpoint; their existence is not proof of atomic automation intent, ordered task/run linkage, or one current worker.

### Target schema and repository wiring

`automation_outbox.py` and `2026110109_persistence_automation_outbox.py` must persist separately:

- verified automation subscription/endpoint intent and source identity;
- inbound event/request idempotency identity and request hash;
- durable task intent linked to `agent_runs`/ordered event/approval identity when applicable;
- outbound event/enqueue intent created in the same transaction as the business mutation;
- delivery state, attempts, lease epoch, leased worker, retry/backoff, terminal result, and response/error digest;
- compatibility projection IDs for `project_triggers`/`webhook_deliveries`, never a second owner.

`AutomationIntentRepository` verifies tenant/scope server-side and claims `(source_kind, source_id, schema_version, request_hash)` atomically. `OutboxRepository` writes business state plus event/enqueue intent in one trusted transaction. `DeliveryLeaseRepository` claims with CAS/lease expiry and fencing epoch; an expired lease can be reclaimed, but a stale worker cannot mark a delivery terminal. Exact duplicate identity is idempotent; same identity with another hash is conflict/quarantine. No secret values are stored, only secret references.

Before target ownership, legacy workers remain authoritative and target rows are shadow evidence. After target ownership, legacy writers are fenced, target claims are the only eligible worker path, and the compatibility projection is read-only. Never run both workers for one delivery partition.

## 9. Repository wiring and application ownership

Wire repositories through the existing infrastructure persistence dependency/session composition, not direct ORM use from API handlers. The wiring must expose one transaction-scoped unit of work carrying authenticated context and writer epoch. Repository methods must not expose an unconstrained positive insert.

Required composition:

```text
authenticated request
  -> tenant/scope resolver
  -> definition/version resolver
  -> plugin-instance/runtime fence resolver
  -> trusted unit of work (RLS GUCs + CAS)
  -> canonical grant / materialization / resource / automation repository
  -> full audit append
  -> commit
```

The gateway/application adapters remain compatibility boundaries during shadow mode. Legacy rows are read-only comparison/projection surfaces after their respective cutover; they are never authorization or delivery authority once a partition is promoted. Every repository operation returns an explicit allow, deny, quarantine, conflict, stale-generation, or system-failure outcome.

## 10. Shadow-read, parity, and promotion record

Shadow reads run by bounded partition `(tenant_id, app_definition_id)`, narrowed by scope or exact instance when necessary. Legacy remains decision/worker owner while comparing:

- counts and source identity;
- tenant, scope, parent compatibility, definition/version, artifact, subject/session, instance/generation;
- capability key, operations/resources/domains/constraints and expiry/revoke result;
- materialization revision/path/status/generation/content/provenance;
- resource root/version/binding target and secret-reference policy;
- automation request/event hash, delivery identity, lease/fencing and terminal status;
- full audit event/source/correlation and redaction digests.

Known deny, expiry, revoke, unsupported mapping, and approved quarantine are explicit reason classes, not positive parity. Promotion requires zero unexplained mismatches, zero unknown retained mappings, zero unresolved positive bindings, validated FKs/RLS, drained final delta, and exactly one current writer generation.

`2026110110_persistence_promotion_records.py` creates an immutable append-only record keyed by the smallest bounded partition. It stores source snapshot/schema, source/target/binding digests, checkpoint/final-delta checkpoint, gate evidence, actor/time, decision owner, current reader/writer owner, fencing epoch, runtime/materialization/outbox generations, compatibility projection marker, correlation ID, rollback window, and decision (`blocked`, `quarantined`, `ready`, or `promoted`). A live status flag cannot replace this record. Promotion record creation itself uses the trusted transaction and audit boundary; a correction is a new record linked to the prior one.

Before read cutover, stop target writes and leave legacy authoritative while retaining evidence. After read cutover, there is no dual-owner rollback: fence the current target generation, fail closed, keep canonical reads authoritative, and repair forward through CAS/idempotent commands. Cleanup is a later retention decision.

## 11. Positive-write gates

No positive canonical grant, published plugin instance, runtime/materialization apply, resource binding, automation intent, outbox delivery ownership, or promotion may proceed unless all applicable gates pass atomically:

1. Authenticated principal resolves exactly one retained `companies.id`; transaction-local tenant context is server-set.
2. Request/plugin assertions match server-resolved tenant, cabinet, project, subject, session, definition, version, and instance.
3. Exactly one active typed scope row and parent membership/compatibility proof exist.
4. Definition/version same-definition keys, lifecycle, immutable artifact, and aligned `String(40)` ownership proof pass.
5. Exact plugin-instance identity, subject/session, runtime generation, lease, and current writer-generation CAS pass when required.
6. Capability identity and approved mapping are bounded; no requested-capability, JSONB, absent-method, wildcard, or tool-name broadening occurs.
7. Resource/materialization revision, binding target, path/idempotency digest, generation, provenance, and secret-reference rules pass.
8. Automation source/request/event identity and hash are idempotent; business mutation and outbox intent share one transaction.
9. Mapping/source/binding payload digests are conflict-free and quarantine is not used as authorization evidence.
10. Named composite FKs, checks, `NOT VALID` validations, RLS `USING`/`WITH CHECK`, `ENABLE`/`FORCE RLS`, and trusted-role privileges pass.
11. Full append-only audit V2 evidence is durably appended; audit failure rolls back the positive mutation.
12. Shadow parity has zero unexplained mismatches; final delta is drained; legacy writer/worker is fenced; one current owner generation is recorded.
13. Immutable promotion record exists for the exact partition and explicitly enables the relevant positive writer/read owner.

Failure is deny, quarantine, conflict, stale-generation, or explicit system failure. It is never an allow through legacy JSONB, copied IDs, guessed ownership, alternate instance, or a separate audit append.

## 12. Implementation acceptance artifacts and unresolved blockers

The implementation PR must provide, without executing them during this freeze, focused acceptance artifacts for:

- canonical owner/tenant-anchor `String(80|64) -> String(40)` classification, proof that unrelated stable legacy IDs are not shortened, and no truncation;
- all four registry shapes, parent compatibility, composite version/instance FKs, `use_alter`, deferred/late validation, and explicit `VALIDATE CONSTRAINT` evidence for every `NOT VALID` FK/check before a positive gate;
- RLS/FORCE no-context, wrong-tenant, wrong-cabinet, cross-project, migration-role and API-role behavior;
- append-only audit mutation denial, full-vs-redacted evidence equality, event idempotency/conflict, and authorization-vs-execution result separation;
- exact instance artifact/subject/session/generation fencing and stale-worker zero-row CAS;
- materialization partial failure, idempotent replay, previous-ready rollback, provenance and secret-reference rejection;
- resource version immutability, explicit binding, cross-scope rejection, and no silent rebind;
- automation atomic business+outbox commit, queue outage, lease expiry, duplicate/hash conflict, retry, terminal delivery, and legacy-worker fencing;
- shadow parity, final delta, immutable promotion record, pre-cutover rollback, and post-cutover forward-only repair.

Unresolved blockers remain target work, not as-built guarantees:

- canonical dynamic-app tenant/owner-anchor columns still use lengths and relations that do not meet the `String(40)`/composite target; stable non-owner legacy IDs may also require a separate compatibility decision, but are not implicitly truncated;
- no inspected scope registry, plugin-instance reciprocal FK closure, canonical grant capability identity, audit V2 table, or target promotion record exists;
- current v1 `GrantSet` lacks exact plugin-instance identity and current audit compatibility surfaces permit narrow independent appends;
- instance-independent grant semantics are not approved; omitted instance remains deny/quarantine;
- resource polymorphic scope, project binding semantics, and materialization `applying`/`skipped` mapping need implementation decisions;
- existing webhook/project-trigger workers and new automation ownership are not yet fenced by one common partition state machine; `project_triggers` needs its own trigger-queue fencing decision, and legacy lease rows cannot be treated as target-safe without a generation/CAS barrier;
- PostgreSQL RLS/FORCE policy behavior, append-only trigger/privilege boundary, large-table `NOT VALID` validation, and migration lock strategy require implementation review;
- audit retention/archive policy and final-delta observation windows are not yet operationally approved.

Until these blockers are closed by implementation evidence and an immutable promotion record, all target positive-write flags remain false.

## 13. Verification freeze record

For this documentation-only phase, only the files listed in the parent report were inspected and only this document is changed. Tests, builds, lint, formatting, compilation, Alembic execution, migration execution, environment changes, and git checks were intentionally not run. No ORM, application adapter, core documentation, database, backfill, positive write, cutover, or cleanup was performed.
