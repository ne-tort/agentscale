# Canonical tenant and scope registry

Статус: migration-stage design contract, только документация. Baseline: 2026-11-01.

Документ выбирает canonical tenant key и описывает typed ownership boundary для tenant/cabinet/project/plugin instance. Он согласован с [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md), [`dynamic-grants-binding-gaps.md`](dynamic-grants-binding-gaps.md), [`dynamic-grants-ownership.md`](dynamic-grants-ownership.md), [`dynamic-app-migration-gaps.md`](dynamic-app-migration-gaps.md) и [`persistence-target-map.md`](persistence-target-map.md). Он не является Alembic-ревизией, не меняет SQLAlchemy-модели, gateway, ORM или core docs. Tests/build/lint/compile/git checks для этой документационной задачи намеренно не выполняются.

## 1. Decision

### 1.1 Canonical tenant key: `companies.id`

Canonical tenant key в текущем migration-stage — `companies.id`.

- Логическое имя в новых контрактах и RLS GUC — `tenant_id`.
- Физическое значение `tenant_id` равно `companies.id`, с тем же строковым типом/форматом идентификатора. Текущие dynamic-app ORM columns используют более широкий `String(80)`, чем `companies.id` (`String(40)`); до FK/backfill foundation revision должна выровнять физический тип/длину и проверить существующие значения, а не полагаться на implicit cast.
- `tenants.id` не вводится как вторая identity: текущей таблицы `tenants` нет, а `companies.id` уже является FK-родителем для `memberships`, `cabinet_instances`, `projects`, company module data и tenant-infra boundary.
- `company_id` — legacy/source vocabulary и compatibility input. Он не является вторым policy owner и не должен использоваться в новых authorization/FK/RLS predicates.

Выбор `companies.id` минимизирует разрыв с существующими owner anchors и не требует скрытого `company -> tenant` backfill, который мог бы раздвоить membership, soft-delete и ACL semantics. `tenant_id` сохраняется как domain name, чтобы новые capability/equipment/runtime контракты не размножали `company_id` и могли позже перейти на отдельную `tenants` таблицу только отдельной foundation migration.

Создание отдельной `tenants` таблицы допустимо только как явно одобренная будущая identity migration: сначала стабильная one-to-one mapping и доказанная parity, затем staged FK switch, затем removal compatibility. Само переименование или копирование `companies.id` в новую таблицу не меняет canonical key автоматически.

### 1.2 Canonical ownership hierarchy

```text
companies.id                         -- tenant_id, canonical tenant anchor
  -> cabinet_instances.id            -- cabinet anchor, tenant membership proven by registry/grants
      -> projects.id                 -- project anchor, company/cabinet parent proven
          -> plugin instance id      -- exact runtime installation, app/version/scope proven
```

Cabinet membership is many-to-many in the current model (`cabinet_company_grants`), поэтому `cabinet_instances.company_id`/`owner_company_id` alone не являются достаточным authorization proof. Project currently stores both `company_id` и `cabinet_id`; оба значения должны согласовываться с typed registry row, а не просто копироваться в grant. Plugin instance is not a tenant, cabinet or project and must remain an exact installation/runtime identity.

## 2. Scope registry contract

### 2.1 Purpose and rows

`scope_registry` — target foundation registry for stable, queryable scope identity. It replaces unsafe assumptions that a polymorphic `(scope_kind, scope_id)` is a foreign key. One row represents one resolved scope and contains the typed parent anchors required for ownership checks:

| Field | Rule |
|---|---|
| `scope_kind` | Exactly `tenant`, `cabinet`, `project`, or `plugin_instance`. |
| `scope_id` | Stable ID of the row named by `scope_kind`; never inferred from JSONB. |
| `tenant_id` | Always `NOT NULL`; FK to `companies.id`. For `tenant`, equals `scope_id`. |
| `cabinet_id` | Required for `cabinet`, `project`, and `plugin_instance` when the instance is cabinet/project scoped; null for tenant scope. FK to `cabinet_instances.id`. |
| `project_id` | Required for project-scoped and project-installed plugin instances; null otherwise. FK to `projects.id`. |
| `plugin_instance_id` | Required only for `plugin_instance`; equals `scope_id`. FK to the instance registry after instance foundation exists. |
| `owner_state` | `active`, `archived`, or `quarantined`; only `active` rows may support positive writes. |
| `source_kind/source_id` | Immutable migration provenance, not authorization input. |

