# Typed owner/scope registry и canonical grants: implementation plan

Статус: migration-stage implementation plan, только документация. Baseline: 2026-11-01.

Этот план переводит согласованные контракты typed owner/scope registry и `dynamic_app_capability_grants` в безопасную последовательность Alembic и application steps. Он следует решениям из [`typed-scope-owner-registry.md`](typed-scope-owner-registry.md), [`canonical-tenant-scope-registry.md`](canonical-tenant-scope-registry.md), [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md), [`dynamic-grants-binding-gaps.md`](dynamic-grants-binding-gaps.md), [`dynamic-grants-ownership.md`](dynamic-grants-ownership.md) и [`persistence-target-map.md`](persistence-target-map.md).

План не является Alembic revision, не изменяет ORM, gateway, adapter или core docs и не разрешает shared-environment DDL. Исполняемый DDL должен быть выведен из SQLAlchemy-моделей и новых линейных ревизий; backfill и импорт выполняются отдельными идемпотентными jobs/commands, а не скрытым autogenerate.

## 1. Целевой результат и инварианты

К концу этой миграционной линии должны быть доказаны следующие свойства:

- canonical tenant key — `companies.id`, логическое имя в новых API/RLS — `tenant_id`, физический тип и длина — `String(40)`;
- `(scope_kind, scope_id)` разрешается только через одну активную typed row registry и никогда не считается polymorphic FK;
- scope kinds ограничены `tenant`, `cabinet`, `project`, `plugin_instance`; registry row содержит tenant и необходимые cabinet/project/instance anchors;
- dynamic-app definition/version/instance/grant связи доказывают одного tenant, одну definition и одну immutable version;
- instance-bound grant доказывает точный plugin instance, artifact, subject/session и runtime generation/fencing;
- `dynamic_app_capability_grants` — единственный authorization owner после partition cutover; `dynamic_app_grants` остаётся read-only compatibility projection на rollback window;
- archived/quarantined owners и grants читаются только migration/security/audit ролями и никогда не проходят positive authorization;
- все новые tenant-owned tables имеют RLS `ENABLE` + `FORCE`, `USING` и `WITH CHECK`, а gateway дополнительно выполняет exact identity/lifecycle checks;
- ни один положительный grant, instance, resource binding или иной tenant-owned write не разрешён до прохождения всех gates ниже.

## 2. Общие правила выполнения

1. Зафиксировать актуальный Alembic head и зарегистрированные модели в implementation PR; новые revisions делать линейной цепочкой, согласованной с этим head.
2. Каждый schema step сначала добавить в модели, затем генерировать/проверять Alembic revision согласно `docs/07-infrastructure/alembic.md`. Ручное редактирование generated DDL допустимо только для data-preserving transfer/backfill и должно быть отдельно ревьюировано.
3. Не менять уже применённые revisions. Длинные проверки и backfill вынести из transactional DDL в resumable migration job с checkpoint, source/target digest и idempotency key.
4. На каждом этапе сначала создать новые объекты и доказательства, затем переключать application reader/writer. Не удалять legacy rows для «исправления» расхождения.
5. До canonical cutover legacy остаётся decision owner. После cutover допускается только один authorization owner и один eligible writer на partition.
6. `company_id`, tenant/cabinet/project/plugin claims из request, payload или plugin — только assertion/provenance. Authority всегда выводится server-side из authenticated context и registry.

## 3. Revision chain и безопасная последовательность

Нумерация `M00–M09` ниже является локальным cross-domain execution order этого плана. Она намеренно не совпадает один-к-одному с gate labels в `typed-scope-owner-registry.md` (`M00–M06`), logical Alembic order в `dynamic-app-migration-gaps.md` (`M00–M08`) или target-map order в `persistence-target-map.md` (`M00–M11`). При реализации это одна линейная Alembic chain с актуальным repository head; соответствие определяется содержанием шага и gate evidence, а не совпадением номера.

### M00: inventory, freeze и migration control plane

**Alembic/application scope:** отдельный inventory/control-plane step без positive authorization.

