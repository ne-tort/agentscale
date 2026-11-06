# M04–M06 adapter implementation decomposition

Статус: migration-stage implementation decomposition, только документация. Baseline: 2026-11-01.

Документ раскладывает реализацию M04 `owner/scope registry`, M05 `plugin instance registry/runtime fencing` и M06 `canonical capability grants/gateway adapter` на application ports, persistence repositories, Alembic revisions/jobs и bounded rollout. Он согласован с [`plugin-instance-scope-decision.md`](plugin-instance-scope-decision.md), [`owner-registry-implementation-plan.md`](owner-registry-implementation-plan.md), [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md) и [`persistence-target-map.md`](persistence-target-map.md).

Это не Alembic revision, не ORM implementation и не разрешение shared-environment DDL, backfill, positive canonical writes или cutover. Локальные labels M04/M05/M06 здесь обозначают bounded implementation domains, а не номера Alembic revisions и не заменяют cross-domain order из `owner-registry-implementation-plan.md`/`persistence-target-map.md`: definition/version foundation и typed parent proof должны быть завершены до instance foundation, canonical grants — после instance key/FK closure, а adapter/backfill/parity/cutover остаются последующими gates. Исполняемый DDL сначала должен быть выражен в SQLAlchemy-моделях, затем сгенерирован в линейной Alembic chain от актуального repository head и отдельно reviewed. Backfill выполняется resumable job/command с checkpoint, а не скрытым `autogenerate`.

## 1. Fixed decisions and boundaries

### 1.1 Target ownership

- `companies.id` остаётся canonical tenant identity; application vocabulary использует `tenant_id`, физический тип и длина выравниваются с `String(40)` после inventory.
- `scope_registry` имеет ровно четыре typed shape: `tenant`, `cabinet`, `project`, `plugin_instance`.
- `(scope_kind, scope_id)` не является polymorphic FK. Каждый scope разрешается ровно в одну active registry row.
- Для `scope_kind = 'plugin_instance'`: `scope_id = plugin_instance_id`, `plugin_instance_id IS NOT NULL`; tenant и installation cabinet/project anchors обязательны согласно installation scope.
- `dynamic_app_capability_grants` становится единственным authorization owner после bounded partition cutover. `dynamic_app_grants` до cleanup остаётся read-only compatibility projection.
- Exact `plugin_instance_id` является отдельной runtime binding identity. `app_id`, `app_version_id`, `scope_id` и `session_id` никогда не заменяют его.
- Registry membership не равна authorization eligibility: нужны definition/version, artifact, subject/session, lifecycle, `runtime_generation` и `writer_generation` checks.
- Instance-independent declarative grant допустим только при явно versioned policy, bounded subject/scope и отдельном runtime admission. Отсутствующий instance не означает «все instances».

### 1.2 Application ownership by phase

| Phase | Read owner | Write owner | Canonical positive write |
|---|---|---|---|
| M04 foundation | legacy application state; registry is evidence | migration job for registry/evidence | запрещена |
| M05 instance foundation | legacy state plus instance shadow reads | fenced instance/runtime writer only for eligible staged rows | запрещена до M05 gate |
| M06 shadow | legacy grant decision | legacy writer; adapter may write mapping/audit and non-eligible canonical evidence | canonical grant allow запрещён |
| M06 partition cutover | canonical repository/gateway | one canonical writer generation | разрешена только после all gates |
| rollback window | canonical after read cutover | canonical forward repair; legacy projection read-only | no dual owner |

A partition is `(tenant_id, app_definition_id)`, optionally narrowed by scope or exact instance. One failed partition does not promote or roll back unrelated partitions.

## 2. Application ports

Ports are protocol boundaries, not concrete implementations. They must derive identity from authenticated server context; request/plugin claims are assertions to compare and audit.

### 2.1 M04 owner/scope registry ports

