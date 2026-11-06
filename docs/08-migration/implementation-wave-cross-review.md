# Implementation-wave cross-domain review

Статус: migration-stage static review, documentation-only. Baseline: 2026-11-01.

Охват: текущие application implementation zones `owner_anchor_registry/T01`, `plugin_instance_registry/T02`, `grant_projection/T03`, `resource_materialization/T04`, `automation_persistence/T05`, `equipment_visibility/T06`, `web_facade_adapter/T07`, `dynamic_app_plugin_gateway/T08`, а также M04/M05/M06 application adapters (`dynamic_apps_definition_adapter`, `scope_registry_adapter`, `plugin_instances_adapter`, `canonical_grants_method_adapter`, `canonical_grants_persistence_adapter`, `canonical_grants_promotion`). Проверка статическая: сопоставлены identity, outcome/quarantine semantics, positive-write boundaries и protocol wiring с migration contracts и target ORM task map.

Этот документ не разрешает DDL, backfill, canonical positive writes, runtime apply, automation ownership или cutover.

## Executive conclusion

Локальные зоны в основном fail-closed и не имеют прямого ORM-write surface. Однако cross-domain handoff пока не является durable и атомарным:

- `owner_anchor_registry` возвращает проверенный typed owner, но не выдаёт source mapping/evidence/checkpoint/correlation, который downstream может связать с T01 promotion evidence.
- `plugin_instance_registry` принимает полный composite identity и quarantine callback, но его identity не содержит installation cabinet/project anchors, source digest или evidence lineage; plugin-instance scope также остаётся несовместимым с частью M04 contract.
- `grant_projection` последовательно проверяет definition, instance и fencing, но принимает/передаёт identity через несколько независимых reads; его atomic transition port опционален, а request не несёт полного resolved M04/M05 snapshot.
- `resource_materialization` и `automation_persistence` имеют полезные локальные idempotency/CAS gates, но не требуют owner-anchor, grant/audit/promotion evidence в том же trusted transaction. Они могут быть подготовлены shadow/read-only, но не могут сами объявлять ownership.
- `equipment_visibility` доказывает tenant/project, freshness и provenance chain, но это отдельная policy boundary без typed owner registry, cabinet membership, durable quarantine outcome или promotion linkage.
- `web_facade_adapter` хорошо ограничивает authenticated user, cabinet/project/session и SSE replay, но остаётся legacy service facade: он не передаёт canonical owner/instance/generation context и не является M06 authorization boundary.
- `dynamic_app_plugin_gateway` проверяет manifest/grants/scope на open и повторно разрешает запись на invoke, но handle/context живут в памяти, runtime fencing/audit/promotion не входят в port, а quarantine evidence не содержит общей identity/digest/correlation envelope.

Следствие: canonical grant allow, published plugin/runtime transition, resource materialization apply, automation delivery ownership и gateway capability execution должны оставаться partition-denied либо shadow-only до закрытия evidence handoff и trusted transaction wiring.

## Identity handoffs