The exact table/column names are implementation decisions for the foundation revision; the shape and checks are normative. A registry row must have a unique identity on `(scope_kind, scope_id)` and a unique typed anchor tuple, for example `(scope_kind, scope_id, tenant_id, cabinet_id, project_id, plugin_instance_id)`. There must be no second registry row for the same logical scope.

The registry has four typed shapes:

```text
tenant:
  scope_id = tenant_id; tenant_id != NULL;
  cabinet_id = NULL; project_id = NULL; plugin_instance_id = NULL

cabinet:
  scope_id = cabinet_id; tenant_id != NULL; cabinet_id = scope_id;
  project_id = NULL; plugin_instance_id = NULL

project:
  scope_id = project_id; tenant_id != NULL; cabinet_id != NULL;
  project_id = scope_id; plugin_instance_id = NULL

plugin_instance:
  scope_id = plugin_instance_id; tenant_id != NULL;
  plugin_instance_id = scope_id;
  cabinet_id/project_id mirror the installation scope, if applicable
```

The registry must reject impossible shapes (for example a project without cabinet, a tenant with a cabinet anchor, or a plugin instance with a different `plugin_instance_id`). A plugin instance may be tenant-scoped only when the dynamic-app policy explicitly permits tenant installation; it may not silently inherit a project scope.

### 2.2 Typed owner anchors

The registry does not make `scope_id` polymorphism look like a real FK. Each anchor is typed and independently verifiable:

- `tenant_id -> companies.id`, `ON DELETE RESTRICT` or archive-only policy;
- `cabinet_id -> cabinet_instances.id`, `ON DELETE RESTRICT` for registry/security evidence;
- `project_id -> projects.id`, `ON DELETE RESTRICT` for registry/security evidence;
- `plugin_instance_id -> dynamic_app_instances.id` or a dedicated runtime-instance table, `ON DELETE RESTRICT`.

The current ORM has independent FKs, but does not enforce every cross-parent relation with composite FKs. In particular, `projects.company_id -> companies.id` and `projects.cabinet_id -> cabinet_instances.id` do not prove that the two parents belong together, while cabinet access can be granted through `cabinet_company_grants`. `cabinet_instances.company_id` and `owner_company_id` are nullable legacy/mirrored anchors, not sufficient proof. Foundation backfill must therefore prove:

```text
project.company_id = registry.tenant_id
AND project.cabinet_id = registry.cabinet_id
AND active cabinet-company membership/owner policy exists
```

That proof may be represented by a future typed ownership relation or a reviewed service transaction plus registry consistency constraint. A bare `project_id -> projects.id` FK does not prove that a caller-selected `tenant_id` or `cabinet_id` belongs to that project. The same explicit proof is required for `dynamic_app_definitions.tenant_id`: the current ORM has no FK to `companies.id`, so definition-owner tenant consistency must be added as a real owner key/constraint or verified in the trusted write path before definition/grant positive writes.

`owner_employee_id`, `employee_id`, authenticated `user_id`, service principal and role are actor/subject anchors, not tenant anchors. They require separate subject/membership checks and must not be packed into `scope_id`.

## 3. Composite FK strategy

### 3.1 App/version/definition proof

Dynamic app dependencies use the existing composite pattern:

```text
(app_version_id, app_definition_id)
  -> dynamic_app_versions(id, app_definition_id)
```