```text
ResolveTenantPort
  resolve(principal, request_context) -> ResolvedTenant
  ResolvedTenant = { tenant_id, user_id, role, source, evidence_digest }

ResolveScopePort
  resolve(tenant_id, scope_kind, scope_id, context) -> ActiveScope | ScopeResolutionFailure
  ActiveScope = {
    registry_id, scope_kind, scope_id, tenant_id,
    cabinet_id?, project_id?, plugin_instance_id?, owner_state
  }

RegisterScopeEvidencePort
  observe(source_ref, candidate_scope) -> MappingOutcome
  MappingOutcome = { mapping_id, status, target_id?, reason_code, checkpoint }

OwnerCompatibilityPort
  prove_cabinet_access(tenant_id, cabinet_id, principal) -> Proof
  prove_project_anchor(tenant_id, cabinet_id, project_id) -> Proof
```

`ResolveScopePort` returns only `owner_state = active` for positive paths. Archived/quarantined rows remain available to migration/security/audit roles, never to ordinary authorization reads. The port must reject missing/malformed transaction context and ambiguous parent tuples.

### 2.2 M05 plugin instance/runtime ports

```text
ResolvePluginInstancePort
  resolve(tenant_id, app_definition_id, app_version_id,
          scope, subject, session, presented_instance?)
    -> ResolvedPluginInstance | InstanceResolutionFailure

PublishInstancePort
  publish(instance_id, expected_writer_generation, runtime_evidence)
    -> PublishedInstance | Fenced | Rejected

RuntimeAdmissionPort
  admit(instance_id, runtime_generation, artifact_digest, session_id)
    -> RuntimeAdmission | Denied

RuntimeFencePort
  current(instance_id/partition) -> { runtime_generation, writer_generation }
  compare_and_advance(partition, expected_writer_generation) -> FenceResult

InstanceLifecyclePort
  disable/revoke/quarantine(instance_id, reason, expected_generation)
    -> LifecycleTransition
```

`ResolvedPluginInstance` must prove exact instance composite identity:

```text
(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)
```

It also carries immutable `artifact_digest`, subject/session binding, lifecycle status and current runtime/writer generations. A stale runtime generation denies a call; a stale writer generation produces zero-row mutation and an audit event. No fallback to another instance or app-wide grant is allowed.

### 2.3 M06 canonical grant and gateway ports

```text
ResolveCapabilityPort
  resolve(request, authenticated_context) -> ResolvedCapabilityContext

GrantSetAdapterPort
  adapt(source_row, resolved_context) ->
    AllowCandidate | KnownDeny | QuarantineCandidate | AdapterFailure

CanonicalGrantRepositoryPort
  issue(command, expected_writer_generation) -> GrantResult
  authorize(query_context) -> AuthorizationDecision
  revoke(command, expected_writer_generation) -> RevokeResult
  reissue(command, expected_generation) -> GrantResult

GrantMappingRepositoryPort
  record(source_identity, source_digest, canonical_digest, outcome)
  find(source_kind, source_id, schema_version) -> MappingRecord?

CapabilityAuditPort
  append(event) -> AuditReceipt

ShadowReadPort
  compare(legacy_decision, canonical_decision, comparison_context)
    -> ParityObservation
```

`CapabilityGatewayService` remains the compatibility boundary during M06. It resolves tenant, scope, definition, version, subject, session and instance server-side, then delegates persistence to the canonical repository. It never authorizes from requested capabilities, legacy JSONB, caller-supplied ownership or adapter-only assertions.

## 3. Persistence repositories and transaction boundary

### 3.1 Repository decomposition

