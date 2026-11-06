# Plugin-instance scope: decision for registry and canonical grants

Статус: migration-stage decision artifact, только документация. Baseline: 2026-11-01.

Документ выбирает форму `plugin_instance` scope для typed owner registry, canonical grants и dynamic app instances. Он сравнивает два варианта:

1. `plugin_instance` как `scope_kind` в `scope_registry`;
2. точная `plugin_instance_id` binding при tenant/cabinet/project scope.

Документ не является Alembic revision, не изменяет SQLAlchemy-модели, gateway, adapter, declarative-app v1, canonical DDL или core docs. Tests/build/lint/format/compile/migration execution/git checks намеренно не выполняются; verification freeze подтверждён в конце.

## 1. Decision summary

Выбран target shape: **`plugin_instance` является полноценным typed `scope_kind` в `scope_registry`, а canonical grant дополнительно хранит exact `plugin_instance_id` binding там, где authorization относится к конкретной установке/runtime**.

Это не два конкурирующих policy owner. Registry отвечает на вопрос «какая typed scope и какие owner anchors принадлежат tenant», а exact instance binding отвечает на вопрос «какой конкретный installation/runtime может использовать grant». Поэтому:

- `(scope_kind, scope_id)` всегда разрешается через ровно одну активную registry row;
- для `scope_kind = 'plugin_instance'` обязательно `scope_id = plugin_instance_id`;
- instance-bound grant при tenant/cabinet/project effective scope обязан иметь exact `plugin_instance_id`, когда policy требует runtime/installation binding;
- `plugin_instance_id` в grant не может быть заменён `app_id`, `app_version_id`, `scope_id` или `session_id`;
- `session_id`, subject и runtime generation остаются отдельными call/runtime gates;
- declarative-only grant может быть instance-independent только при явно утверждённой bounded policy, с явным sentinel/namespace правилом и без неявного расширения на все instances.

Выбор сохраняет единый ownership graph для RLS и audit, одновременно не смешивая owner scope с lifecycle identity конкретного runtime.

## 2. Варианты

### 2.1 Variant A: `plugin_instance` как `scope_kind`

Registry содержит typed rows для `tenant`, `cabinet`, `project` и `plugin_instance`:

```text
plugin_instance registry row:
  scope_kind = 'plugin_instance'
  scope_id = plugin_instance_id
  tenant_id = canonical companies.id
  cabinet_id/project_id = installation parent, if applicable
  plugin_instance_id = scope_id
  owner_state = active | archived | quarantined
```

Canonical grants могут ссылаться на registry scope. Instance-bound grants также сохраняют exact `plugin_instance_id` и composite relation к instance/version/definition/tenant/scope. Для tenant/cabinet/project scope grant может быть policy-scoped, но не становится runtime allow без нужной exact instance check.

**Преимущества:**

- registry становится единым typed resolution boundary для всех owner scopes;
- RLS и parent `EXISTS` получают одинаковую форму для plugin installation и обычных scopes;
- plugin installation имеет собственные `owner_state`, provenance, quarantine и archive semantics;
- можно различать installation ownership, instance lifecycle и active runtime generation;
- предотвращается polymorphic FK illusion на `scope_id`;
- canonical grants могут безопасно поддержать как instance-scoped, так и policy-approved instance-independent declarative grants.

**Стоимость и риски:**

- появляется циклическая зависимость registry ↔ `dynamic_app_instances`, которую нужно staged/deferred;
- требуется чётко определить, какие cabinet/project anchors зеркалятся на plugin row и как проверяется их совместимость;
- `plugin_instance` нельзя разрешать только по существованию строки: нужны artifact, subject/session, lifecycle и generation checks;
- расширяется migration/backfill surface: старые rows без exact instance остаются quarantine или instance-independent по утверждённой policy.

### 2.2 Variant B: exact `plugin_instance_id` при tenant/cabinet/project scope

Registry содержит только `tenant`, `cabinet`, `project`; grant сохраняет обычный effective scope, а exact instance хранит отдельным nullable `plugin_instance_id`:

```text
scope_kind = tenant | cabinet | project
scope_id = tenant_id | cabinet_id | project_id
plugin_instance_id = exact installation, when required
```

Instance identity проверяется отдельным composite FK/service relation, но не является registry scope.

**Преимущества:**

- меньше typed scope kinds и проще первоначальная схема registry;
- меньше циклов в DDL и меньше новых rows при backfill;
- удобная модель для policy grants, которые разрешают capability на cabinet/project и затем проверяют runtime instance.