The simple `app_version_id -> dynamic_app_versions.id` FK currently present on `dynamic_app_capability_grants` is insufficient for same-definition proof and must be replaced or supplemented before positive canonical writes. The same rule applies to grants and instances. `app_id` is denormalized lookup data; definition ownership and equality checks remain mandatory.

### 3.2 Scope references

A grant, resource, runtime, equipment or audit table may retain explicit `tenant_id`, `cabinet_id`, `project_id` columns for indexing and RLS, but its positive scope reference must be one of:

1. a composite FK to a unique registry key such as `(scope_kind, scope_id, tenant_id, cabinet_id, project_id)`; or
2. separate typed FKs for a fixed scope shape, where the table has a non-polymorphic owner contract.

A generic FK from `scope_id` to `companies.id`, `cabinet_instances.id`, or `projects.id` is forbidden. It would accept an ID from the wrong table and would not prove parent tenant consistency.

For `dynamic_app_capability_grants`, the target relation is conceptually:

```text
(plugin_instance_id, app_definition_id, app_version_id, tenant_id,
 scope_kind, scope_id)
  -> dynamic_app_instances(id, app_definition_id, app_version_id,
                           tenant_id, scope_kind, scope_id)
```

The instance table must expose the matching unique key, and its scope columns must themselves resolve through `scope_registry`. If the instance table is not ready, the relation is staged: create/validate foundation registry and instance unique key first, then add the composite FK, then enable positive writes. Adapter assertions are not a permanent substitute for a database relation.

For child tables whose ownership is inherited, prefer the parent FK and parent `EXISTS` policy over copied tenant columns. For example, materialization entries resolve through `project_runtime_revisions -> projects`; equipment offers resolve through snapshot -> ingestion run; approval requests resolve through run -> project. A copied tenant field may be retained for lookup only if trusted writes prove equality.

### 3.3 Delete policy

Registry and authorization evidence use `RESTRICT` or explicit archive transitions. Derived operational children may use `CASCADE` only where the existing target map explicitly permits it. Audit, mapping and quarantine rows never cascade-delete evidence. Operational rollback is not a schema downgrade and must not be implemented by deleting scope rows.

## 4. RLS context and parent `EXISTS`

Every tenant-owned target table uses transaction-local context. The canonical settings are:

```text
app.tenant_id   = canonical companies.id
app.cabinet_id  = optional cabinet_instances.id
app.project_id  = optional projects.id
app.user_id     = authenticated subject/employee id
app.role        = resolved role
```

The application sets these values only after authenticated server-side resolution; client/plugin `company_id`, `tenant_id`, cabinet or project claims are assertions to compare, never authority. Values are transaction-local and cleared at transaction end. Missing `app.tenant_id` is deny, not an unrestricted/platform default.

Policy shape:

- tenant scope: `tenant_id = current_setting('app.tenant_id', true)` and `EXISTS` to `companies` proving the retained/allowed status (not merely ID existence) where needed;
- cabinet scope: registry row matches tenant and `cabinet_id = current_setting('app.cabinet_id', true)` plus active cabinet membership/owner policy;
- project scope: registry row matches all tenant/cabinet/project GUCs and `projects` confirms the same typed anchors;
- plugin instance: registry row matches tenant and installation scope, then instance status/version/artifact/runtime generation are checked by the gateway/service;
- child rows: parent `EXISTS` predicate establishes ownership; child-supplied tenant values cannot widen access.