| Handoff | Что передаётся сейчас | Что теряется или остаётся неоднозначным | Required next boundary |
|---|---|---|---|
| Authenticated request -> T01 owner anchor | `OwnerKey`, server `RlsContext`, typed scope shape, parent compatibility | Нет `source_ref`, payload digest, checkpoint, mapping ID и correlation; `OwnerAnchorDecision` не отличает durable quarantine от read-time deny | Вернуть immutable `OwnerAnchorEvidence` с registry row ID, source/canonical digest, parent proof ID, checkpoint и correlation; сохранить read-only resolution |
| T01 -> T02 definition/version | M04 adapter resolves tenant/app/version/artifact; scope is separately resolved | Definition resolution не содержит scope registry identity, shared snapshot или durable classification; повторный resolve допускает evidence drift | Composite `DefinitionVersionEvidence` должен ссылаться на active owner evidence, same-definition key, artifact digest и source snapshot |
| T02 -> T03/T02 plugin registry | `PluginInstanceRegistryIdentity`: instance, definition, version, tenant, scope and artifact; subject/session and generations are record fields | Нет cabinet/project installation anchors, lifecycle proof lineage, source digest или audit correlation; `scope_kind=plugin_instance` only checks `scope_id == plugin_instance_id` | Canonical instance snapshot must include typed installation anchors, owner registry ID, definition/version evidence ID, artifact proof and writer/runtime evidence |
| T03 -> M06 grant projection | Request carries tenant/company, app/version, instance, scope, subject/session, capability and fencing evidence; service rechecks definition and instance | `app_definition_id`, resolved version identity, installation anchors, owner evidence and immutable snapshot digest are not request-level requirements; `correlation_id` may be empty and is synthesized late | Require a non-optional resolved context/snapshot before `ALLOW`; preserve caller claims separately as assertions for mismatch audit |
| M06 -> canonical projection | `GrantProjectionCommand` and optional `AtomicGrantProjectionPort` | `projected=False` is a valid result even after allow; no typed result for quarantine append/audit failure; transition contract does not require promotion partition or writer epoch | Atomic repository command must include full identity, gate evidence, expected writer generation, audit envelope and explicit `allow/deny/quarantine/conflict/stale/system-failure` result |
| T01/T02/T03/M06 -> T04 resources | Resource selection has explicit resource/binding/version/target; resolver checks published active version and target path | Resource source port performs separate reads; no owner/grant snapshot, source digest, CAS revision token or audit/promotion evidence; `version_number` is local selection, not durable exact FK proof | Add read snapshot or repository transaction keyed by project/revision/generation; materialization apply must consume promotion/gate evidence and persist provenance atomically |
| T01/M05 -> T05 automation | Envelope has tenant, subscription, source event, request hash and idempotency key; task/delivery/outbox leases use CAS-shaped contracts | No typed scope/owner anchor, project/run/approval binding, grant/runtime generation or promotion owner; `ProjectTriggersPort` is empty and separate legacy queue is unresolved | Resolve tenant/scope/project/run parent before intent; commit business mutation + outbox intent + audit in one trusted UoW; make legacy trigger fence explicit |
| Auth/web -> T07 facade | `WebRequestContext`, scope enum, cabinet/project/session checks, typed DTOs and replay cursor | No canonical tenant/owner anchor evidence, runtime generation, capability context or correlation handoff; legacy service remains source of truth | Add a server-resolved request context bridge; facade may read/project but cannot authorize or mutate canonical state directly |
| M04/M05/M06 -> T08 gateway | Gateway checks definition/version/instance/grants and exact method constraints; invoke re-resolves current record | In-memory opaque handle is not durable identity; no artifact digest, subject/session/generation/lease in `PluginContext`; repository port lacks explicit fence/audit/promotion | Resolve through canonical M04/M05/M06 snapshot, bind handle to evidence ID/expiry, enforce runtime fence and append full audit before capability execution |

## Quarantine propagation

### Positive properties

- T01 owner resolution denies missing RLS context, context mismatch, duplicate rows, archived/quarantined owner state and incompatible parent proof.
- T02 plugin registry quarantines malformed snapshots and identity/subject/session/generation mismatches when a quarantine port is supplied.
- T03 grant projection quarantines legacy/unsupported/wildcard source, quarantined definition and quarantined canonical grant outcomes; it never treats requested capability as sufficient.
- T04 resource resolution records typed failures and removes **all** operations when a required selection fails, preventing partial materialization planning.
- T05 automation contracts keep idempotency/hash conflicts and stale lease outcomes visible instead of converting them to success.
- T06 equipment policy rejects missing/stale/mismatched snapshot, supplier, candidate-group and provenance evidence before selection/materialization.
- T08 gateway quarantines repository, scope, quota and executor failures when the quarantine port exists and never returns a handle on a negative open.

### Propagation gaps

