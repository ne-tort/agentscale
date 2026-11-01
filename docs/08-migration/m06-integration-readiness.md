# M06 integration readiness gate

Статус: migration-stage gate checklist, только документация. Baseline: 2026-11-01.

Цель этого gate — определить, может ли текущий M06 `canonical_grants` read/decision adapter перейти к trusted persistence integration для ограниченной partition. Документ связывает application-контракт [`canonical_grants`](../../apps/api/src/prodavan/application/canonical_grants/), [`m06-trusted-persistence-adapter.md`](m06-trusted-persistence-adapter.md), M04.e definition/version foundation, M05 instance/runtime fencing, canonical grant target DDL, typed owner/scope registry, immutable MCP capability-bundle snapshot и partition promotion record. Он **не** является разрешением на DDL, backfill, positive writes, cutover или cleanup.

Текущий adapter разрешает runtime decision через read-порт, server-side tenant/definition/plugin resolution и fencing evidence; его контракты явно говорят, что он не создаёт и не изменяет grants. Trusted integration считается готовой только после закрытия всех пунктов ниже и отдельного review promotion record.

## 1. Gate outcome and ownership

Для каждой bounded partition используется ключ `(tenant_id, app_definition_id)`, при необходимости суженный до `(scope_kind, scope_id)` или exact `plugin_instance_id`. Допустимые состояния:

| State | Meaning | Canonical positive write | Decision owner |
|---|---|---:|---|
| `read_decision_ready` | Текущий adapter может принимать fail-closed decision; persistence writes отсутствуют | нет | legacy/read adapter contract |
| `shadow_ready` | Canonical evidence, mapping, quarantine, audit и shadow parity могут собираться | нет | legacy |
| `persistence_ready` | Trusted repositories/DDL/RLS/transaction evidence reviewed, но cutover не выполнен | только явно разрешённые staged/evidence writes | legacy |
| `promotable` | Все upstream gates, parity, fencing и immutable promotion record complete | только после отдельного enable decision | legacy до cutover |
| `canonical_owner` | Bounded cutover завершён; один canonical read/write owner | да, для named partition | canonical |
| `blocked` | Любой обязательный evidence отсутствует или противоречив | нет | текущий owner, fail closed |

Правила ownership:

- `dynamic_app_capability_grants` — единственный canonical authorization owner после cutover.
- `dynamic_app_grants` остаётся read-only compatibility projection; он не может быть вторым policy owner.
- `requestedCapabilities` и v1 `GrantSet` — request/provenance, а не allow.
- Один неизвестный mapping, unresolved positive binding, unexplained parity mismatch или stale writer блокирует только affected partition, но блокирует её promotion.
- Наличие read/decision service, таблицы или live status flag само по себе не является evidence trusted persistence readiness.

## 2. Required implementation surface

### 2.1 Existing read/decision anchors

Проверить static contract текущей реализации:

- [`application/canonical_grants/contracts.py`](../../apps/api/src/prodavan/application/canonical_grants/contracts.py): exact tenant/definition/version/artifact/scope/plugin/subject/session identity, `RuntimeFencingEvidence`, bounded non-wildcard payload, expiry/revoke decision. The current `CanonicalGrant` and `CanonicalGrantRequest` are concretely plugin-instance/session-bound; an instance-independent policy is not represented by this read/decision contract and requires a separately reviewed adapter/repository path.
- [`application/canonical_grants/ports.py`](../../apps/api/src/prodavan/application/canonical_grants/ports.py): current read-only ports and their limitation; `CanonicalGrantReadPort` must not be mistaken for `CanonicalGrantRepositoryPort`. `PluginFencingDecisionPort` is a decision/read boundary, not evidence of a trusted positive-write transaction.
- [`application/canonical_grants/service.py`](../../apps/api/src/prodavan/application/canonical_grants/service.py): fail-closed resolution and fencing checks; no direct ORM insert, revoke, reissue, mapping, quarantine or trusted transaction.
- [`application/canonical_grants/__init__.py`](../../apps/api/src/prodavan/application/canonical_grants/__init__.py): exported decision contract only.
- [`application/capability_gateway_service.py`](../../apps/api/src/prodavan/application/capability_gateway_service.py): compatibility gateway boundary; current audit limitations must not be treated as durable MCP-complete evidence.
- [`infrastructure/persistence/models/dynamic_app_grants.py`](../../apps/api/src/prodavan/infrastructure/persistence/models/dynamic_app_grants.py): current table is insufficient canonical target evidence (simple version FK, three scope shapes, no capability/subject/generation digests).