- Freeze protocol/schema versions, capability mapping rules, lifecycle/revoke semantics и source snapshot.
- Инвентаризировать `companies`, memberships, `cabinet_instances`, `cabinet_company_grants`, `projects`, definitions, versions, legacy/canonical grants, instances, sessions, subjects, runtime bindings и всех producers `tenant_id/company_id`.
- Посчитать missing, overlength, malformed, orphan, duplicate, deleted, cross-tenant/cross-cabinet, conflicting digest, unsupported constraint и missing runtime-binding rows по tenant/app/version/scope.
- Создать checkpoint/control metadata, source snapshot identifier и redacted audit/quarantine/mapping storage plan. Не делать положительных импортов и не принимать новые canonical writes.
- Выпускать partition к следующему шагу только при deterministically repeatable inventory; ambiguous values не исправлять угадыванием по имени, slug или UI state.

**Gate M00:** каждый candidate tenant/scope ID классифицирован как valid, retained/archived или quarantine; доказано, что значения, пригодные для `companies.id`, укладываются в `String(40)`.

### M01: physical type alignment и owner-anchor preparation

**Alembic revision:** подготовить базовые типы/unique keys и deletion boundary без registry positive backfill.

- Выровнять все будущие canonical `tenant_id` и связанные dynamic-app owner columns с `companies.id` (`String(40)`). До `ALTER TYPE` выполнить отдельную проверку длины/формата и поместить overlong/invalid values в quarantine.
- Для `dynamic_app_definitions.tenant_id` выполнить data-preserving conversion только после inventory; добавить FK к `companies.id` с archive/restrict semantics и уникальный owner key `(id, tenant_id)`.
- Сохранить `company_id` как legacy/provenance там, где нужно, но не использовать его в новых FK/RLS predicates. Не выполнять implicit cast из `String(80)` в `String(40)`.
- Подготовить typed compatibility keys для project ownership. Предпочтительный вариант: уникальный parent tuple `(projects.id, projects.company_id, projects.cabinet_id)` и downstream composite references; если применён эквивалентный owner relation/consistency trigger, зафиксировать его security review и rollback inventory.
- Проверить cabinet/company membership через active `cabinet_company_grants` либо одобренную owner relation; nullable `cabinet_instances.company_id/owner_company_id` не считать доказательством.
- Установить archive-only/protected deletion policy для tenant/security evidence. Existing hard-delete cascades не должны уничтожать registry, grants, mappings, quarantine или audit.

**Alembic details:** если типы или индексы требуют lock-sensitive операций, разделить preparation и validation; constraint сначала создать `NOT VALID`, затем валидировать после quarantine/backfill. Использовать `ON DELETE RESTRICT` для owner/security evidence.

**Gate M01:** no invalid dynamic-app tenant values outside quarantine; definition owner key and project/cabinet compatibility proof are available; no positive definition/grant/instance write while required parent partition is unresolved.

### M02: empty registry, mapping, quarantine и audit tables

**Alembic revision:** создать пустые foundation tables и индексы, ещё без instance cyclic FK.

- Создать `scope_registry` с typed columns: `scope_kind`, `scope_id`, `tenant_id`, nullable `cabinet_id`, `project_id`, `plugin_instance_id`, `owner_state`, immutable source provenance, schema version и payload digest.
- Добавить unique identity `(scope_kind, scope_id)` и unique typed anchor tuple; check constraints для четырёх shapes:
  - `tenant`: `scope_id = tenant_id`, остальные anchors null;
  - `cabinet`: `scope_id = cabinet_id`, tenant non-null, project/instance null;
  - `project`: `scope_id = project_id`, tenant/cabinet/project non-null, instance null;
  - `plugin_instance`: `scope_id = plugin_instance_id`, instance equal scope, cabinet/project зеркалят installation scope.
- Добавить `owner_state IN ('active','archived','quarantined')`, при этом только active может участвовать в positive writes.
- Добавить `dynamic_app_capability_grant_mappings`, `dynamic_app_capability_grant_quarantine` и append-only capability audit tables до любого canonical import. Mapping unique key: `(source_kind, source_id, canonical_schema_version)`; conflicting source digest — conflict/quarantine, не overwrite.
- RLS/privileges для mapping/quarantine/audit установить так, чтобы migration/security role мог писать, tenant API видел только resolved tenant rows, а unresolved evidence оставалась migration-admin-only.
- На этом шаге не добавлять FK `scope_registry.plugin_instance_id -> dynamic_app_instances`, пока instance key не подготовлен.

**Gate M02:** пустые target/evidence tables доступны, checks/unique keys покрывают impossible shapes, audit/quarantine не могут стать authorization source, и rollback может удалить только новые пустые объекты.