**Недостатки:**

- ownership и runtime identity расходятся: exact instance не получает единого scope-registry owner anchor;
- для `plugin_instance_id` нужен второй polymorphic/conditional relation или отдельный registry, фактически возвращающий Variant A под другим именем;
- RLS scope проверяет tenant/cabinet/project, но не может одной registry resolution гарантировать installation parent compatibility;
- легко ошибочно считать cabinet/project grant переносимым между instances;
- plugin instance archive/quarantine/provenance и lifecycle начинают жить вне typed owner boundary;
- tenant-scoped installation без cabinet/project требует дополнительной special case проверки;
- риск mismatch между `scope_kind/scope_id`, instance tenant and installation scope выше, особенно при copied denormalized columns.

### 2.3 Decision matrix

| Критерий | Variant A: registry `plugin_instance` | Variant B: exact binding only | Решение |
|---|---|---|---|
| Typed ownership/RLS | Одна registry resolution для всех scopes | Scope и instance проверяются раздельно | A |
| Exact runtime authorization | Дополнительный exact binding всё равно нужен | Exact binding обязателен | Оба, но A даёт owner anchor |
| FK integrity | Composite registry/instance FKs после staging | Отдельная relation, иначе weak service-only proof | A |
| DDL complexity | Выше из-за цикла, решается staged FK | Ниже первоначально, выше при полном integrity | A |
| Declarative instance-independent policy | Возможна явно как tenant/cabinet/project scope | Естественна, но transfer risk выше | A с explicit policy |
| Archive/quarantine evidence | Typed row и state в registry | Отдельная evidence/lifecycle path | A |
| Cross-scope confusion risk | Check `scope_id = plugin_instance_id` | Check exact binding against ordinary scope | A |
| Migration effort | Больше rows/anchor reconciliation | Меньше rows, но more service exceptions | A as target; allow staged rollout |
| Compatibility with v1 GrantSet | Adapter supplies instance from server context | Adapter supplies instance from server context | Паритет |

## 3. Target shape

### 3.1 Registry contract

`scope_registry` получает normative scope kinds:

```text
tenant | cabinet | project | plugin_instance
```

Минимальный plugin row:

```text
(scope_kind, scope_id, tenant_id,
 cabinet_id, project_id, plugin_instance_id,
 owner_state, source_kind, source_id,
 schema_version, payload_digest)
```

Обязательные checks:

```text
scope_kind = 'plugin_instance'
scope_id = plugin_instance_id
plugin_instance_id IS NOT NULL
tenant_id IS NOT NULL
cabinet_id/project_id mirror installation scope
```

`cabinet_id` и `project_id` могут быть оба NULL только для явно разрешённой tenant installation. Plugin row не наследует project scope молча. Если instance installation scope изменяется, создаётся новая forward state/installation identity; существующая registry identity не переписывается для сокрытия истории.

Unique boundaries:

- `UNIQUE (scope_kind, scope_id)` — ровно одна logical scope;
- unique typed anchor tuple, включающий tenant/cabinet/project/plugin instance;
- unique instance identity key на instance table: `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)`;
- exact instance ID не переиспользуется после revoke/archive;
- registry-to-instance and instance-to-registry relations are both named, validated constraints after staging; one nullable/deferred link is temporary migration state, not the final integrity boundary.

`owner_state = active` допускает positive reads/writes только при остальных lifecycle gates. `archived` и `quarantined` сохраняются для migration/security/audit roles и никогда не являются authorization source.

### 3.2 Dynamic app instance contract

`dynamic_app_instances` или dedicated runtime-instance table должен иметь:

- exact `plugin_instance_id`/primary instance identity;
- `app_definition_id`, `app_version_id`, canonical `tenant_id`;
- `scope_kind`, `scope_id` и resolved cabinet/project anchors;
- immutable `artifact_digest`, равный published version artifact digest;
- `subject_kind`, `subject_id` при instance-bound policy;
- `session_id` как active call/runtime boundary, но не как instance identity;
- lifecycle `draft | published | disabled | revoked | quarantined`;
- `runtime_generation` для restart/redeploy;
- `writer_generation` для issue/revoke/runtime worker fencing;
- runtime evidence (`runtime_ref`, start/stop/heartbeat/lease metadata) без секретов.

Instance row eligible for positive authorization только когда exact definition/version/tenant/scope, artifact, status, subject/session and current runtime generation are proven. Registry membership alone is insufficient.