| Repository | Owned tables/relations | Required operations | Security rule |
|---|---|---|---|
| `ScopeRegistryRepository` | `scope_registry`, typed anchor keys | resolve active scope; insert/update evidence state; archive/quarantine transition | no hard delete of retained owner rows; active-only positive resolution |
| `OwnerMappingRepository` | scope/owner mapping and checkpoint records | idempotent source mapping, digest conflict detection, checkpoint resume | changed source digest is conflict/quarantine, never overwrite |
| `DefinitionVersionRepository` | `dynamic_app_definitions`, `dynamic_app_versions` | resolve definition by `(tenant_id, app_id)`; version by `(definition_id, manifest_version)`; validate artifact | simple version ID is insufficient; same-definition composite proof required |
| `PluginInstanceRepository` | `dynamic_app_instances` or dedicated runtime table | exact resolve, lifecycle transition, runtime evidence, composite-key lookup | mutations use CAS on `writer_generation`; lifecycle and artifact are checked in transaction |
| `RuntimeFenceRepository` | instance/partition generation state | read current epoch; advance with compare-and-set; reject stale worker | no stale issue/revoke/runtime mutation |
| `CanonicalGrantRepository` | `dynamic_app_capability_grants` | issue, authorize, revoke, reissue, expiry observation | only trusted transaction; exact scope/instance/subject/session checks |
| `GrantMappingRepository` | `dynamic_app_capability_grant_mappings` | source-to-target idempotency and digest comparison | one source/schema key; target link nullable for deny/quarantine |
| `GrantQuarantineRepository` | `dynamic_app_capability_grant_quarantine` | insert/count/retry/resolve quarantine | migration/security scoped; never in authorization query |
| `CapabilityAuditRepository` | `dynamic_app_capability_audit` | append issue/allow/deny/import/revoke/fence/cutover event | append-only; audit failure cannot become allow |
| `LegacyGrantProjectionRepository` | `dynamic_app_grants` | read-only projection refresh and comparison | no authorization decision and no post-cutover writes |

### 3.2 Trusted transaction

For a positive issue/revoke/runtime transition, one trusted transaction must:

1. set transaction-local `app.tenant_id` and optional `app.cabinet_id`, `app.project_id`, `app.user_id`, `app.role` from authenticated context;
2. lock or CAS the relevant partition/instance writer epoch;
3. resolve active registry row and parent compatibility via `EXISTS` checks;
4. resolve definition/version and immutable artifact;
5. resolve exact instance, subject/session and runtime generation when policy requires it;
6. validate canonical payload digests, idempotency and active uniqueness;
7. write grant/mapping/lifecycle transition and append audit atomically;
8. commit only if RLS `USING`/`WITH CHECK` and all positive-write gates pass.

A repository method must not expose an API that can insert a positive grant without this transaction boundary. Audit, mapping and quarantine writes are separate role paths but must preserve the same redacted source/target evidence.

## 4. M04: owner/scope registry implementation

### 4.1 Schema and Alembic slices

M04 is implemented as several linear revisions/jobs, not one large DDL transaction:

1. **M04.a type and anchor preparation**
   - inventory all candidate tenant/cabinet/project IDs and overlength/malformed values;
   - align future canonical `tenant_id` with `companies.id` (`String(40)`), quarantine invalid values before conversion;
   - add definition owner key `(id, tenant_id)` and project compatibility key `(projects.id, projects.company_id, projects.cabinet_id)` where needed;
   - preserve `company_id` as provenance/compatibility only; no implicit cast or guessed owner.
2. **M04.b empty registry/evidence tables**
   - create `scope_registry`, owner mappings, quarantine and append-only audit foundation;
   - create unique `(scope_kind, scope_id)` and typed anchor uniqueness;
   - create checks for tenant/cabinet/project/plugin-instance shapes, but defer the instance relation until M05 key exists;
   - create indexes and rollback inventory for named constraints, policies and helper functions.
3. **M04.c parent registry backfill job**
   - import retained tenant rows;
   - import cabinet rows only with active cabinet-company access proof;
   - import project rows only with matching tenant/cabinet registry rows and active compatibility proof;
   - record source ID, source digest, resolved anchors, status and checkpoint for every row.