### 2.2 Required implementation files and ports

The implementation PR must identify concrete owners and reviewable paths for all of the following. File names below are required target boundaries, not permission to create them in this documentation task.

| Area | Required file/port boundary | Required evidence |
|---|---|---|
| Canonical model/DDL | `apps/api/src/prodavan/infrastructure/persistence/models/canonical_grants.py` | target table, named checks/FKs/indexes, four-shape scope branch, mapping/quarantine/audit tables or reviewed adjacent model ownership |
| Definition/version | `DefinitionVersionRepository` and its M04.e implementation | resolve definition by `(tenant_id, app_id)`, version by `(app_definition_id, manifest_version)`, composite keys, immutable artifact proof |
| Typed scope | `ScopeRegistryRepository`, `ResolveScopePort`, `OwnerCompatibilityPort` | exactly one active row and parent/membership proof for all four shapes |
| Plugin instance | `PluginInstanceRepository`, `ResolvePluginInstancePort` | exact composite instance key, artifact, lifecycle, subject/session and scope anchors |
| Runtime/fencing | `RuntimeFenceRepository`, `RuntimeFencePort`, `RuntimeAdmissionPort` | current `runtime_generation`, partition/instance `writer_generation`, lease/CAS and zero-row stale mutation |
| Adapter | `ResolveCapabilityPort`, `GrantSetAdapterPort` | server-resolved identity, canonical payload/digesting, known deny/quarantine/system failure outcomes |
| Trusted grant write | `CanonicalGrantRepositoryPort` / `CanonicalGrantRepository` | issue, authorize, revoke, reissue only inside trusted transaction; no direct ORM positive insert |
| Source mapping | `GrantMappingRepositoryPort` / `GrantMappingRepository` | `(source_kind, source_id, canonical_schema_version)` idempotency, digest compare, conflict quarantine |
| Quarantine | `GrantQuarantineRepositoryPort` / `GrantQuarantineRepository` | visible, counted, retryable, fail-closed evidence; never authorization input |
| Audit | `CapabilityAuditPort` / `CapabilityAuditRepository` | append-only event in same commit boundary, role/trigger boundary, full envelope |
| Legacy projection | `LegacyGrantProjectionRepository` | read-only projection refresh/parity only, no post-cutover decision or write |
| Shadow/parity | `ShadowReadPort` and partition parity report | normalized decision/binding/digest comparison and final-delta checkpoint |
| Gateway | `CapabilityGatewayService` integration point | compatibility boundary delegates to resolver and trusted repository; never grants from JSONB/request assertions |
| MCP snapshot | `CapabilityBundleRow` / `capability_bundles` integration and snapshot-link contract | immutable bundle digest and exact attachment/package/allowlist provenance linked from audit/promotion evidence |
| Promotion | immutable append-only partition promotion record | source/target digests, gates, owner transition, fencing and rollback window; not a mutable readiness flag |

Repository sessions are implementation details, not a security boundary. Every positive repository method must require resolved context and writer epoch; there must be no alternate method that can insert/modify a positive grant outside the trusted transaction.

## 3. Upstream M04/M04.e/M05 gates

All checks are per candidate partition and must link to named evidence IDs, schema/revision lineage and source snapshot digests.

### M04 owner/scope registry

