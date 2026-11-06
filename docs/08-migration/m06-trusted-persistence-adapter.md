# M06 trusted persistence adapter implementation contract

Статус: migration-stage implementation contract, только документация. Baseline: 2026-11-01.

Документ задаёт исполняемый контракт для trusted M06 persistence adapter: repositories, transaction boundary, tenant RLS, canonical grants, append-only audit, quarantine и bounded cutover. Он опирается на [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md), [`m04-m06-adapter-implementation.md`](m04-m06-adapter-implementation.md), [`m04-m06-readiness-matrix.md`](m04-m06-readiness-matrix.md), [`dynamic-grants-binding-gaps.md`](dynamic-grants-binding-gaps.md), [`dynamic-grants-ownership.md`](dynamic-grants-ownership.md), [`dynamic-app-migration-gaps.md`](dynamic-app-migration-gaps.md) и текущий [`capability_gateway_service.py`](../../apps/api/src/prodavan/application/capability_gateway_service.py). Документ не является ORM/Alembic implementation и не разрешает shared-environment DDL, positive writes или cutover.

## 1. Fixed ownership and identity decisions

`dynamic_app_capability_grants` является единственным canonical authorization owner после bounded partition cutover. `dynamic_app_grants` остаётся read-only compatibility projection; два policy owner одновременно запрещены. Manifest `requestedCapabilities` и v1 `GrantSet` описывают request/provenance, но не являются allow.

`companies.id` — canonical tenant identity. В application ports он называется `tenant_id`, но physical type и FK должны быть выровнены с `companies.id` (target inventory ожидает `String(40)`). Копия tenant/scope в дочерней строке никогда не заменяет parent proof.

Каждая positive authorization row должна сохранять и проверять полный effective identity:

```text
companies.id / tenant_id
app_definition_id + app_id
app_version_id + manifest_version
immutable app_artifact_digest
scope_kind + scope_id + cabinet_id? + project_id?
subject_kind + subject_id
plugin_instance_id? + session_id?
runtime_generation? + writer_generation
lease_id? / lease owner when runtime admission requires it
capability_key + operation/resource/domain constraints
```

`app_id`, `manifest_version`, `company_id`, subject, scope and instance supplied by a client or plugin are assertions only. Adapter resolves authoritative values from authenticated context and parent tables, then compares assertions. A missing, ambiguous or conflicting identity denies or quarantines; it never widens scope.

### 1.1 Exact identity proof

- `tenant_id` must equal the resolved `companies.id`; the definition owner and all parent anchors must agree.
- Definition is resolved by `(tenant_id, app_id)`. Version is resolved only by `(app_definition_id, manifest_version)`; a version ID alone or a manifest version alone is insufficient.
- The grant stores `(app_version_id, app_definition_id)` and uses a composite FK to the version key. `app_artifact_digest` must equal the immutable version digest; an instance-bound call must also equal the instance artifact digest.
- `scope_kind/scope_id` resolves exactly one active typed scope registry row. `tenant`, `cabinet`, `project` and approved `plugin_instance` shapes are explicit; a polymorphic `scope_id` is not a generic FK.
- `subject_kind/subject_id` is explicit and non-null. A null/empty subject is not a wildcard. Instance-independent policy requires a separately approved bounded sentinel/path.
- A plugin/runtime call requires exact `plugin_instance_id`, exact definition/version/tenant/scope relation, current runtime state, `runtime_generation`, authenticated `session_id`, and lease/fencing evidence where runtime admission defines a lease. `session_id` never substitutes for subject or instance.
- `grant_generation` identifies a forward reissue; `writer_generation` fences stale workers. A stale writer or runtime lease produces zero-row mutation/deny plus audit, never fallback to another instance or app-wide grant.

## 2. Ports and repository ownership

The gateway remains the compatibility boundary. It must call resolution ports before the trusted repository and must not authorize from legacy JSONB or requested capabilities.