### 3.3 Canonical grant shape

`dynamic_app_capability_grants` остаётся единственным canonical authorization owner после bounded cutover. Его effective scope fields are:

```text
app_definition_id
app_version_id
app_artifact_digest
tenant_id
cabinet_id nullable
project_id nullable
scope_kind
scope_id
capability_key
subject_kind
subject_id
plugin_instance_id nullable
session_id nullable
operations/resources/domains/constraints + canonical digests
grant_generation
writer_generation
idempotency_key
```

Normative rules:

1. `scope_kind/scope_id` resolves through active typed registry; generic FK on polymorphic `scope_id` is forbidden.
2. If `scope_kind = 'plugin_instance'`, `plugin_instance_id` is non-null and equals `scope_id`.
3. If effective scope is tenant/cabinet/project and the grant is for a concrete plugin/runtime call, `plugin_instance_id` is mandatory and must satisfy the exact composite instance FK; the scope registry row must also prove the same tenant and cabinet/project installation anchors.
4. A policy-scoped declarative grant may omit `plugin_instance_id` only when a versioned policy explicitly permits instance-independent use. It must not be interpreted as “all instances” by default; the policy must define the bounded subject, scope, lifecycle, artifact/version compatibility and runtime admission path. Such a row is not eligible for a code-plugin call until the gateway resolves a concrete instance and applies the instance gates.
5. `session_id` is required where active session binding is policy-required and is never used to identify an installation.
6. `subject_kind/subject_id` is server-resolved and not nullable for v1 subject-bound grants. An unbound policy needs an explicit reviewed sentinel, not SQL NULL wildcard behavior.
7. Composite FKs prove identity, not eligibility. Parent `EXISTS` checks still verify active tenant/cabinet/project, definition/version, artifact, instance lifecycle, subject/session and current generation.
8. One row represents one `capability_key` for one binding. v1 denied/unsupported/unbound entries go to audit/quarantine, not positive grants.

Active uniqueness must include the complete authorization tuple and canonical payload digests, including an explicit empty sentinel only for an approved instance-independent branch. `grant_generation` handles reissue; `writer_generation` fences stale workers. Expiry is evaluated at authorization time, not in a moving-clock index predicate.

## 4. FK, unique and delete implications

### 4.1 FK order and cycle

The exact relation is conceptually:

```text
(plugin_instance_id, app_definition_id, app_version_id,
 tenant_id, scope_kind, scope_id)
  -> dynamic_app_instances(
       id, app_definition_id, app_version_id,
       tenant_id, scope_kind, scope_id)
```

Safe order:

1. Align canonical tenant type to `companies.id`; prepare cabinet/project compatibility keys.
2. Create empty scope registry, mapping, quarantine and audit tables.
3. Backfill tenant/cabinet/project registry rows and validate typed anchors.
4. Make definition `(id, tenant_id)` and version `(id, app_definition_id)` keys real.
5. Add instance fields and unique composite key; backfill only exact instances.
6. Add both registry-to-instance and instance-to-registry FK relations in named follow-up constraint revision(s), after the instance composite key exists. Use `use_alter=True`, explicit `op.create_foreign_key`, or a reviewed deferred relation for the cycle; nullable staging is temporary, not permanent integrity.
7. Create canonical grants and exact composite grant-to-instance FK.
8. Add checks, indexes, RLS and trusted writer before any positive canonical write.

No adapter-only assertion substitutes for the final validated FK/relation. `NOT VALID` is a staging tactic only; it must be validated before the relevant positive-write gate.

### 4.2 Delete and archive

Use archive/forward state transitions and `ON DELETE RESTRICT` for registry, definition/version, instance and canonical grant provenance. Do not allow company/cabinet/project or version cascades to erase grants, registry rows, mappings, quarantine or audit evidence. Audit/mapping/quarantine may use `SET NULL` for optional target references while retaining immutable source ID/digest. Hard delete is allowed only for pre-import orphan cleanup after dependency and retention preflight.

## 5. RLS, audit and fencing

### 5.1 RLS

New tenant-owned tables use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, with both `USING` and `WITH CHECK`. Policies require server-set transaction-local context:

```text
app.tenant_id
app.cabinet_id (optional)
app.project_id (optional)
app.user_id / app.role
```

Grant/registry policy must verify:

- `tenant_id = current_setting('app.tenant_id', true)`;
- active registry row for `(scope_kind, scope_id)`;
- cabinet/project GUC and active parent/membership compatibility where relevant;
- exact instance tenant/definition/version/scope equality when `plugin_instance_id` is present;
- active lifecycle, artifact and current runtime generation through parent `EXISTS`/trusted writer checks.