4. **M04.d validation/tightening**
   - validate `NOT VALID` constraints after quarantine and backfill;
   - tighten only rows with deterministic typed shape; leave unresolved historical rows quarantined/archived;
   - do not enable positive plugin-instance branch before M05 exact identity is available.
5. **M04.e definition/version foundation**
   - establish retained definition ownership `(id, tenant_id)`, same-definition version key `(id, app_definition_id)`, manifest-version uniqueness and immutable artifact proof before any instance or grant backfill;
   - resolve versions only by `(app_definition_id, manifest_version)` and quarantine orphan, cross-tenant, cross-definition or artifact-conflicting rows;
   - treat this as the definition/version prerequisite corresponding to the owner-registry plan's cross-domain M04; it is not permission for positive grants.

### 4.2 Typed shape rules

```text
tenant:
  scope_id = tenant_id
  cabinet_id IS NULL, project_id IS NULL, plugin_instance_id IS NULL

cabinet:
  scope_id = cabinet_id
  tenant_id IS NOT NULL
  project_id IS NULL, plugin_instance_id IS NULL

project:
  scope_id = project_id
  tenant_id IS NOT NULL, cabinet_id IS NOT NULL
  plugin_instance_id IS NULL

plugin_instance:
  scope_id = plugin_instance_id
  plugin_instance_id IS NOT NULL, tenant_id IS NOT NULL
  cabinet_id/project_id mirror installation scope
```

A tenant-scoped installation may have null cabinet/project only when the installation policy explicitly allows it. A plugin row does not inherit project scope implicitly. Installation scope changes create a new forward identity/state; the existing registry identity is not rewritten to hide history.

### 4.3 M04 backfill and quarantine

The job is resumable per source anchor and idempotent by `(source_kind, source_id, schema_version, source_payload_digest)`. It must classify, not repair by guess:

- valid retained owner -> registry `active` or policy-approved `archived`;
- missing/deleted parent -> quarantine or archived evidence;
- overlength/malformed ID -> quarantine before type conversion;
- ambiguous cabinet/company or project tuple -> quarantine;
- duplicate/conflicting source digest -> conflict/quarantine;
- cross-tenant/cross-cabinet relation -> quarantine and security audit.

Quarantine stores redacted metadata, partial resolved anchors and checkpoint. It cannot satisfy registry resolution or positive authorization. Unknown required parent mappings block the affected partition.

### 4.4 M04 RLS and gate