```text
ResolveTenantPort.resolve(principal, request_context)
  -> tenant_id, user_id, role, evidence_digest
ResolveScopePort.resolve(tenant_id, scope_kind, scope_id, context)
  -> active typed scope or failure
DefinitionVersionRepository.resolve_definition(tenant_id, app_id)
DefinitionVersionRepository.resolve_version(app_definition_id, manifest_version)
PluginInstanceRepository.resolve_exact(tenant, definition, version, scope, subject, session, presented)
RuntimeFenceRepository.current_and_compare_advance(partition, expected_writer_generation)
GrantSetAdapterPort.adapt(source, resolved_context)
  -> AllowCandidate | KnownDeny | QuarantineCandidate | AdapterFailure
CanonicalGrantRepository.issue/authorize/revoke/reissue(command, epoch)
GrantMappingRepository.record_or_compare(source_identity, digests, outcome)
GrantQuarantineRepository.open/count/retry(source_identity, reason)
CapabilityAuditRepository.append(event)
ShadowReadPort.compare(legacy, canonical, comparison_context)
```

Repository boundaries are strict:

| Repository | Owned responsibility | Forbidden shortcut |
|---|---|---|
| `DefinitionVersionRepository` | tenant-scoped definition/version lookup and artifact proof | lookup by version string/id without same-definition proof |
| `ScopeRegistryRepository` | active typed scope and cabinet/project compatibility | deriving ownership from copied IDs or JSONB |
| `PluginInstanceRepository` | exact instance, subject/session, artifact and lifecycle | fallback to another instance or app-level grant |
| `RuntimeFenceRepository` | CAS for runtime/writer generation and lease ownership | stale-worker update or `SELECT` then mutate |
| `CanonicalGrantRepository` | issue, authorize, revoke, reissue with full identity | direct ORM insert or legacy row authorization |
| `GrantMappingRepository` | source/schema/digest idempotency and conflict detection | `ON CONFLICT DO NOTHING` before digest comparison |
| `GrantQuarantineRepository` | visible retryable fail-closed evidence | making quarantine queryable by authorization |
| `CapabilityAuditRepository` | append-only immutable security evidence | update/delete from API role or audit failure -> allow |
| `LegacyGrantProjectionRepository` | compatibility projection and parity reads only | post-cutover policy decisions or writes |

No repository method may expose a positive grant insert that bypasses the trusted transaction. ORM sessions are implementation details and are not a security boundary by themselves.

## 3. Trusted transaction boundary

A positive issue, revoke/reissue, instance lifecycle transition or runtime admission mutation runs in one trusted transaction. The transaction must:

1. Start with a server-created authenticated context and set transaction-local `app.tenant_id` to the resolved `companies.id`; set optional `app.cabinet_id`, `app.project_id`, `app.user_id`, `app.role`, request/trace correlation and writer epoch. Never copy these GUCs from request JSON.
2. Lock or CAS the partition/instance writer epoch and, where applicable, validate `runtime_generation` and `lease_id`/lease owner. A stale epoch must affect zero rows.
3. Resolve and verify the active typed scope. Parent `EXISTS` checks prove company/cabinet/project compatibility and active membership; denormalized columns are compared to anchors.
4. Resolve definition and version within tenant, verify composite same-definition identity, immutable artifact and lifecycle.
5. Resolve exact subject, session and plugin instance. Verify instance composite key, scope, subject/session, artifact, lifecycle and runtime generation whenever policy requires runtime binding.
6. Parse and canonicalize the capability payload, apply the approved mapping version, compute payload/binding/operations/resources/domains/constraints digests, and reject unsupported or broadening semantics.
7. Compare mapping identity and source digest. Same source/schema/digest is idempotent; same source identity with a changed digest is a conflict/quarantine. Enforce active full-tuple uniqueness and generation CAS atomically.
8. Insert/update the canonical row only for an eligible positive candidate, and append the audit event in the same commit boundary. Revoke is forward-only; renewal/reissue creates a new generation and retains old evidence.
9. Commit only after RLS `USING`/`WITH CHECK`, parent checks, role privileges and audit append succeed. The audit append is part of the same trusted transaction: an audit failure rolls back the grant/mapping/lifecycle mutation and returns deny/system failure, never allow.

Read authorization uses the same resolved context and RLS transaction settings. Expiry is evaluated at read time (`now >= expires_at` denies); expired history is not deleted and expiry must not be put in a moving-clock partial-index predicate.

## 4. Capability mapping contract