### M03: tenant/cabinet/project registry backfill

**Application migration job:** resumable, one source anchor per idempotent transaction; schema revision не выполняет полный backfill.

- Создать tenant registry rows для retained companies: active или archived по утверждённой soft-delete/status policy. Не создавать новый tenant identity и не выводить tenant из названия.
- Создать cabinet rows только если active cabinet-company grant/approved owner relation однозначно доказывает tenant. Ambiguous/missing/deleted parent — quarantine с resolved anchors и digest.
- Создать project rows только если `projects.id`, `projects.company_id`, `projects.cabinet_id`, registry tenant/cabinet и active cabinet access согласованы. Простые отдельные FKs недостаточны.
- Записывать source identity, source payload digest, first/last observation, checkpoint и reason; повторный запуск должен вернуть ту же mapping result.
- Valid registry backfill не означает active authorization: owner_state и parent lifecycle должны быть проверены отдельно.

**Gate M03:** unique registry identity, typed shape, parent existence, project/cabinet compatibility и owner state валидированы; для partition нет unknown required parent; ещё не включать plugin-instance positive writes.

### M04: definition/version foundation и composite FK staging

**Alembic/application scope:** укрепить dynamic-app owner/version graph до grant table.

- Для каждой definition доказать `(id, tenant_id) -> dynamic_app_definitions`; cross-tenant/orphan/deleted/overlong definition quarantine.
- Для `dynamic_app_versions` обеспечить unique `(id, app_definition_id)`, immutable artifact digest и same-definition lifecycle. Версию разрешать по `(app_definition_id, manifest_version)`, никогда по одной строке version.
- Добавить/валидировать composite references `(app_version_id, app_definition_id) -> dynamic_app_versions(id, app_definition_id)` в будущих grants/instances. Existing simple version FK не считать достаточным.
- Существующий current-version FK cycle создавать staged: сначала nullable/deferred relation и valid rows, затем `use_alter=True`/отдельная post-create constraint revision после таблиц и backfill; не создавать цикл одним `CREATE TABLE` order.
- При необходимости использовать `DEFERRABLE INITIALLY DEFERRED` только для транзакционных циклов, где это согласовано с ORM и operation semantics; не маскировать invalid ownership deferred constraint.

**Gate M04:** каждая positive-eligible definition имеет retained tenant owner, valid scope registry row, deterministic version resolution, immutable artifact и same-definition proof. Invalid historical definitions/versions остаются quarantined.

### M05: plugin instance registry, runtime generation и fencing

**Alembic revision(s):** расширить `dynamic_app_instances` либо создать dedicated runtime-instance table перед instance grants.

- Materialize exact `plugin_instance_id`; не переиспользовать `app_id`, `app_version_id`, `scope_id` или `session_id`.
- Добавить canonical `tenant_id`, scope anchors, `subject_kind/subject_id` при instance-bound policy, `session_id`, `artifact_digest`, lifecycle status (`draft/published/disabled/revoked/quarantined`), `runtime_ref`, start/stop/heartbeat evidence.
- Добавить monotonic `runtime_generation` для restart/redeploy и `writer_generation` fencing epoch для issue/revoke/runtime workers. Старый generation/epoch не может мутировать grant или runtime state.
- Создать unique key, достаточный для grant FK:
  `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)`.
  Это instance identity key, а не доказательство authorization eligibility: отдельными полями и checks должны быть закреплены exact `plugin_instance_id`, artifact equality, subject/session binding, lifecycle и runtime/writer generations.
- Backfill instance only with exact definition/version/tenant/scope/artifact proof. Missing runtime/session/subject evidence marks row draft/disabled/quarantined by approved policy, never published by inference.
- После того как instance unique key существует, добавить `scope_registry.plugin_instance_id -> instance` and/or reciprocal registry relation in a separate constraint revision. Это закрывает cyclic dependency в порядке: anchors -> instance key -> registry-instance FK.
- Если registry row должна ссылаться на instance до заполнения instance table, оставить linkage nullable/deferred в M02/M03 и ужесточить в M05; не добавлять fake generic FK.

**Gate M05:** exact instance key, artifact equality, lifecycle, subject/session binding and current generation/fencing evidence proven; stale/duplicate/cross-tenant instances quarantined.