RLS must have both `USING` and `WITH CHECK`, with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` for new tenant-owned tables. Policies are created after typed parent FKs and before application cutover. The registry itself must deny rows with `owner_state <> 'active'` for positive reads/writes while preserving archived/quarantined evidence for migration operators.

RLS is not a replacement for gateway checks: exact app definition/version, subject, plugin runtime generation, capability, operation/resource/domain and lifecycle remain mandatory before adapter invocation. Conversely, a gateway check is not a replacement for the registry FK/RLS boundary.

## 5. `company_id` compatibility mapping

Compatibility mapping is explicit and one-way toward canonical `tenant_id`:

| Legacy input | Resolution | Positive-use rule |
|---|---|---|
| `company_id` in gateway context | Resolve `companies.id` from authenticated principal/session, membership, and any cabinet/project anchor | Accept only when all independently resolved anchors equal the same `tenant_id`; otherwise deny/quarantine. |
| `company_id` in legacy grant/app payload | Treat as source provenance | Never copy into a canonical grant without definition/scope/registry proof. Preserve source value and digest in mapping/audit. |
| `cabinet_instances.company_id` | Legacy mirrored primary org anchor | Diagnostic hint only; verify against active cabinet-company grant/owner policy and registry. |
| `projects.company_id` | Existing project tenant column | Verify against `projects.id`, `cabinet_id`, registry and authenticated tenant; no client override. |
| equipment/automation `tenant_id` | Existing domain vocabulary | During backfill map directly to `companies.id` only after inventory proves identifier parity; orphan/ambiguous values quarantine. |

Mapping table requirements are `(source_kind, source_id, canonical_schema_version)` unique, source/canonical payload digests, resolved tenant/scope IDs, status, reason code, checkpoint and target IDs. Same source identity with a different digest is a conflict, never an overwrite. The adapter must retain `company_id` only as migration provenance where needed; canonical authorization queries and FKs use `tenant_id`.

## 6. Migration, backfill and quarantine

### Phase M00: inventory and anchors

1. Inventory `companies`, `cabinet_instances`, `cabinet_company_grants`, `projects`, memberships, dynamic definitions/versions/grants/instances and all `tenant_id` producers.
2. Count missing, soft-deleted, duplicate, cross-company and cross-cabinet relationships. Record source IDs and digests.
3. Validate that every candidate `tenant_id` maps to exactly one `companies.id`; do not create a new tenant row or guess from display name/login slug.
4. Create empty registry, mapping, quarantine and audit structures before any positive scope/grant write.

### Phase M01: registry backfill

1. Insert tenant rows from active and retained companies, preserving archived companies as `archived` where policy requires historical evidence. Because the current `companies` ORM uses soft-delete/status but also has legacy cascade delete paths, positive registry creation must require the chosen active/retained predicate, and security evidence must use archive-only or an explicitly protected deletion policy.
2. Insert cabinet rows only after company ownership/membership is proven. Ambiguous `company_id`, `owner_company_id`, or missing active relation becomes quarantine; nullable mirrored cabinet anchors are never sufficient by themselves.
3. Insert project rows only after `projects.company_id`, `projects.cabinet_id` and registry anchors agree, and the active cabinet-company membership/owner relation is proven. Do not infer a project from workspace name or selected UI state.
4. Insert app-definition owner links before dynamic grants/instances: each `dynamic_app_definitions.tenant_id` must resolve to exactly one retained `companies.id` and expose the required `(id, tenant_id)` owner key for downstream composite FKs. Ambiguous, orphaned, deleted or cross-tenant definitions become quarantine.
5. Insert plugin-instance rows only after the app definition/version composite relation, exact tenant/scope, artifact digest and runtime state are proven. A missing runtime binding is `draft`/`quarantined` by policy, never `published`.
6. Validate registry uniqueness and parent anchors, then add/validate composite FKs in dependency order.

### Phase M02: dynamic grants and dependent data

Backfill through the adapter, one source row per idempotent transaction. Resolve definition/version within the canonical tenant, preserve capability identity and constraints, and require registry scope proof. Denied, expired-without-provenance, cross-tenant, malformed, unsupported or duplicate/conflicting entries never enter the positive grant table.

Quarantine is visible, counted, retryable and fail-closed. Each row stores source identity, schema version, reason code, payload digest, partially resolved anchors, first/last observation and migration checkpoint, but no secrets or raw tokens. Unknown quarantine for a tenant/app/instance partition blocks its cutover.

### Phase M03: parity and cutover

Shadow compare legacy and target decisions on exact tenant, cabinet, project, subject, session, app/version, plugin instance, capability, operation/resource/domain and lifecycle. Require zero unexplained mismatches, zero unresolved positive bindings and a recorded checkpoint. Stop legacy writes, fence in-flight changes, apply final delta, then switch the gateway to the canonical owner. Keep `dynamic_app_grants` read-only as compatibility projection during the rollback window; never run two authorization owners concurrently.

## 7. Positive-write gates

A canonical positive grant, instance, resource binding or tenant-owned write is allowed only when all applicable gates pass:

1. authenticated principal and transaction-local `app.tenant_id` resolve to one non-quarantined `companies.id`;
2. requested legacy `company_id`/scope assertions, if present, equal server-resolved typed anchors;
3. `(scope_kind, scope_id)` resolves to one active registry row with valid typed parent FKs;
4. cabinet/project parent chain is proven, including the project-company and cabinet access relation;
5. dynamic app version has composite same-definition proof, immutable artifact digest and valid lifecycle;
6. plugin call has exact `plugin_instance_id`, tenant/scope, artifact and active runtime generation when policy requires it;
7. subject/session binding, capability identity, bounded operations/resources/domains/constraints and lifecycle are valid;
8. mapping row is idempotent, payload digest matches, no conflicting source row exists, and immutable audit is written in the same transaction boundary;
9. RLS `WITH CHECK` and all required service policy checks succeed.

Any missing anchor, stale generation, unknown mapping, unsupported constraint, quarantine state or audit failure is deny/quarantine. There is no fallback to legacy JSONB, requested capabilities, wildcard scope or an adapter-only ownership assertion.

## 8. Rollback and failure handling

### Before positive cutover

Disable registry/grant backfill for the affected partition, leave legacy reads/writes authoritative, and retain all target rows, mappings, quarantine and audit evidence. Repair through a forward mapping and rerun parity. Do not delete target rows to hide conflicts.

### After canonical read cutover

There must be one owner. Fence the canonical writer generation, stop new issue/revoke operations for the affected partition, and default incomplete reconciliation to deny. Continue authorization from the last valid canonical snapshot while repairing with forward canonical commands and the same idempotency keys. The legacy table remains a read-only projection and is not restored as a second authorization owner.

### Schema downgrade

A downgrade is permitted only before tenant data import and only after a dependency/row-count preflight proves no registry, grant, mapping, audit or quarantine references. After import, use forward corrective revisions. Never drop companies, typed anchors, registry evidence or security history as an operational rollback.

## 9. Open risks and implementation boundary

- `companies` currently has no explicit `tenants` alias table; future external tenant identity would require a separate, parity-driven migration.
- The current `companies`/membership ORM has soft-delete/status fields but legacy hard-delete cascade paths; registry/security evidence therefore requires archive-only or explicitly protected deletion semantics before relying on `RESTRICT` owner anchors.
- `dynamic_app_definitions.tenant_id` currently has no FK to `companies.id`, and its dynamic-app type is wider (`String(80)`) than `companies.id` (`String(40)`); the foundation must align the physical type, validate all values, and add/validate a real owner key such as `(id, tenant_id)` (or an equivalent trusted relation) before definition/grant positive writes.
- Cabinet-company membership is not encoded as a single composite FK in the current ORM; the foundation revision must choose a typed relation or a reviewed consistency trigger/service transaction. Separate project FKs to company and cabinet do not prove parent compatibility.
- `dynamic_app_instances` currently has no runtime generation/plugin subject relation sufficient for all v1 calls; registry creation alone does not close that binding gap.
- Existing canonical grant ORM has a simple version FK and no capability/subject/plugin columns; positive writes remain gated until the target DDL and composite keys are implemented.
- Registry creation must be ordered to avoid a FK cycle with plugin instances: tenant/cabinet/project anchors first, definition owner key and instance unique key/runtime relation second, grant composite FK third.

Until these prerequisites and gates are accepted, this document is a target contract only; no shared-environment DDL or positive canonical write is authorized.