The adapter preserves each v1 `GrantSet.name` exactly as `capability_key`; different names never merge. `decision=denied` is a known deny/provenance outcome, not a positive row. `requestedCapabilities` only proves that the app requested a capability and never proves an allow.

`methods -> operations` is a versioned, closed mapping registry, for example `(mapping_name, mapping_version, protocol_version, method, operation, semantics_digest)`. The adapter records `mapping_version` in `schema_version`/provenance and audit. It may map only methods explicitly present in the approved registry; absent `methods` is not wildcard. Unknown, duplicate, ambiguous or deprecated method mappings quarantine. Mapping changes create a new version; existing canonical grants are not silently rewritten.

Canonical payload rules:

- `operations`, `resources` and `domains` are sorted, duplicate-free, non-empty JSON arrays;
- `constraints` is a canonical object preserving `eventTypes`, `maxBytesPerRequest` and every field that the gateway enforces;
- all four digests use canonical serialization and exclude diagnostics/raw secrets;
- wildcard or unbounded resource/operation semantics are unsupported unless a separate bounded policy explicitly defines them;
- an unsupported constraint, missing binding or denied decision returns quarantine/known deny and can never become positive authorization.

Gateway invocation has two audit stages: pre-invocation authorization and final adapter outcome. An allow record is not an execution-success claim: a successful adapter return must emit a linked final success/result event with status and duration, while missing adapter, invocation exception or malformed result emits a linked final deny/error event with the same request/trace/span correlation. The current `CapabilityGatewayService` records the pre-invocation decision and only failure-side final event, so durable MCP-complete final-outcome coverage remains an implementation gap.

## 5. Append-only audit contract

`CapabilityAuditRepository` persists every authorization attempt and lifecycle/import event: allow, deny, issue, import, revoke, expiry observation, adapter failure, quarantine, mapping conflict, stale generation/lease, parity, cutover and rollback. The event is immutable and tenant-scoped.

Required envelope fields are:

```text
occurred_at, event_id, event_type, decision, reason_code
tenant_id, cabinet_id?, project_id?, user_id?/subject_id?, session_id?
app_definition_id, app_id, app_version_id, manifest_version?
app_artifact_digest?, plugin_instance_id?, runtime_generation?, writer_generation?, lease_id?
capability_grant_id?, source_kind?, source_id?, mapping/schema_version?
request_id, trace_id, span_id
profile_id, tool_server, tool_name, tool_version
arguments_digest (canonical redacted digest; never raw arguments)
operation, resource, domain, payload_digest
result_status, error_code, duration_ms
redaction, metadata (redacted only)
```

For MCP, `profile_id`, `tool_server`, `tool_name`, `tool_version`, `span_id`, canonical `arguments_digest`, result status, error code and duration are correlation/provenance fields. MCP tool identity is never converted into `capability_key`; the declarative capability and MCP envelope remain separate linked identities. When the call is backed by the runtime persistence map, the immutable capability-bundle snapshot must also retain the project/profile/server/package identity, attachment id, package name/version, qualified package digest and normalized allowlist; the audit event links to that snapshot/digest rather than treating the MCP tool name as authorization. The current gateway's `company_id`/request-only audit must be enriched by the adapter or linked deterministically to this durable envelope.

Database role policy must revoke application/API `UPDATE` and `DELETE` on audit. A deny-by-default trigger or equivalent reviewed writer boundary rejects mutation, including attempts to alter tenant or historical digest. Optional target FKs use `ON DELETE SET NULL` while immutable source IDs/digests remain in the event; security history must survive grant cleanup.

## 6. Quarantine and mapping persistence

Every source row receives a mapping outcome, including known deny and quarantine. Mapping is unique by `(source_kind, source_id, canonical_schema_version)` and stores source/canonical payload digests, status, reason and checkpoint. A changed source digest is a conflict, never an overwrite.

Quarantine is a durable, visible, counted and retryable table with source identity, schema version, reason code, payload digest, partial resolved tenant/definition/version/scope/subject identities, checkpoint and redacted metadata. Mandatory quarantine cases include:

- orphan, ambiguous or cross-tenant/cross-definition parent resolution;
- malformed/overlength IDs, invalid lifecycle/timestamps or duplicate active identity;
- missing subject, session, instance, runtime generation or lease when required;
- artifact mismatch, unsupported capability/constraint or unknown mapping version;
- `decision=denied`, expired/revoked provenance without a provable actor, or any broadening transformation.