### M06: canonical grant DDL, indexes, RLS и trusted write boundary

**Alembic revision(s):** создать canonical grants после registry/instance keys, затем constraints/indexes/policies отдельными безопасными шагами при необходимости.

- Создать `dynamic_app_capability_grants` с explicit `app_definition_id`, `app_version_id`, `app_id`, aligned `tenant_id`, typed scope anchors, `capability_key`, subject binding, optional `plugin_instance_id/session_id`, canonical operations/resources/domains/constraints и four payload digests.
- Добавить composite same-definition FK `(app_version_id, app_definition_id)`; definition tenant FK `(app_definition_id, tenant_id)`; exact instance composite FK `(plugin_instance_id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)` с `ON DELETE RESTRICT`.
- Scope checks должны покрывать tenant/cabinet/project и plugin-instance shape; canonical scope reference должен быть registry composite key либо fixed typed FKs. Generic FK по `scope_id` запрещён.
- Add checks: non-empty capability and policy arrays, constraints object, expiry/revoke invariants, positive generations, subject-kind enum, plugin/session binding. Missing method/constraint semantics не расширять в wildcard.
- Enforce or verify `app_artifact_digest = immutable version artifact_digest`; for instance-bound rows also require instance artifact equality and current runtime generation. A digest copied into a row without equality proof is not an eligible grant.
- Создать partial unique active identity по полному authorization tuple с canonical digests; moving clock/expiry не помещать в predicate. Renewal/reissue закрывает или supersede previous row transactionally и increments `grant_generation`.
- Создать lookup indexes по tenant/scope/app/version/capability/subject/instance, generation/fencing и source mapping. Идемпотентность должна сравнивать payload/binding digest на conflict.
- Включить `ENABLE ROW LEVEL SECURITY` и `FORCE ROW LEVEL SECURITY` на grants, audit, mappings, quarantine и других новых tenant-owned tables. Создать `USING` и `WITH CHECK` policies после parent FKs, но до application cutover.
- RLS policy обязана проверять transaction-local `app.tenant_id`, optional cabinet/project GUCs, active registry row и parent `EXISTS` (company retained/active, cabinet access, project tuple, definition/version/instance lifecycle). Missing/malformed GUC — deny.
- Отдельно ограничить audit append-only privileges/triggers; audit failure не может превратить deny в allow.

**Gate M06:** schema constraints, RLS/FORCE RLS, policy privileges, indexes и trusted transaction boundary review-complete; canonical positive insert still disabled until adapter and backfill gates pass.

### M07: adapter/gateway integration in shadow mode

**Application change:** adapter — единственная compatibility boundary; gateway сохраняет legacy owner до bounded cutover.

- Resolve authenticated principal/session to canonical `tenant_id` and registry scope; compare legacy `company_id`, cabinet/project and plugin assertions, never trust them.
- Resolve definition by stable `app_id` within tenant, then version by `(app_definition_id, manifest_version)`; verify manifest app/version and immutable artifact.
- Parse GrantSet/legacy payload, preserve exact capability `name` as `capability_key`, map methods to operations only by approved versioned rule, retain `eventTypes/maxBytesPerRequest` and unsupported constraints in canonical constraints or quarantine.
- Require exact subject, session and plugin instance binding where policy says so; verify instance artifact, status, runtime generation and writer generation.
- Distinguish `allow`, known `deny`, `quarantine` and adapter/system failure with stable reason codes. Denied/quarantined/unsupported entries never enter positive grants.
- Write canonical grant + mapping + audit atomically through repository/trusted transaction; same source/schema/digest replay is idempotent, changed digest is conflict.
- In shadow mode, compare legacy and canonical decisions without using canonical result for authorization. Emit complete redacted audit envelope including tenant/scope/subject/session/instance/generation/app/version/capability/operation/resource/domain and MCP correlation fields where applicable.

**Gate M07:** adapter resolution deterministic; no fallback to legacy JSONB/requested capabilities/wildcards; shadow output and audit are complete; application still reads legacy as owner.

### M08: quarantine/backfill и canonical readiness

**Application migration job:** import one source row per transaction, resumable by checkpoint.