1. **No common evidence envelope.** Quarantine records use different shapes (`OwnerAnchorReason`, registry reason strings, `ResolutionFailure`, `QuarantineEvidence`, automation conflict, plain `ValueError`). There is no required tenant/scope/source ID, canonical digest, parent evidence ID, correlation, checkpoint and observed generation common to all zones.
2. **Optional persistence hooks weaken durability.** Plugin quarantine, gateway quarantine and grant projection transition are optional. A negative decision can therefore be correct in memory while no durable quarantine/audit row exists for promotion or replay.
3. **Read failures are not equivalent to quarantined source rows.** Definition adapter malformed ORM mapping returns an in-memory negative resolution; owner resolution returns deny without mapping evidence. Downstream consumers cannot distinguish already-classified source data from an unclassified read failure.
4. **Downstream re-resolution can drift.** Grant projection resolves definition, instance and grant/fence through separate ports. Gateway re-resolves on invoke, and resource materialization separately reads resource, bindings and version. There is no shared snapshot ID proving all reads saw the same state.
5. **Exception-to-quarantine conversion is incomplete.** Equipment visibility raises `ValueError`; web facade raises scope violations; resource resolution returns failures; automation returns typed conflicts. Callers need a policy-specific adapter to persist these as quarantine, but no cross-zone port requires one.
6. **Quarantine does not automatically block the partition.** A quarantined plugin/definition/resource/automation source is not connected to the partition promotion evaluator. Positive gates must consume unresolved-quarantine counts, not only local decision outcomes.

Required rule: quarantine is durable, redacted, idempotent by source identity + schema + payload digest, linked to the same correlation/evidence snapshot, and always denies the affected partition until explicitly resolved by a forward append-only record.

## Positive-write gates

The current implementation supports the following local gates, but none is sufficient alone for a positive owner transition:

| Operation | Local checks observed | Missing gate before positive write |
|---|---|---|
| Owner registry active resolution | RLS context, exact typed shape, unique row, parent/lifecycle proof | T01 source classification, durable mapping/checkpoint, FK/check validation, RLS policy evidence and promotion partition |
| Plugin publish/runtime transition | Exact composite lookup, artifact, subject/session and generation comparison in read adapter | Atomic CAS carrying full composite identity, reciprocal FK validation, audit append, owner evidence and promotion record |
| Canonical grant projection | Canonical source/schema, no wildcard, deterministic definition/artifact, exact instance/scope, fencing and grant decision | Non-optional atomic transition; full identity snapshot; trusted RLS/WITH CHECK; audit failure rollback; writer epoch and promoted partition |
| Resource materialization apply | Explicit binding/version/target, published active version, path normalization, all-required-failure barrier | Owner/grant admission, revision CAS, exact resource-version FK snapshot, audit/provenance append and promotion gate |
| Automation intent/outbox | Canonical request hash, tenant-scoped idempotency, same event/state/enqueue identity, lease expiry validation | Scope/project/run/approval parent proof, business mutation + intent atomicity, audit, writer/worker fence and T09/T10 promotion |
| Equipment selection/materialization | Tenant/project, source chain, freshness, supplier and candidate membership checks | Typed owner/cabinet membership evidence, durable snapshot digest/quarantine, approval/transition transaction and promotion linkage |
| Web facade read/run | Authenticated user, cabinet/project/session boundary, replay cursor monotonicity and typed DTO validation | Canonical context bridge, capability/generation admission for mutations, durable correlation and explicit legacy ownership/fence decision |
| Dynamic plugin invoke | Opaque handle, current record match, exact method/capability/constraints, scope/quota/executor checks | Durable handle binding, runtime/lease CAS, full audit, canonical grant projection and promotion owner; no execution after stale generation |

**Global gate decision:** no inspected path proves `canonical_positive_writes=true` or equivalent ownership. The existing M04/M05/M06 review findings remain active: mutable promotion identity (F-01), plugin scope mismatch (F-02), narrow CAS key (F-03), non-durable definition classification (F-04), un-gated scope writes (F-05), incomplete method handoff (F-06), non-atomic evidence persistence (F-07), evaluator replacement of failed evidence (F-08), and incomplete promotion partition identity (F-09).

## Ports and wiring compatibility

### Compatible seams

- All reviewed zones are application-level and largely persistence-neutral, so they can be wired behind repositories without changing domain models.
- T01 `OwnerAnchorReader`/`ParentCompatibilityReader`/`RlsContextReader` is compatible with the target `ScopeRegistryRepository` read contract, provided the repository exposes duplicate/conflict evidence rather than collapsing it.
- T02 registry `read_exact(identity)` matches the target exact composite instance lookup and can remain read-only during shadow phase.
- T03 `AtomicGrantProjectionPort` is the correct conceptual seam for a trusted repository-owned transition; it must become mandatory for any allow that claims projection.
- T04 resource resolver can feed the existing materialization adapter only after resolution is complete; its required-failure barrier is compatible with partial-apply prohibition.
- T05 automation port already expresses separate task, delivery and outbox CAS outcomes and can map to target repositories without treating legacy `project_triggers` as target ownership.
- T07 facade DTO projection and SSE event normalization are compatible with a compatibility read adapter, not with canonical authorization ownership.
- T08 gateway's executor/quota/scope ports are composable with M06 method mapping, but the repository contract must be upgraded to return the same resolved identity/evidence used by grant admission.