Quarantine is never read by authorization and never counts as positive parity. Unresolved rows are migration-admin/security-only; tenant users may see only rows resolved to their tenant. Retry must use the same source identity and compare the digest again. Repair is a forward mapping, not an overwrite or deletion.

## 7. RLS, FORCE RLS and privileges

All four target tables (`dynamic_app_capability_grants`, audit, mappings and quarantine) enable and force row-level security. New typed scope/instance evidence tables use the same boundary. RLS policies are named and included in rollback inventory.

Normative policy shape:

- grants `USING` and `WITH CHECK` require `tenant_id = current_setting('app.tenant_id', true)`, valid optional cabinet/project context, active scope, and parent `EXISTS` checks for company, definition/version, subject/session and instance;
- grant writes additionally require trusted writer role, current writer generation, exact artifact/binding identity and no revoked/quarantined parent;
- audit reads are tenant-scoped; inserts are only through the trusted audit writer; API tenant roles cannot bypass RLS or mutate history;
- mapping/quarantine writes are limited to migration/security/audit roles; tenant reads expose only resolved same-tenant evidence;
- absent, empty, malformed or contradictory GUC context denies by default; no policy uses a client-supplied fallback;
- `FORCE ROW LEVEL SECURITY` must also apply to table owners where supported by the deployment role model; privileged bypass roles are explicit and limited to migration/security operations.

Policies are installed after anchor/FK preparation and before shadow/cutover. Parent checks remain required even when denormalized tenant IDs appear equal, because equality alone does not prove ownership.

## 8. Staged DDL and data protocol

Implementation follows one reviewed linear Alembic chain, with models as source of truth. The logical order is:

1. align `companies.id` tenant type and owner anchors; establish scope registry and cabinet/project compatibility evidence. The registry must support all four typed shapes (`tenant`, `cabinet`, `project`, `plugin_instance`); the `plugin_instance` branch remains deferred until the M05 exact instance key/reciprocal relation is validated;
2. validate definition owner `(id, tenant_id)`, version key `(id, app_definition_id)`, manifest lookup and immutable artifact; stage cyclic FKs with `use_alter`/named follow-up constraints;
3. establish exact plugin-instance composite key, runtime/lease/writer generations and reciprocal registry relation;
4. create mappings, quarantine and append-only audit before any canonical import;
5. create canonical grants with composite version FK, tenant/definition proof, exact instance relation, capability key, subject/session, scope branch, payload digests, lifecycle and generation checks;
6. add indexes/active uniqueness, privileges and `ENABLE/FORCE RLS` with both `USING` and `WITH CHECK`; revise the intermediate three-shape grant check to the approved four-shape contract, including `plugin_instance` with `scope_id = plugin_instance_id` and installation anchors, before any instance-bound positive write;
7. add large-table constraints as `NOT VALID` where needed. Backfill/classify first, then `VALIDATE CONSTRAINT` and record named validation evidence. `NOT VALID` does not make invalid historical rows eligible and must not weaken new-write checks;
8. run resumable dependency-ordered backfill: scope -> definitions/versions -> instances -> grants. Each bounded transaction records checkpoint, source/canonical digests, resolved identity and redacted audit;
9. enable shadow reads while legacy remains decision owner. Canonical results are comparison evidence only;
10. perform final delta, parity and fencing checks before partition cutover.

`NOT VALID` constraints are tightened only after orphan/cross-scope rows are quarantined. Positive canonical writes remain disabled until all required constraints are validated or a reviewed new-write equivalent is present, and the positive-write gate below is satisfied.

## 9. Parity, cutover and rollback

A partition is `(tenant_id, app_definition_id)`, optionally narrowed by typed scope or exact instance. Promotion requires:

```text
zero unknown mappings for retained source rows
zero unresolved positive bindings
zero unexplained legacy/canonical allow-deny mismatches
all positive rows pass parent EXISTS, artifact and RLS/FK proof
all required NOT VALID constraints validated
mapping and source/canonical digests plus final checkpoint recorded
no stale runtime, lease or writer generation accepted
legacy writer drained and exactly one eligible writer remains
```