- Backfill definitions, instances and grants only after their preceding gate. Preserve source IDs, source/canonical payload digests, timestamps, actor provenance, schema version and mapping checkpoint.
- Quarantine orphan, overlong, deleted, cross-tenant, cross-definition, ambiguous, duplicate-active, denied, unsupported, malformed, expired-without-provenance, missing-subject, missing-instance and stale-generation entries.
- Quarantine records are visible/countable/retryable, migration-admin/security scoped, contain no tokens/secrets/raw arguments, and cannot satisfy authorization queries.
- Require source mapping status and target identity before any row becomes authorization eligible. Same source identity with a different digest is a conflict requiring forward repair.
- Keep the legacy table as the decision owner until the partition's bounded cutover. Before that boundary, legacy reads/writes remain enabled and canonical rows are shadow evidence; only after final delta and writer fencing may legacy writes stop and the table become a read-only compatibility projection.

**Gate M08:** for each candidate partition: zero unknown mappings, zero unresolved positive bindings, no open conflict that could alter allow/deny, and complete source/target digest checkpoint. Quarantine may remain only when explicitly excluded from the partition and fail-closed.

### M09: parity, fencing и bounded cutover

**Application rollout:** partition by tenant + app definition, optionally narrower scope/instance.

- Shadow compare exact decisions across tenant, cabinet, project, subject, session, app/version, instance/generation, capability, operation/resource/domain, lifecycle and artifact.
- Require zero unexplained parity mismatches, valid RLS/FK/parent `EXISTS` evidence, zero unknown positive bindings, and recorded audit/checkpoint digest.
- Stop legacy grant/revoke writes for partition; fence in-flight legacy workers and drain final delta. Apply final adapter backfill transaction.
- Switch gateway reads/decisions and management commands to canonical repository. Exactly one writer generation is current; legacy `dynamic_app_grants` becomes read-only projection with mapping IDs.
- Keep cutover bounded; a failed partition does not promote globally and does not change unresolved partitions.

**Positive-write gate after M09:** allow only when authenticated tenant, active registry, parent compatibility, definition/version, exact capability/subject/instance/runtime generation, mapping idempotency, audit write, RLS `WITH CHECK` and current writer generation all pass. Any missing/stale/quarantined condition is deny/quarantine.

## 4. Alembic FK-cycle and operation details

### 4.1 Safe creation order

1. Existing anchors and aligned types (`companies`, cabinets, projects, definition owner key).
2. Empty registry/mapping/quarantine/audit tables and non-cyclic FKs.
3. Tenant/cabinet/project registry rows and validation.
4. Definition/version composite keys and constraints.
5. Instance table fields and composite unique key.
6. Registry-to-instance FK and instance lifecycle/generation constraints.
7. Canonical grant table and grant-to-instance composite FK.
8. RLS policies, privileges, indexes and trusted write path.
9. Adapter/backfill, parity and bounded cutover.

### 4.2 `use_alter` / deferred FK rules

- Model cyclic or late-bound relations explicitly with SQLAlchemy `use_alter=True` and stable constraint names where the implementation requires table creation before the opposite FK; generate a follow-up Alembic `op.create_foreign_key` after both tables and unique keys exist.
- Use nullable/deferred linkage during backfill only where the target contract explicitly permits it. Tighten to `NOT NULL`/validated FK after quarantine removes invalid rows; do not claim nullable adapter checks are permanent integrity.
- For same-transaction current-version or registry-instance cycles, use a reviewed deferrable constraint only if all writes occur inside a trusted transaction and validation cannot be bypassed. Otherwise split the relation into later revision.
- Create `NOT VALID` constraints only as a lock/backfill tactic, then run explicit validation gate before positive writes. A `NOT VALID` FK/check is not implementation complete.
- Do not add `ON DELETE CASCADE` to registry/grant/instance/audit/mapping/quarantine provenance. Use `RESTRICT`, archive transitions, or `SET NULL` only for optional historical audit links.

### 4.3 RLS installation order

1. Create parent anchors and registry/target tables.
2. Add and validate typed/composite FKs and parent lifecycle checks.
3. Enable RLS and `FORCE ROW LEVEL SECURITY`.
4. Create both `USING` and `WITH CHECK` policies for active tenant/cabinet/project/instance contexts.
5. Revoke broad table privileges and grant narrow migration/gateway/audit roles.
6. Exercise no-context/wrong-tenant/wrong-cabinet/cross-project semantics only after verification freeze is lifted; until then, record these as acceptance checks, not executed tests.
7. Enable application cutover only after policies and trusted writer are reviewed.