`scope_registry` and tenant-owned evidence tables use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`. Policies include both `USING` and `WITH CHECK` and deny missing/malformed transaction-local context. Registry positive policy requires:

- `tenant_id = current_setting('app.tenant_id', true)`;
- active typed row and correct scope shape;
- cabinet/project context and active membership compatibility;
- no archived/quarantined row in ordinary tenant API reads.

**M04 gate:** all retained candidate parent anchors and definition/version prerequisites are deterministically classified; active registry rows have valid typed shape and parent proof; definition/version same-definition keys and immutable artifact proof are available; no unknown required parent/version remains in the partition; registry/evidence RLS is installed; no positive plugin-instance/grant write is enabled.

## 5. M05: plugin instance registry, runtime generation and fencing

### 5.1 Schema and Alembic slices

1. **M05.a instance identity preparation**
   - extend `dynamic_app_instances` or introduce a dedicated runtime-instance table with exact `plugin_instance_id`;
   - add canonical `tenant_id`, `scope_kind`, `scope_id`, cabinet/project anchors, subject/session fields and immutable `artifact_digest`;
   - add lifecycle `draft | published | disabled | revoked | quarantined`.
2. **M05.b definition/version and instance keys**
   - consume the M04 definition/version foundation: `dynamic_app_definitions` owner key `(id, tenant_id)`, `dynamic_app_versions` key `(id, app_definition_id)`, scoped manifest-version resolution and immutable artifact proof must already be validated;
   - create unique instance key `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)`;
   - add checks for scope shape, definition/version ownership and artifact consistency.
3. **M05.c generation/fencing state**
   - add monotonic `runtime_generation` and partition/instance `writer_generation`;
   - persist runtime reference and start/stop/heartbeat evidence without secrets;
   - add CAS update path for lifecycle and runtime worker operations.
4. **M05.d close registry-instance cycle**
   - after the instance unique key exists, create named registry-to-instance and reciprocal instance-to-registry constraints in a follow-up revision;
   - validate all rows and only then make required links non-null;
   - enable plugin-instance positive resolution only after validation.

### 5.2 `use_alter`, deferred FK and nullable staging

The registry/instance relation is cyclic. SQLAlchemy relations that require table creation before the opposite target exists must use explicit stable constraint names and `use_alter=True` where appropriate. The implementation sequence is:

1. create registry and instance tables/columns without the late cyclic FK;
2. create/validate instance composite unique key;
3. backfill exact instance identities and quarantine invalid rows;
4. run a separate Alembic `op.create_foreign_key` revision for each named direction;
5. validate `NOT VALID` constraints and record validation evidence;
6. tighten temporary nullable linkage to the final contract before M06 positive writes.

`DEFERRABLE INITIALLY DEFERRED` is allowed only for a reviewed same-transaction cycle where every writer uses the trusted transaction and deferred validation cannot hide invalid ownership. Otherwise split the relation into follow-up revisions. Nullable/deferred linkage is migration staging, not a permanent adapter assertion. No generic FK on `scope_id` is acceptable.

### 5.3 M05 instance backfill/quarantine

Backfill an instance only when exact definition, version, tenant, scope, artifact and installation identity are proven. A v1 `GrantSet` does not contain `plugin_instance_id`; the adapter must resolve it from authenticated runtime/session registry. Missing subject/session, stale runtime evidence, duplicate identity, cross-tenant relation, artifact mismatch or unsupported lifecycle yields `draft`, `disabled` or `quarantined` by explicit policy, never inferred `published`.

Historical rows retain source ID/digest and checkpoint. Quarantine is retryable only through a forward mapping with the same source identity; changed digest is a conflict. No row with unknown instance identity may become a positive grant.

### 5.4 M05 RLS and gate

Instance and runtime tables use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, with `USING` and `WITH CHECK`. Policies require active registry scope, same tenant/definition/version, exact scope anchors, and current authenticated context. `EXISTS` checks validate lifecycle and runtime generation; copied denormalized columns do not prove ownership.

`runtime_generation` changes on restart/redeploy. A call carrying an old generation is denied and audited. `writer_generation` is a fencing epoch: issue/revoke/lifecycle changes use CAS and stale workers receive zero-row mutation/deny.

**M05 gate:** exact instance composite key and staged FK relation are validated; artifact equality, lifecycle, subject/session and current runtime/writer generation are proven; stale/duplicate/cross-tenant rows are quarantined; RLS/FORCE RLS and trusted fencing path are reviewed; positive grant insert remains disabled until M06.

## 6. M06: canonical grant DDL and gateway adapter

### 6.1 Schema and Alembic slices

1. **M06.a evidence and projection foundation**
   - create mappings, quarantine and append-only audit before canonical import;
   - retain source/canonical payload digests, schema version, status, reason and checkpoint;
   - keep `dynamic_app_grants` available as legacy decision owner/projection according to partition phase.
2. **M06.b canonical grant table**
   - create `dynamic_app_capability_grants` with definition/version, canonical tenant/scope anchors, exact capability key, subject/session, optional exact instance, bounded policy JSON and four digests, lifecycle/generation/fencing/idempotency fields;
   - use composite same-definition version FK, definition-tenant FK and exact instance composite FK, all `ON DELETE RESTRICT`;
   - revise the current DDL scope check to include the approved `plugin_instance` branch. The `canonical-grant-target-ddl.md` three-shape check is an intermediate contract delta, not final after M04/M05.
3. **M06.c indexes/checks/trusted writer**
   - add non-empty capability/operations/resources/domains/constraints checks, expiry/revoke/generation checks and active full-tuple uniqueness;
   - create authorization, validity, instance-generation and source lookup indexes;
   - do not put moving-clock expiry in a partial index predicate.
4. **M06.d RLS and privilege boundary**
   - enable `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` on grants, audit, mappings, quarantine and other new tenant-owned tables;
   - install `USING` and `WITH CHECK` policies only after parent FKs/typed checks are validated and before cutover;
   - revoke API update/delete on audit, use append-only trusted writer and deny-by-default mutation trigger/equivalent reviewed boundary;
   - install trusted repository/gateway role and partition writer epoch.
5. **M06.e adapter and shadow-read enablement**
   - resolve legacy row/GrantSet through application ports;
   - write only mapping/audit/quarantine or canonical evidence until M06 readiness gate;
   - compare legacy and canonical decisions without using canonical result for authorization.

### 6.2 Canonical repository invariants

`CanonicalGrantRepository` must enforce the target DDL and the plugin-instance decision together:

- `app_id` is lookup/provenance only; definition equality is loaded server-side;
- version resolution uses `(app_definition_id, manifest_version)` and composite `(app_version_id, app_definition_id)` FK;
- `app_artifact_digest` equals immutable version artifact digest; instance-bound rows also equal instance artifact;
- scope registry resolves active typed row; plugin scope requires `scope_id = plugin_instance_id`;
- tenant/cabinet/project grants with concrete runtime calls require exact `plugin_instance_id` unless an approved bounded policy explicitly permits instance-independent use;
- `subject_kind/subject_id` is explicit and server-resolved; NULL is not a wildcard;
- methods map to operations only through a versioned approved rule; absent methods never mean wildcard;
- `eventTypes`, `maxBytesPerRequest` and supported constraints remain in canonical constraints and digest;
- expiry is read-time deny; revoke is forward-only; reissue creates a new `grant_generation` and retains evidence;
- `writer_generation` and idempotency/CAS prevent stale or duplicate mutation.

### 6.3 Adapter mapping and ports

The adapter receives a legacy row or v1 `GrantSet`, but constructs canonical data only from authenticated/resolved context:

1. resolve `tenant_id` from principal and compare legacy `company_id` as assertion;
2. resolve definition by stable `app_id` within tenant;
3. resolve version by definition plus `manifest_version`; verify immutable artifact;
4. resolve typed scope and exact instance/session/subject where required;
5. preserve `GrantSet.name` exactly as `capability_key`;
6. canonicalize sorted duplicate-free operations/resources/domains and calculate four digests;
7. preserve supported constraints; send unsupported/ambiguous/denied/incomplete entries to quarantine;
8. write mapping and audit atomically with any eligible canonical grant;
9. classify outcome as `allow`, known `deny`, `quarantine` or adapter/system failure with stable reason code.

Same source/schema/digest replay is idempotent. Same source identity with a different digest is a conflict and cannot overwrite a target row.

### 6.4 M06 RLS and positive-write gate

Grant RLS `USING`/`WITH CHECK` requires transaction-local tenant and optional cabinet/project context, active registry row, parent definition/version/instance `EXISTS`, exact artifact and lifecycle/generation compatibility. Missing or malformed GUC denies. Mapping/quarantine unresolved evidence is migration-admin/security only; tenant reads are limited to resolved tenant rows.

**M06 schema gate:** all checks, named FKs, indexes, RLS/FORCE RLS policies, privileges, append-only audit boundary and trusted writer are present and reviewed. This gate alone does not enable positive canonical writes; adapter, backfill and parity gates remain mandatory.

## 7. Backfill, shadow-read and parity

### 7.1 Resumable backfill protocol

Backfill proceeds in dependency order: owner registry -> definitions/versions -> instances -> canonical grants. Each source row is processed in a bounded transaction with:

- source kind/ID and schema version;
- source payload digest and canonical payload/binding digest when representable;
- resolved tenant/scope/definition/version/subject/instance/generations;
- status and stable reason code;
- first/last observation and resumable checkpoint;
- redacted immutable audit correlation.

Backfill must never use `ON CONFLICT DO NOTHING` before validating source identity and digest. Invalid, orphan, deleted, overlong, cross-tenant, cross-definition, duplicate-active, denied, unsupported, expired-without-provenance, missing-subject, missing-instance and stale-generation rows are quarantined. Quarantine count is visible and retryable but never counts as positive parity.

### 7.2 Shadow-read contract

During M06 shadow mode:

- legacy remains decision owner and eligible writer;
- gateway evaluates legacy decision as the answer;
- canonical adapter/repository resolves and evaluates the same request in read-only/shadow mode;
- `ShadowReadPort` records normalized decision, reason, binding and digest comparison;
- canonical result is never used to allow a request;
- adapter failure or incomplete binding is fail-closed for canonical evidence and does not broaden legacy behavior;
- audit contains tenant/scope/subject/session/instance/generations, app/version/capability/operation/resource/domain and relevant MCP correlation digests, without secrets/raw arguments.

### 7.3 Partition parity acceptance

A partition can proceed only when the bounded observation window demonstrates:

```text
zero unexplained allow/deny parity mismatches
zero unknown mappings for retained source rows
zero unresolved positive bindings
all positive target rows satisfy typed scope and parent EXISTS proof
all composite FKs and RLS policies validate
source/target digests and checkpoint are recorded
no stale runtime or writer generation is accepted
```

Known deny, expired, revoked and quarantined entries are compared by explicit reason class, not counted as positive grants. A mismatch blocks only the affected partition but prevents its cutover.

## 8. Cutover, rollback and cleanup

### 8.1 Bounded cutover sequence

1. freeze partition source changes and stop new legacy grant/revoke claims;
2. advance/fence writer generation and drain in-flight legacy workers;
3. apply final delta through the adapter with source mapping/idempotency;
4. record zero-unknown parity checkpoint and immutable cutover audit;
5. switch gateway reads/authorization and management commands to `CanonicalGrantRepository`;
6. make `dynamic_app_grants` read-only compatibility projection with mapping IDs;
7. verify exactly one eligible writer and one authorization owner;
8. leave unresolved rows quarantined and fail-closed; do not promote the global migration because one partition is incomplete.

### 8.2 Before canonical read cutover

Stop adapter/backfill writes for the affected partition, keep legacy reads/writes authoritative, and retain target rows, mappings, quarantine and audit. Repair through forward mapping/revision and rerun shadow parity. Do not delete imported rows to conceal a mismatch. Schema downgrade is allowed only before tenant data import and after dependency/row-count/export preflight.

### 8.3 After canonical read cutover

There is no dual-owner rollback. Fence the canonical writer generation and stop new issue/revoke workers for the affected partition; keep canonical reads authoritative and default incomplete reconciliation to deny. Repair with canonical commands, CAS/idempotency and forward revisions. Legacy rows remain read-only projection and are never restored as authorization authority.

If runtime evidence becomes stale, deny/quarantine the call and audit the fencing event. Do not reuse another instance or fall back to app-wide permission. Reissue creates a new grant generation and preserves prior rows/audit.

### 8.4 Cleanup boundary

Only after retention/rollback window, complete source mapping or owner-approved quarantine/retirement, zero legacy readers/writers and valid audit replay may a later cleanup revision remove the compatibility projection. Drop order is dependent readers/indexes, projection refresh, compatibility adapter, then legacy table. Registry, canonical grants, definitions, instances, mappings, quarantine and audit remain protected evidence. Cleanup is never part of M04–M06 implementation.

## 9. Explicit positive-write gates

Every canonical positive grant, published instance or runtime mutation must pass applicable gates atomically:

1. authenticated principal resolves to one retained `companies.id` and transaction-local tenant context;
2. client/plugin company, tenant, cabinet, project, subject and instance assertions match server resolution;
3. `(scope_kind, scope_id)` resolves to exactly one active typed registry row;
4. plugin scope has `scope_id = plugin_instance_id`; ordinary scope has exact installation relation when runtime policy requires it;
5. cabinet access and project `(company_id, cabinet_id)` compatibility are proven;
6. definition/version same-definition composite keys, lifecycle and immutable artifact are valid;
7. exact instance, subject/session and current `runtime_generation` are valid when required;
8. capability identity, bounded arrays/constraints, expiry/revoke and positive generations are valid;
9. source mapping idempotency and payload/binding digests are conflict-free;
10. current `writer_generation` owns the partition and stale CAS fails closed;
11. immutable audit append succeeds inside the trusted transaction boundary;
12. RLS `USING`/`WITH CHECK`, parent `EXISTS` and role privileges succeed.

Failure yields deny, quarantine or explicit system failure, never allow through legacy JSONB, requested capabilities, wildcard scope, guessed owner, alternate instance or adapter-only assertion.

## 10. Implementation acceptance checklist

Implementation PRs for M04–M06 must add focused acceptance coverage for:

- String(40) type alignment and overlength quarantine;
- all four registry shapes and impossible-shape checks;
- parent cabinet/project compatibility and active membership;
- definition/version composite identity and immutable artifact;
- `use_alter`, named follow-up FK, nullable staging and reviewed deferred-cycle behavior;
- exact instance key, artifact equality, lifecycle, subject/session and generation fencing;
- canonical grant plugin-instance scope branch, active uniqueness and idempotent conflict;
- RLS `ENABLE` + `FORCE`, `USING`/`WITH CHECK`, no-context and cross-tenant/cabinet/project denial;
- adapter resolution, method mapping, unsupported constraints and quarantine reason codes;
- shadow-read normalized parity and final-delta checkpoint;
- revoke/expiry/reissue, append-only audit and stale worker fencing;
- pre-cutover legacy rollback and post-cutover forward-only repair.

These checks are written as acceptance requirements but are not executed during the migration verification freeze.

## 11. Unresolved risks and blockers

- Current canonical grant DDL's `scope_kind` check omits `plugin_instance`; M06 implementation must revise it before positive writes.
- Existing IDs may be `String(80)` while canonical anchors are `String(40)`; conversion requires inventory/quarantine, never implicit cast.
- Current instance rows may lack tenant, exact subject/session, artifact and generation fields; M05 cannot be considered complete by adding only an adapter check.
- Registry-instance cycle can leave a service-only integrity hole if the late named FK is not validated before M06.
- Cabinet/company and project/company/cabinet compatibility may require relation proof beyond independent FKs.
- Existing cascade deletes must be constrained before importing security evidence.
- v1 GrantSet lacks exact instance ID; historical entries may remain quarantine until runtime/session resolution is available.
- Instance-independent declarative policy boundary is not fully specified; omit-instance must remain deny/quarantine until approved.
- PostgreSQL partial uniqueness, RLS policy behavior and append-only audit enforcement need implementation-specific review after verification freeze.
- No cutover is safe with unknown mappings, unresolved positive bindings, unexplained parity mismatch or an unfenced legacy writer.

## 12. Verification freeze

Для этой documentation-only задачи tests, build, lint, format, compile, Alembic execution, migration execution и git checks намеренно не запускались. Код, ORM, core docs, shared environments and running databases не изменялись. Backfill, positive canonical writes, cutover, cleanup and rollback drills не выполнялись. Verification remains frozen until the migration is implemented and reviewed multiple times, followed by an explicit decision to lift the freeze.