Known deny, expiry, revoke and approved quarantine are compared by explicit reason class, not counted as positive grants. One incomplete partition blocks only that partition and cannot be globally promoted.

Bounded cutover freezes new legacy changes, advances/fences the writer generation, drains in-flight workers, applies final delta idempotently, records immutable cutover audit, switches gateway reads/management commands to the canonical repository and makes `dynamic_app_grants` read-only projection. There is never a dual authorization owner.

Before canonical read cutover, stop adapter/backfill writes for the affected partition, keep legacy reads/writes authoritative, preserve all target evidence and repair forward. After canonical read cutover there is no legacy rollback authority: fence canonical workers, keep canonical reads authoritative and fail closed on incomplete reconciliation; repair with canonical CAS/idempotency commands and forward revisions. Schema downgrade is permitted only before tenant data import and after dependency/export preflight. No rollback deletes imported grants, audit, mappings or quarantine.

## 10. Positive-write gates

Every canonical positive grant, reissue, revoke, published instance or runtime mutation must pass atomically:

1. authenticated principal resolves exactly one retained `companies.id`, and transaction-local tenant context is set from it;
2. request/plugin `company_id`, tenant, cabinet, project, subject and instance assertions match server resolution;
3. exactly one active typed scope row and all cabinet/project membership/parent compatibility proofs exist;
4. definition/version same-definition keys, lifecycle and immutable version/instance artifact equality pass;
5. exact `subject_kind/subject_id`, session, plugin instance and current `runtime_generation`/lease are present whenever policy requires them;
6. capability key is preserved, requested capability is only corroboration, and method mapping version is approved and bounded;
7. canonical arrays/constraints are non-empty, sorted, duplicate-free, digest-consistent and do not broaden permission;
8. source mapping idempotency and source/binding payload digests are conflict-free;
9. `grant_generation` and current `writer_generation` CAS/active uniqueness pass;
10. RLS `USING`/`WITH CHECK`, parent `EXISTS`, named FK/check constraints and trusted role privileges pass;
11. append-only audit append succeeds with trace/span/profile/MCP digest/result/error/duration fields where applicable;
12. legacy writer is fenced for the cutover partition and the final parity checkpoint is zero-unknown.

Failure is `deny`, `quarantine` or explicit system failure. It is never an allow through requested capabilities, legacy JSONB, copied tenant, guessed version, wildcard absence, alternate instance, stale lease/generation or adapter-only assertion.

## 11. Implementation acceptance inventory

The implementation PR must provide focused acceptance coverage for:

- `companies.id`/tenant type alignment, overlength quarantine and no-context/cross-tenant RLS denial;
- composite version-definition and instance identity, artifact equality, typed scope shape and parent compatibility;
- `USING` plus `WITH CHECK`, `ENABLE` plus `FORCE RLS`, role privilege boundaries and append-only audit mutation denial;
- method mapping versions, absent-method semantics, unsupported constraints, denied entries and quarantine reason stability;
- source digest conflicts, idempotent replay, active uniqueness, generations, lease fencing, expiry/revoke/reissue;
- complete audit envelope including MCP trace/span/profile/tool/argument digest/result/error/duration;
- `NOT VALID` -> backfill -> validate evidence, shadow parity, final delta and one-owner cutover;
- pre-cutover legacy rollback and post-cutover forward-only repair.

These are acceptance requirements, not executed checks during the current migration verification freeze.

## 12. Unresolved risks and verification freeze

Known blockers remain: current instance rows may not expose all exact identity/generation/lease fields; v1 `GrantSet` lacks `plugin_instance_id`; the approved instance-independent policy is not complete; existing model IDs may exceed `String(40)`; current gateway audit is not yet durable MCP-complete; PostgreSQL RLS/partial uniqueness/append-only trigger behavior requires implementation review. No positive write or cutover is safe while any blocker, unknown mapping, unresolved binding, parity mismatch or unfenced writer remains.

For this documentation-only change, tests, build, lint, format, compile, Alembic execution, migration execution and git checks were intentionally not run. Code, ORM, core docs, shared environments and databases were not changed. Verification remains frozen until migration implementation is complete and explicitly approved for verification.