## 5. Backfill, quarantine и parity acceptance

### Required evidence per source row

- source kind/id and schema version;
- source payload digest and canonical payload digest when representable;
- resolved tenant, scope, definition, version, subject, instance and generation IDs;
- status, stable reason code, first/last observation and migration checkpoint;
- immutable audit correlation without raw credentials, tokens, arguments or secrets.

### Partition acceptance

A partition is eligible for cutover only if all are true:

```text
zero unexplained parity mismatches
zero unknown mappings for retained source rows
zero unresolved positive bindings
all positive rows satisfy typed scope and parent EXISTS proof
all required composite FKs/RLS policies validate
source/target digests and checkpoint are recorded
legacy writer is fenced and final delta is drained
```

Expired/revoked evidence remains retained; expiry is runtime deny, not destructive deletion. Quarantine is not counted as positive parity and cannot be silently retried as allow.

## 6. Rollback and failure handling

### Before canonical read cutover

Stop the affected backfill/adapter writes, leave legacy reads/writes authoritative, and retain target rows, mappings, quarantine and audit evidence. Repair via forward mapping/revision and rerun parity. Do not delete imported rows to hide a mismatch. A schema downgrade is allowed only before tenant data import and after dependency/row-count/export preflight proves no references.

### After canonical read cutover, before legacy cleanup

There is no dual-owner rollback. Fence canonical writer generation and stop new issue/revoke workers for the affected partition; keep canonical reads authoritative and default incomplete reconciliation to deny. Repair via canonical commands, CAS/idempotency and forward revisions. Legacy rows remain read-only projection, never restored as authorization authority.

### Runtime generation/fencing failure

If a worker presents stale `writer_generation`, reject mutation and audit the fencing event. If runtime generation/artifact/session evidence is stale or unavailable, deny/quarantine calls and grants; do not reuse another instance or fall back to app-wide permission. Reissue creates a new grant generation and preserves historical row/audit evidence.

### Cleanup

Only after retention/rollback window, complete source mapping or owner-approved quarantine/retirement, zero legacy readers/writers and valid audit replay may a later cleanup revision remove compatibility projection. Before dropping the legacy table, explicitly remove/validate every FK, projection refresh, reader and writer dependency. Drop order is child indexes/readers, projection refresh, compatibility adapter, then legacy table; mapping, quarantine, audit, canonical grants, definitions, instances and owner anchors remain protected evidence.

## 7. Explicit positive-write gates

Every canonical positive write must pass all applicable gates atomically:

1. authenticated principal resolves to one retained `companies.id` and transaction-local `app.tenant_id`;
2. all client/plugin `company_id`, tenant, cabinet, project, subject and instance assertions match server resolution;
3. `(scope_kind, scope_id)` resolves to exactly one active registry row with valid typed anchors;
4. cabinet access and project `(company_id, cabinet_id)` compatibility are proven;
5. definition owner and version same-definition composite FKs/lifecycle/artifact are valid;
6. instance, if required, matches exact definition/version/tenant/scope, artifact, subject/session and current runtime generation;
7. capability identity, bounded arrays/constraints, lifecycle, expiry/revoke state and generation are valid;
8. mapping idempotency key and payload digest are conflict-free;
9. current `writer_generation` owns the partition and stale worker CAS fails closed;
10. audit event is durably appended in the trusted transaction boundary;
11. RLS `USING`/`WITH CHECK`, parent `EXISTS` and role checks succeed.

Failure of any gate yields deny, quarantine or explicit system failure; never an allow through legacy JSONB, requested capabilities, wildcard scope, guessed owner or adapter-only assertion.

## 8. Review handoff and verification freeze

Implementation PRs must include focused acceptance coverage for type conversion/quarantine, registry shapes, composite FK enforcement, `use_alter`/deferred cycle, RLS/FORCE RLS no-context and cross-tenant behavior, adapter resolution, instance generation/fencing, idempotent mapping/conflict, revoke/expiry, parity and forward rollback. Those checks are written as part of implementation but executed only when migration-wide verification freeze is explicitly lifted.

Для этой documentation-only задачи tests, build, lint, format, compile, Alembic execution, migration execution и git checks намеренно не запускались. Shared-environment DDL, backfill, cutover и cleanup не выполнялись и этим планом не разрешаются автоматически.