Missing or malformed GUC denies. Client/plugin claims are compared assertions only. Archived/quarantined plugin rows are invisible to ordinary tenant authorization reads. Mapping/quarantine/audit have narrower migration/security/audit roles; unresolved evidence is migration-admin-only.

### 5.2 Audit

Append-only capability audit records every issue/import, allow/deny, revoke, expiry observation, adapter failure, quarantine, cutover and fencing event. Events retain tenant/scope, definition/version, capability, subject/session, exact plugin instance, runtime/grant/writer generation, reason, source/target digests and correlation IDs. Raw tokens, arguments and secrets are excluded.

Application/API roles cannot update/delete audit rows. A deny or audit write failure cannot become allow. `plugin_instance_id` and generation are mandatory for instance-bound events; unresolved migration events may be tenant-null only under migration-admin policy and must never accept client-supplied tenant identity.

### 5.3 Fencing

`runtime_generation` changes on restart/redeploy; a grant/runtime call carrying an old generation is denied and audited. `writer_generation` is a partition fencing epoch; issue/revoke/update operations use CAS against the current epoch. After cutover, stale legacy workers and old canonical workers both receive zero-row mutation/deny. Reissue creates a new grant generation and preserves old evidence. No runtime fallback to another instance or app-wide grant is allowed.

## 6. Migration order and compatibility

### 6.1 Migration order