### Wiring incompatibilities and blockers

- No shared `ResolvedMigrationContext`/evidence protocol exists across T01-T08. Each zone uses its own record and reason vocabulary.
- M04 adapters and T01/T02 interfaces expose separate resolution calls. No composition root currently guarantees one transaction-scoped UoW, RLS GUC setup, correlation propagation and snapshot reuse across the chain.
- T03 grant projection imports legacy `plugin_instances` contracts/ports, while T02 `plugin_instance_registry` has a distinct identity/record contract. An adapter mapping exists conceptually, but no type-level proof requires installation anchors, source digest or common evidence ID.
- M06 persistence adapters still expose independent promotion/quarantine/audit append calls, contrary to the target full-evidence transaction boundary.
- T04 materialization ports know only `ContentResource`, `ResourceVersion`, `ResourceBinding` and target scope. They do not consume canonical owner/grant/promotion context or expose CAS writer generation.
- T05 aliases multiple repository responsibilities to one protocol, which is convenient for wiring but hides whether transaction scope, tenant RLS and source/target ownership are actually enforced. `ProjectTriggersPort` has no operations, so the legacy queue fence cannot be represented.
- T06 equipment visibility uses `tenant_id/project_id` only, while T01 canonical owner proof requires typed scope and cabinet compatibility. The zone cannot independently satisfy M04 owner handoff.
- T07 legacy services accept `WebRequestContext`, but no port accepts the canonical resolved tenant/scope/instance context. This is appropriate for compatibility reads, unsafe for positive mutations.
- T08 keeps `_contexts` in process memory; multi-worker restart, replay, or failover loses handle state. The repository port does not require artifact, lifecycle generation, lease or audit evidence in `resolve`.

## Unresolved blockers

1. **Canonical identity envelope is unresolved.** Decide and implement one immutable evidence shape carrying tenant, company, cabinet/project/plugin anchors, definition/version, artifact, subject/session, runtime/writer generation, lease, source/canonical digests, checkpoint, correlation and parent evidence IDs.
2. **Plugin installation scope is unresolved.** Align `OwnerKey`, M05 registry identity, M06 grant scope and target composite FKs so cabinet/project anchors are retained and equality is enforceable. Keep plugin-instance positive paths quarantined until then.
3. **Trusted transaction composition is unresolved.** The dependency graph must guarantee: authenticated context -> owner/scope -> definition/version -> instance/fence -> grant/resource/automation operation -> full audit -> promotion evidence -> commit. Separate reads and optional transition ports cannot be treated as this boundary.
4. **Promotion lineage is unresolved.** Final review must append a new immutable record linked to the prior record; evaluators may add derived evidence but cannot replace failed source gates. The record must bind exact partition and writer generation.
5. **Durable quarantine is unresolved.** Every negative classification that affects promotion must persist source identity, digest, reason, evidence ID, checkpoint and correlation; read-time deny and durable quarantine must not be conflated.
6. **M04/M05 adapter identity is incomplete.** Definition and scope adapters lack shared snapshot evidence; plugin CAS/fencing accepts a narrow mutation key. These are upstream blockers for M06, T04, T05 and T08.
7. **Resource ownership is unresolved.** Resource polymorphic scope must resolve through the registry or an explicitly reviewed policy-only boundary; materialization must use an exact version identity and revision CAS, not independent latest reads.
8. **Automation ownership is unresolved.** T09 must distinguish task/run, event/enqueue and delivery identities, bind them to tenant/scope/run/approval parents, and separately fence `project_triggers`; no dual worker owner is allowed.
9. **Equipment provenance is not connected to migration evidence.** Tenant/project checks and source chains need typed owner proof, durable snapshot/quarantine records and a clear approval/materialization promotion handoff.
10. **Gateway execution evidence is unresolved.** In-memory handles must be bounded or replaced by durable evidence references; stale runtime generation, grant identity and audit must be checked before executor side effects.