- [ ] Canonical tenant is `companies.id`, called `tenant_id` in application/RLS context; physical type/length is aligned, with overlength/malformed values quarantined before conversion.
- [ ] Definition owner key `(app_definition_id, tenant_id)` and parent anchor keys exist and are validated.
- [ ] Cabinet/company relation is proven through active `cabinet_company_grants` or an explicitly approved owner relation; mirrored nullable cabinet columns alone are insufficient.
- [ ] Project relation proves `(project_id, tenant_id, cabinet_id)` compatibility and active cabinet access; independent FKs alone are insufficient.
- [ ] `scope_registry` has unique `(scope_kind, scope_id)` and immutable source/schema/payload digest evidence.
- [ ] Registry rows have `owner_state=active` for positive paths; archived/quarantined rows are migration/security-only.
- [ ] `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, both `USING` and `WITH CHECK`, deny absent/malformed transaction-local context.

### M04.e definition/version

- [ ] Every retained definition resolves within one authenticated tenant by `(tenant_id, app_id)`.
- [ ] Every version resolves only by `(app_definition_id, manifest_version)` and has same-definition key `(id, app_definition_id)`.
- [ ] `app_artifact_digest` is immutable and matches the resolved version; lifecycle is explicitly eligible.
- [ ] Orphan, cross-tenant, cross-definition, duplicate or artifact-conflicting rows are mapped/quarantined with checkpoint and stable reason.
- [ ] M04.e evidence includes retained/archived/quarantined counts, deterministic resolver proof, source/canonical digests and `no_positive_write` marker.

### M05 plugin instance/runtime fencing

- [ ] Exact instance key is available and validated: `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)`.
- [ ] Instance and registry reciprocal/staged FKs are named and validated before instance-bound positive grants.
- [ ] Instance artifact equals immutable version artifact; lifecycle is approved (`published` or explicitly equivalent), not inferred from registry membership.
- [ ] Exact `subject_kind/subject_id`, `session_id`, `plugin_instance_id` and installation scope are resolved server-side.
- [ ] Current `runtime_generation`, `writer_generation`, lease owner and lease ID are available where runtime admission requires them.
- [ ] Restart/redeploy advances runtime generation; stale runtime calls deny and audit. Stale writer CAS affects zero rows and audits a fencing event.
- [ ] No fallback to another instance, app-wide permission or instance-independent path is allowed.
- [ ] M05 promotion evidence includes instance/source/target digests, lifecycle/quarantine counts, subject/session proof, current generations and stale-worker denial evidence.

## 4. Four scope shapes and plugin anchors

`scope_registry` supports exactly four typed shapes. A polymorphic `scope_id` or copied IDs in JSONB is not sufficient.

| Shape | Required identity/anchors | Positive-path proof |
|---|---|---|
| `tenant` | `scope_id = tenant_id`; `cabinet_id`, `project_id`, `plugin_instance_id` are null | retained active `companies.id`; tenant context matches; no narrower anchor is present |
| `cabinet` | `scope_id = cabinet_id`; `tenant_id` and cabinet anchor non-null; project/instance null | active cabinet exists and active tenant-cabinet access/owner relation proves compatibility |
| `project` | `scope_id = project_id`; `tenant_id`, `cabinet_id`, `project_id` non-null; instance null | project company/cabinet exactly match registry and active cabinet access exists |
| `plugin_instance` | `scope_id = plugin_instance_id`; exact instance anchor non-null; cabinet/project mirror installation scope, or both null only under explicit tenant-install policy | exact instance composite key, definition/version/tenant/scope/artifact/lifecycle/subject/session/runtime proof; no implicit project inheritance |

For every shape:

- [ ] Exactly one active registry row resolves for `(scope_kind, scope_id)`.
- [ ] `tenant_id` equals the resolved `companies.id`; request/plugin `company_id`, tenant, cabinet and project are assertions to compare, never authority.
- [ ] Parent `EXISTS` checks prove retained tenant, scope lifecycle, membership/compatibility and definition owner.
- [ ] `scope_kind=plugin_instance` is present in the final canonical grant model/check, not only in the registry design.
- [ ] For an instance-bound grant, `plugin_instance_id` is exact and is not replaced by app, version, scope or session ID.
- [ ] An instance-independent declarative grant is positive only under a separately approved bounded policy with exact subject/scope/artifact/runtime admission; missing instance otherwise denies/quarantines.

## 5. Canonical grant DDL and trusted transaction

### 5.1 DDL evidence

The final implementation must reconcile the current model with [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md). The current `dynamic_app_capability_grants` model is not final because it has `String(80)` identity fields, simple cascading version FK and only `tenant|cabinet|project` scope check.

- [ ] `dynamic_app_capability_grants` stores `app_definition_id`, `app_id`, `app_version_id`, canonical tenant/scope anchors, exact capability key, explicit subject, optional exact instance/session, bounded operations/resources/domains/constraints, four payload digests, lifecycle, `grant_generation`, `writer_generation`, idempotency and source/schema provenance.
- [ ] Composite FK `(app_version_id, app_definition_id)` references the same-definition version key with `ON DELETE RESTRICT`; definition-tenant proof is present.
- [ ] Exact instance FK references `(plugin_instance_id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)` with `ON DELETE RESTRICT`, after M05 key closure.
- [ ] Final scope check includes all four approved shapes where grant policy permits them, including `plugin_instance` with `scope_id=plugin_instance_id` and installation anchors.
- [ ] Operations/resources/domains are canonical, non-empty, sorted, duplicate-free arrays; constraints is a bounded object; all four digests use canonical serialization.
- [ ] Active full-tuple uniqueness is enforced without a moving-clock `expires_at` predicate; expiry is read-time deny.
- [ ] Revoke is forward-only, reissue creates a new `grant_generation`, and old rows/evidence remain retained.
- [ ] Mapping, quarantine and audit tables exist before positive import; changed source digest is conflict/quarantine, never overwrite.
- [ ] All target tables have named `ENABLE` + `FORCE RLS` policies with `USING` and `WITH CHECK`; API roles cannot bypass tenant boundary.
- [ ] Audit `UPDATE`/`DELETE` privileges are revoked from application/API roles and a deny-by-default mutation boundary is reviewed.
- [ ] Security/provenance FKs use `RESTRICT` or approved archive semantics; cleanup cannot cascade away grant, mapping, quarantine or audit evidence.

### 5.2 Trusted transaction evidence

For issue, revoke/reissue, instance lifecycle or runtime admission mutation, one trusted transaction must demonstrate:

1. [ ] Server resolves authenticated principal to exactly one `companies.id`; transaction-local `app.tenant_id` and optional cabinet/project/user/role context are set from that resolution.
2. [ ] Partition/instance writer epoch is locked or CAS-checked; stale epoch affects zero rows.
3. [ ] Active typed scope and parent/membership compatibility are resolved and checked with `EXISTS`.
4. [ ] Definition/version, same-definition identity, lifecycle and immutable artifact are resolved and checked.
5. [ ] Exact subject/session/plugin instance and current runtime generation/lease are checked where required.
6. [ ] Capability key is preserved exactly; mapping version is approved; canonical payload, binding and operation/resource/domain/constraint digests are computed.
7. [ ] Source mapping idempotency and active full-tuple uniqueness are compared atomically; a changed digest is conflict/quarantine.
8. [ ] Canonical row is mutated only for an eligible positive candidate and audit/mapping/lifecycle evidence is written in the same commit boundary.
9. [ ] Commit succeeds only after RLS `USING`/`WITH CHECK`, parent checks, FKs, role privileges and append-only audit append succeed.
10. [ ] Audit failure rolls back the grant/mapping/lifecycle mutation and returns deny/system failure, never allow.

## 6. Method mapping and capability semantics

- [ ] `GrantSet.name` is preserved exactly as `capability_key`; different names never merge.
- [ ] `decision=denied` is known deny/provenance evidence, never a positive row.
- [ ] `requestedCapabilities` is corroboration only; manifest presence cannot authorize.
- [ ] `methods -> operations` uses a closed, versioned registry, for example `(mapping_name, mapping_version, protocol_version, method, operation, semantics_digest)`.
- [ ] Mapping version is persisted in schema/provenance and audit; mapping changes create a new version and do not silently rewrite existing grants.
- [ ] Absent `methods` is not wildcard; unknown, duplicate, ambiguous or deprecated methods are quarantine.
- [ ] Unsupported constraints, including unenforced `eventTypes` or `maxBytesPerRequest`, are quarantine rather than dropped or broadened.
- [ ] `operations`, `resources`, `domains` and constraints are canonicalized deterministically; wildcard/unbounded semantics are denied unless a separate bounded policy explicitly defines them.
- [ ] One source/schema/digest replay is idempotent; same source identity with a changed digest is conflict/quarantine.

## 7. MCP bundle snapshot and promotion linkage

MCP tool identity is provenance/correlation, not a `capability_key`. The trusted integration must link the authorization/audit event to an immutable capability-bundle snapshot.

### 7.1 Snapshot evidence

- [ ] `capability_bundles` (currently represented by [`CapabilityBundleRow`](../../apps/api/src/prodavan/infrastructure/persistence/models/runtime_revisions.py)) stores canonical serialized manifest and content-addressed `digest`; the current ORM row is only a manifest/digest container and does not by itself prove immutability or MCP linkage.
- [ ] Snapshot digest covers the manifest plus the MCP attachment data required by the runtime persistence map: `project_id`, `profile_id`, `tool_server`, attachment ID, package name/version, qualified package digest and normalized allowlist.
- [ ] Normalized allowlist is explicit and bounded; it does not become a generic wildcard or replace declarative capability identity.
- [ ] Snapshot is immutable once published; replacement creates a new digest/version and retains the previous snapshot for rollback evidence.
- [ ] Bundle/revision linkage (`project_runtime_revisions.bundle_id`) and any attachment/link table are recorded in the grant/audit provenance; a standalone lock table is optional only if this linkage is durable and deterministic.
- [ ] Audit carries `profile_id`, `tool_server`, `tool_name`, `tool_version`, `span_id`, canonical redacted `arguments_digest`, result status, error code and duration; raw arguments, tokens and secrets are excluded.
- [ ] Pre-invocation authorization and final adapter outcome are both linked to the same request/trace/span and snapshot digest. Allow is not execution success; missing adapter, exception or malformed result emits final deny/error.

### 7.2 Promotion record contents

For every candidate partition, create an immutable append-only promotion record even when the outcome is `blocked` or `quarantined`. It must include:

- [ ] partition key and optional narrowed scope/instance key;
- [ ] source snapshot ID/schema version, source row counts and source digest;
- [ ] canonical payload, binding, operations/resources/domains/constraints and MCP bundle/snapshot digests;
- [ ] M04 scope and parent compatibility evidence;
- [ ] M04.e definition/version owner, resolver and artifact evidence;
- [ ] M05 exact instance composite key/FK, artifact, subject/session and runtime/writer generation evidence;
- [ ] M06 schema constraint/index/policy/privilege inventory and trusted writer identity;
- [ ] mapping, quarantine and audit counts, with all unresolved/unknown rows and stable reasons;
- [ ] final-delta checkpoint, observation window, normalized parity report and zero-unknown/zero-unresolved decision;
- [ ] legacy writer drain proof, fencing epoch, current writer generation and exactly-one-writer evidence;
- [ ] decision owner, proposed canonical read/write owner, actor, timestamp, correlation ID and reviewer/revision lineage;
- [ ] rollback window, rollback target/command and explicit `canonical_positive_writes` value;
- [ ] outcome `blocked|shadow|persistence_ready|promotable|canonical_owner`, never inferred from a mutable live status flag.

A promotion record authorizes nothing by itself. It is complete only when all required evidence is immutable, cross-linked and reviewed. Every pre-cutover record (`blocked`, `quarantined`, `shadow` or `persistence_ready`) must carry `canonical_positive_writes=false`; the field is absent from live status/feature flags and may become `true` only in the reviewed final record for the named partition after criteria 1–12 pass.

## 8. Parity, fencing and bounded cutover

### Shadow and parity checklist

- [ ] Legacy remains decision owner during shadow; canonical result is comparison evidence only.
- [ ] Comparison uses exact tenant, scope, definition/version, artifact, subject, session, plugin instance, capability, operation/resource/domain, lifecycle and relevant generation context.
- [ ] Known deny, expiry, revoke and approved quarantine are compared by explicit reason class, not counted as positive parity.
- [ ] Zero unexplained allow/deny mismatches for the observation window.
- [ ] Zero unknown mappings and zero unresolved positive bindings for retained source rows.
- [ ] Every positive target row passes typed scope, parent `EXISTS`, artifact, subject/session, lifecycle, FK and RLS proof.
- [ ] No stale runtime generation, lease or writer generation is accepted.
- [ ] Final delta is idempotent, checkpointed and included in the immutable promotion record.

### Fencing and cutover checklist

- [ ] Freeze new legacy changes for the partition.
- [ ] Advance/fence writer generation and drain in-flight legacy workers.
- [ ] Prove exactly one current eligible writer and one authorization owner.
- [ ] Apply final delta through trusted mapping/idempotency transaction.
- [ ] Record cutover audit and promotion record before switching owner.
- [ ] Switch gateway reads and management commands to `CanonicalGrantRepository` only for named partition.
- [ ] Mark `dynamic_app_grants` read-only projection and preserve mapping IDs.
- [ ] Fail closed on any missing evidence; do not promote globally because another partition is incomplete.

## 9. Rollback and recovery

### Before canonical read cutover

- [ ] Stop adapter/backfill writes for the affected partition.
- [ ] Keep legacy reads/writes authoritative.
- [ ] Retain canonical rows, mappings, quarantine, audit and MCP snapshot evidence.
- [ ] Repair forward with the same source identity, digest comparison, checkpoint and stable reason; do not delete rows to hide mismatch.
- [ ] Schema downgrade is considered only before dependent data import and after dependency/export preflight.

### After canonical read cutover

- [ ] Do not restore legacy writes or create dual authorization owners.
- [ ] Fence canonical writer generation and stop issue/revoke workers for affected partition.
- [ ] Keep canonical reads authoritative and fail closed on incomplete reconciliation.
- [ ] Repair using canonical CAS/idempotency commands, new forward generation/reissue and forward revisions.
- [ ] Preserve grant history, mappings, quarantine, audit, bundle snapshots and promotion records; no rollback deletion of security evidence.
- [ ] Stale legacy and canonical workers receive zero-row mutation/deny; no fallback to another plugin instance or app-wide grant.

## 10. Explicit positive-write enable criteria

Positive canonical writes (`issue`, positive import, `revoke`, `reissue`, runtime/instance publication where applicable) remain disabled unless **all** criteria below are true for the named partition in one reviewed decision:

1. [ ] M04 typed registry is complete for the partition, with exactly one active valid scope row and parent/membership compatibility proof.
2. [ ] M04.e retained tenant, definition/version owner, deterministic resolution and immutable artifact evidence is complete.
3. [ ] M05 exact instance key/FK, artifact, lifecycle, subject/session and current runtime/writer generation/lease evidence is complete whenever policy requires instance binding.
4. [ ] Final canonical DDL is implemented and reviewed: four-shape scope checks, same-definition and instance composite FKs, `RESTRICT`/archive semantics, checks, indexes, active uniqueness and no moving-clock predicate.
5. [ ] Mapping, quarantine and append-only audit persistence exists before positive import; audit roles/privileges/trigger boundary are reviewed.
6. [ ] Trusted repository/transaction path sets server-derived RLS context and passes parent `EXISTS`, `USING`, `WITH CHECK`, role and writer CAS checks atomically.
7. [ ] Capability identity and method mapping version are approved; no absent-method wildcard, unsupported constraint, broadening transform, unknown mapping or changed-digest conflict remains unresolved.
8. [ ] MCP bundle snapshot is immutable, digest-linked and contains the required project/profile/server/package/attachment/allowlist identity; audit final outcome linkage is complete.
9. [ ] Shadow observation has zero unexplained mismatches, zero unknown mappings and zero unresolved positive bindings; known deny/quarantine classes are explicitly reconciled.
10. [ ] Legacy writer is drained/fenced, current writer generation is unique, and final delta checkpoint is complete.
11. [ ] Immutable promotion record contains all M04/M04.e/M05/M06, snapshot, parity, fencing, owner-transition and rollback evidence, with `canonical_positive_writes=true` only as the final explicit enable decision.
12. [ ] Named owner approves the partition; no unresolved blocker, security review gap or verification-freeze violation remains.

Any failed, missing, stale, ambiguous or contradictory criterion produces `deny`, `quarantine` or explicit system failure. It never enables a positive write. `canonical_positive_writes=true` is valid only in the promotion record after criteria 1–12 are evidenced; it is not a default, schema flag or live status shortcut.

## 11. Known blockers and verification freeze

Current blockers that must be resolved by implementation/review evidence include:

- current persistence model is not the target canonical DDL: it lacks exact capability identity, subject/session, four digests, generation/fencing and composite same-definition/instance FKs;
- current scope check omits `plugin_instance`, and current dynamic-app IDs may be `String(80)` while canonical anchors are `String(40)`;
- current instances may lack canonical tenant, exact subject/session, artifact, runtime and writer-generation fields;
- v1 `GrantSet` lacks `plugin_instance_id`, so instance-bound imports require runtime/session resolution;
- instance-independent policy is incomplete; omit-instance remains deny/quarantine;
- current gateway audit is not yet durable MCP-complete without bundle snapshot and final outcome linkage;
- PostgreSQL RLS/FORCE RLS, append-only audit enforcement, active uniqueness, deferred/cyclic FK validation and trusted transaction behavior require implementation-specific review;
- no promotion is safe with unknown mappings, unresolved positive bindings, unexplained parity mismatch or an unfenced writer.

Для этой документационной задачи намеренно не запускались tests, backend/frontend tests, build, compile, lint, format, Alembic, migration execution, environment changes или любые git checks. Код, ORM, core docs, shared environments и databases не изменялись. Backfill, positive writes, cutover, rollback drills и MCP snapshot promotion не выполнялись. Verification remains frozen until migration implementation is complete, reviewed multiple times and the coordinator explicitly lifts the freeze.