The following order is normative at the design level and maps to the existing owner-registry plan without changing its M labels (the plan's cross-domain M04/M05/M06 content, not the typed-registry gate labels):

1. **Inventory/type alignment:** classify overlong, orphan, duplicate, deleted, cross-tenant and ambiguous IDs; align future `tenant_id` to `companies.id`; quarantine invalid values.
2. **Empty evidence/registry structures:** create scope registry, mappings, quarantine and append-only audit; no positive writes.
3. **Tenant/cabinet/project registry:** backfill only proven typed parent tuples and active access relations.
4. **Definition/version foundation:** add definition owner key, same-definition version key, immutable artifact proof and staged current-version cycle (implementation-plan M04).
5. **Instance foundation:** materialize exact `plugin_instance_id`, instance composite key, artifact, lifecycle, subject/session and runtime/writer generations; quarantine unsupported rows (implementation-plan M05).
6. **Close cyclic relation:** validate both named registry-instance FK directions and all typed shape checks.
7. **Canonical grant DDL:** create capability identity, exact scope/instance fields, composite FKs, active uniqueness, indexes, RLS and trusted writer (implementation-plan M06).
8. **Adapter shadow/backfill:** map declarative v1 and legacy entries idempotently; preserve source/target digests; deny/quarantine incomplete bindings.
9. **Parity and bounded cutover:** partition by tenant + definition, optionally scope/instance; require zero unexplained mismatch, zero unknown positive bindings, validated FKs/RLS, final delta and fenced legacy writer.
10. **Cleanup later:** retain legacy `dynamic_app_grants` as read-only compatibility projection through rollback/retention window; delete only in a later reviewed forward migration.

### 6.2 Declarative-app v1 compatibility

The decision is compatible with declarative-app v1 because v1 `GrantSet` is requested/provenance data, not a self-sufficient allow. The adapter must:

- resolve `app_id` and `manifest_version` inside authenticated tenant and definition;
- preserve `GrantSet.name` as exact `capability_key`;
- map `methods` to canonical `operations` only through approved versioned mapping;
- retain/enforce `resources`, `domains`, `eventTypes`, `maxBytesPerRequest` and lifecycle times;
- obtain tenant/cabinet/project, subject, session and exact plugin instance server-side;
- require artifact equality and current runtime generation for plugin calls;
- route denied, unsupported, ambiguous, missing-binding and instance-unresolvable entries to quarantine/audit;
- never turn absent methods into wildcard operations or treat requested capabilities as grants.

A v1 GrantSet contains no `plugin_instance_id`; therefore instance binding is resolved from authenticated runtime/session registry. Until that registry relation and generation proof exist, instance-scoped entries cannot become positive canonical grants. A declarative-only policy grant may be imported without an instance only if the policy explicitly says that the grant is reusable at the selected tenant/cabinet/project scope and the gateway still performs exact runtime admission.

This remains compatible with `dynamic-ui-implementation-contract.md`: manifest `requested_capabilities` stay requested contract, typed actions/queries remain independently capability-checked, and no UI route or action receives privilege from manifest presence alone.

### 6.3 Alignment with canonical DDL and owner-registry plan

The decision extends, rather than replaces, `canonical-grant-target-ddl.md`:

- its current tenant/cabinet/project checks must be expanded to include the approved plugin-instance registry branch;
- its exact composite instance FK remains required for instance-bound grants;
- its active uniqueness, digest, audit, mapping/quarantine, `RESTRICT`, RLS and fencing rules remain applicable;
- the current `CHECK (scope_kind IN ('tenant','cabinet','project'))` is a known target-contract delta and cannot be treated as final after this decision.

It follows `owner-registry-implementation-plan.md`: M02 creates empty registry/evidence tables, M03 backfills typed parents, M05 prepares exact instance identity and generations, and M06 creates canonical grants. The M05 registry-instance cycle is explicitly staged before M06 positive writes. `typed-scope-owner-registry.md` remains the source for canonical company/tenant and parent compatibility semantics.

## 7. Positive-write gates

A positive canonical grant, instance publication or runtime mutation is allowed only when all applicable conditions pass atomically:

1. authenticated principal resolves to one retained `companies.id` and transaction-local tenant context;
2. client/plugin company, tenant, cabinet, project, subject and instance assertions match server resolution;
3. `(scope_kind, scope_id)` resolves to exactly one active registry row with valid shape and parent anchors;
4. for plugin scope, `scope_id = plugin_instance_id`; for ordinary scope, exact instance relation matches the effective scope when required;
5. cabinet access and project `(company_id, cabinet_id)` compatibility are proven;
6. definition/version same-definition keys, lifecycle and immutable artifact digest are valid;
7. subject/session binding and current runtime generation are valid for instance/runtime policy;
8. capability identity, bounded operations/resources/domains/constraints, expiry and revoke invariants are valid;
9. mapping idempotency and canonical payload/binding digests are conflict-free;
10. current `writer_generation` owns the partition and stale CAS fails closed;
11. immutable audit append succeeds in the trusted transaction boundary;
12. RLS `USING`/`WITH CHECK`, role privileges and parent `EXISTS` checks succeed.

Failure is deny, quarantine or explicit system failure, never an allow via legacy JSONB, requested capabilities, wildcard scope, guessed owner, alternate instance or adapter-only assertion.

## 8. Unresolved risks and blockers

- Existing dynamic-app IDs are `String(80)` while canonical company/cabinet/project IDs are `String(40)`; conversion requires inventory and quarantine, not implicit cast.
- Current `dynamic_app_instances` lacks canonical tenant, exact subject/session relation, artifact field and runtime/writer generation; Variant A cannot be enabled for positive writes until M05 closes these gaps.
- Registry-instance FK is cyclic and needs named staged/deferred DDL; incorrect ordering can leave a service-only integrity hole.
- Cabinet/company access and project/company/cabinet compatibility are separate relations; independent FKs do not prove the typed parent tuple.
- Existing cascade deletes can erase owners/evidence unless archive-only and `RESTRICT` policy is implemented first.
- The current canonical grant contract's scope check excludes `plugin_instance`; implementation must revise the target model/DDL in a separate reviewed migration PR.
- v1 GrantSet does not carry exact instance ID; server-side runtime resolution, session binding and generation evidence remain mandatory and may quarantine historical entries.
- The policy boundary for reusable instance-independent declarative grants is not yet fully specified. Until its subject, scope, artifact and runtime admission semantics are approved, omit-instance means deny/quarantine rather than all-instance allow.
- Registry membership is not lifecycle authorization: archived, disabled, revoked or stale instances must remain denied even when FKs are valid.
- Partial active uniqueness and expiry/reissue races need PostgreSQL-specific implementation and acceptance coverage after verification freeze.
- Audit append-only privileges/triggers and migration-admin visibility require a security-reviewed implementation; documentation alone does not enforce them.
- Partition cutover cannot proceed with unknown mappings, unresolved positive bindings, unexplained parity mismatch or an unfenced legacy writer.

No positive canonical write, shared-environment DDL, backfill, cutover or cleanup is authorized by this document alone.

## 9. Verification freeze

This is a documentation-only migration artifact. No tests, build, lint, format, compile, Alembic execution, migration execution or git checks were run. No code or core documentation was changed. Verification remains frozen until the entire migration is implemented and reviewed multiple times, followed by an explicit decision to lift the freeze.