## Next persistence tasks

The next work should remain documentation/implementation preparation until verification freeze is lifted:

1. Define a shared immutable `MigrationEvidenceContext` and outcome vocabulary (`allow`, `deny`, `quarantine`, `conflict`, `stale-generation`, `system-failure`) without making it a second domain owner.
2. Complete T01/T02 persistence foundations: owner anchor inventory, source mapping/quarantine/checkpoint tables, definition/version composite keys, typed parent compatibility and validated RLS/FORCE policies.
3. Complete T03/T04 exact instance key and reciprocal registry FKs, including installation anchors, `use_alter`/late constraints and explicit validation evidence. Keep published/runtime positive paths disabled.
4. Replace narrow M05 CAS requests with full identity + artifact + subject/session + expected writer generation predicates; emit fencing evidence atomically.
5. Make M06 projection transition and audit append one trusted transaction. Require full `AuditOutcomeEvidence` and reject direct narrow append as authoritative evidence.
6. Add promotion record lineage and immutable partition identity. A final decision must append a new record, preserve failed source gates, include all upstream evidence IDs/digests and set positive-write enablement only for the exact partition.
7. Add resource repository snapshot/CAS and exact root/version/binding FK wiring; require grant/audit/promotion admission before apply.
8. Add automation target persistence for intent/event/outbox/delivery, tenant/scope/run parent proof and lease fencing; decide and record the separate legacy `project_triggers` worker fence.
9. Connect equipment snapshot/provenance persistence to owner registry and quarantine evidence; convert policy failures into durable typed outcomes at the owning boundary.
10. Keep web facade read compatibility and gateway execution behind canonical context adapters; introduce durable correlation/evidence references before any canonical write or side-effecting execution.
11. Only after implementation evidence is complete, perform the postponed acceptance review and then an explicit decision to lift verification freeze. No current finding authorizes tests/build/lint/compile/Alembic/git checks during this review.

## Inspected files

Migration contracts and prior cross-domain decisions:

- [`docs/08-migration/adapter-cross-domain-review.md`](adapter-cross-domain-review.md)
- [`docs/08-migration/m04-m06-adapter-implementation.md`](m04-m06-adapter-implementation.md)
- [`docs/08-migration/m04-m06-readiness-matrix.md`](m04-m06-readiness-matrix.md)
- [`docs/08-migration/target-orm-schema-task-map.md`](target-orm-schema-task-map.md)
- [`docs/08-migration/persistence-implementation-phase.md`](persistence-implementation-phase.md)
- [`docs/08-migration/m06-trusted-persistence-adapter.md`](m06-trusted-persistence-adapter.md)

Implementation zones:

- `apps/api/src/prodavan/application/owner_anchor_registry/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/plugin_instance_registry/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/grant_projection/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/resource_materialization/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/automation_persistence/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/equipment_visibility/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/web_facade_adapter/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/dynamic_app_plugin_gateway/contracts.py`, `ports.py`, `service.py`

M04/M05/M06 application adapters inspected through the existing implementation and review surfaces:

- `apps/api/src/prodavan/application/dynamic_apps_definition_adapter/repository.py`
- `apps/api/src/prodavan/application/dynamic_apps_definition/service.py`
- `apps/api/src/prodavan/application/scope_registry_adapter/service_adapter.py`
- `apps/api/src/prodavan/application/scope_registry/service.py`
- `apps/api/src/prodavan/application/plugin_instances_adapter/repository.py`
- `apps/api/src/prodavan/application/plugin_instances_adapter/service.py`
- `apps/api/src/prodavan/application/canonical_grants_method_adapter/contracts.py`, `service.py`
- `apps/api/src/prodavan/application/canonical_grants_persistence_adapter/adapter.py`, `ports.py`
- `apps/api/src/prodavan/application/canonical_grants_promotion/contracts.py`, `ports.py`, `service.py`
- `apps/api/src/prodavan/application/canonical_grants/service.py`

## Verification freeze

Tests, build, lint, format, compile, Alembic/migration execution, environment changes and all git checks were intentionally not run. No code, ORM, core documentation, shared environment or database was changed. The only changed file is this new review document; findings and persistence tasks remain subject to implementation and a later explicit verification decision.
