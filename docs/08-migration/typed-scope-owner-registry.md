# Typed scope and owner registry

Статус: migration-stage target design, только документация. Baseline: 2026-11-01.

Документ закрывает foundation blockers для canonical tenant registry и typed owner relation. Он согласован с [`canonical-tenant-scope-registry.md`](canonical-tenant-scope-registry.md), [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md), [`dynamic-app-migration-gaps.md`](dynamic-app-migration-gaps.md), [`dynamic-grants-binding-gaps.md`](dynamic-grants-binding-gaps.md) и [`dynamic-grants-ownership.md`](dynamic-grants-ownership.md). Он сверяет текущие ORM-модели dynamic apps, identity, cabinets и projects, но не изменяет SQLAlchemy-модели, Alembic revisions, gateway или core docs. Tests/build/lint/format/compile/migration/git checks для этой документационной задачи намеренно не выполняются.

## 1. Decision and boundary

### 1.1 Canonical tenant anchor

Canonical tenant key — `companies.id`, логически называемый `tenant_id` в новых контрактах, RLS GUC и target DDL.

- Физический тип и формат `tenant_id` должны быть идентичны `companies.id`: `String(40)`, не текущему `String(80)` dynamic-app columns.
- `tenants.id` не вводится как скрытая вторая identity. Отдельная `tenants` таблица возможна только отдельной parity-driven foundation migration.
- `company_id` остаётся legacy/source vocabulary. Он разрешается через authenticated ownership и сохраняется только как provenance/diagnostic; новые FK, RLS predicates и canonical authorization queries используют `tenant_id`.
- Нельзя принимать `company_id` из payload, plugin или request как authority и нельзя делать implicit cast из `String(80)` в `String(40)`.

### 1.2 Registry purpose

`scope_registry` — стабильная typed identity для authorization scope и owner anchors. Она устраняет ложное предположение, что пара `(scope_kind, scope_id)` является polymorphic FK. Одна logical scope имеет одну registry row и один набор согласованных parent anchors.

Normative scope kinds:

```text
tenant | cabinet | project | plugin_instance
```

Каждый registry row содержит как минимум:

| Anchor | Required meaning |
|---|---|
| `scope_kind`, `scope_id` | Typed logical identity; pair is unique and never inferred from JSONB. |
| `tenant_id` | Always non-null FK to `companies.id`; tenant scope has `scope_id = tenant_id`. |
| `cabinet_id` | FK to `cabinet_instances.id`; required for cabinet/project scopes and for installations with a cabinet parent. |
| `project_id` | FK to `projects.id`; required for project scope and project-installed instances. |
| `plugin_instance_id` | FK to the exact instance registry; required only for plugin-instance scope and equal to `scope_id`. |
| `owner_state` | `active`, `archived`, or `quarantined`; only `active` supports positive authorization writes. |
| `source_kind`, `source_id`, `schema_version`, `payload_digest` | Immutable migration provenance, not policy input. |

The exact physical table name is a foundation implementation choice, but the typed shape, unique keys, FK behavior, and gates below are normative.

### 1.3 Typed shapes

```text
tenant:
  scope_id = tenant_id
  tenant_id != NULL
  cabinet_id = NULL, project_id = NULL, plugin_instance_id = NULL

cabinet:
  scope_id = cabinet_id
  tenant_id != NULL, cabinet_id != NULL
  project_id = NULL, plugin_instance_id = NULL

project:
  scope_id = project_id
  tenant_id != NULL, cabinet_id != NULL, project_id != NULL
  plugin_instance_id = NULL

plugin_instance:
  scope_id = plugin_instance_id
  tenant_id != NULL, plugin_instance_id != NULL
  cabinet_id/project_id mirror the installation scope; both may be NULL only for an explicitly allowed tenant installation
```

A database check must reject impossible combinations, including a tenant with a cabinet anchor, a project without a cabinet, or a plugin row whose `plugin_instance_id` differs from `scope_id`. A plugin instance never silently inherits a project scope. Tenant-scoped installation is valid only under an explicit dynamic-app policy.

## 2. Existing model reconciliation

### 2.1 Identity and tenant type

`companies.id` is currently `String(40)` with soft-delete `deleted_at` and visible `status`; memberships reference it with `ON DELETE CASCADE`. This is the canonical parent, but the current cascade behavior conflicts with retaining authorization evidence. Before registry FKs become authoritative:

1. inventory all company IDs and prove every candidate dynamic-app tenant value fits the `String(40)` contract;
2. quarantine overlong, malformed, missing, duplicate or ambiguous values;
3. choose archive-only owner semantics or add an explicitly protected deletion boundary;
4. do not rely on the existing hard-delete cascade to preserve registry, grant, mapping, audit or quarantine evidence.

Membership is a subject/tenant relation, not a scope registry row. Active membership and role remain separate checks for human authorization.

### 2.2 Cabinets and company compatibility

`cabinet_instances.id` is `String(40)`. Its `company_id` and `owner_company_id` are nullable legacy/mirrored anchors with `SET NULL`; neither alone proves tenant ownership. Cabinet access is many-to-many through `cabinet_company_grants(cabinet_id, company_id)`, whose current rows have independent FKs and an active/status field.

The target relation is therefore:

```text
cabinet_registry.cabinet_id -> cabinet_instances.id
cabinet_registry.tenant_id  -> companies.id
EXISTS active cabinet_company_grants(cabinet_id, company_id)
  OR an explicitly approved owner relation
```

A nullable cabinet mirror may be retained for compatibility, but it is diagnostic only. If the selected policy requires a single owner, the foundation must materialize a typed owner relation with a unique `(cabinet_id, tenant_id)` key. If shared cabinet access remains valid, the registry row is still one tenant-specific membership/ownership assertion and must not be synthesized from `company_id` alone.

### 2.3 Projects and parent compatibility

`projects.id`, `projects.company_id` and `projects.cabinet_id` are all `String(40)` with separate FKs. Those independent FKs do not prove that the selected company is allowed to use the selected cabinet. A project registry row is eligible only when all of the following agree:

```text
projects.id              = registry.project_id
projects.company_id      = registry.tenant_id
projects.cabinet_id      = registry.cabinet_id
active cabinet/company relation exists
project status is retained and policy-eligible
```

The implementation must expose a real compatibility key, preferably a unique parent tuple such as `(id, company_id, cabinet_id)` and a composite child/registry FK, or an equivalent typed ownership relation whose consistency is enforced in the same trusted transaction. A pair of simple FKs is not an acceptable substitute. Project child tables inherit ownership through their project parent; copied tenant/cabinet columns require equality proof and parent `EXISTS` checks.

## 3. Dynamic-app owner relation

### 3.1 Definition owner anchor

Current `dynamic_app_definitions` uses `id`, `tenant_id`, `scope_kind`, `scope_id` as `String(80)` and has no FK from `tenant_id` to `companies.id`. It also permits only `tenant | cabinet | project` scope kinds. The target foundation must:

1. migrate `tenant_id` to the exact `companies.id` physical type/length after value inventory;
2. add a real FK `dynamic_app_definitions.tenant_id -> companies.id` with archive/restrict semantics;
3. expose a unique owner key `(id, tenant_id)` for downstream composite FKs;
4. resolve definition scope through `scope_registry`, proving scope tenant equals definition tenant;
5. add/validate scope-shape consistency: tenant definitions have no cabinet/project parent, cabinet definitions have the matching cabinet, and project definitions have matching cabinet/project parents;
6. quarantine orphan, deleted, cross-tenant, overlong or ambiguous definitions before any positive grant/instance write.

`app_id` remains a stable app identity within `(tenant_id, app_id)`, not an owner FK. `scope_id` remains a typed lookup key, never a generic FK. `current_version_id` and version ownership retain the existing same-definition composite pattern and FK-cycle staging rules.

### 3.2 Version and definition proof

All grant and instance paths require:

```text
(app_version_id, app_definition_id)
  -> dynamic_app_versions(id, app_definition_id)
(app_definition_id, tenant_id)
  -> dynamic_app_definitions(id, tenant_id)
```

The simple `app_version_id -> dynamic_app_versions.id` relation is insufficient. `app_id` and `manifest_version` are lookup/provenance values; the adapter resolves them inside the already authenticated definition and checks immutable artifact digest and lifecycle.

The current `DynamicAppCapabilityGrantRow` does not satisfy this target: its `id`, `app_version_id`, `tenant_id` and `scope_id` are `String(80)`, `app_version_id` has only a simple FK, and that FK currently uses `ON DELETE CASCADE`. It also lacks `app_definition_id`, cabinet/project anchors, capability identity, subject binding, plugin-instance binding and generation/fencing fields. The foundation revision must inventory and align these columns before positive writes, replace the simple/cascading version relation with the reviewed same-definition `RESTRICT` relation, and quarantine rows that cannot be represented. The legacy `AppGrantRow` has a composite version-definition FK but also retains cascading owner FKs; it remains a read-only projection and must not be treated as canonical evidence.

## 4. Plugin instance subject and runtime generation

### 4.1 Instance identity

Current `dynamic_app_instances` has `id`, definition/version composite proof, `scope_kind`, `scope_id`, status and `runtime_ref`, but no canonical tenant column, exact registry relation, subject/session binding, artifact digest field or runtime generation/fencing key. Registry creation alone does not close this gap.

The target instance relation must expose a unique key sufficient for the grant FK:

```text
(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)
```

The instance row or a dedicated runtime-instance table must additionally retain:

| Field | Contract |
|---|---|
| `plugin_instance_id` | Stable exact installation identity; never substitute app, version, scope or session ID. |
| `tenant_id`, scope anchors | Resolved from registry; same-definition and same-tenant proof required. |
| `artifact_digest` | Equals immutable version artifact digest. |
| `subject_kind`, `subject_id` | Authenticated human/service binding when policy requires an instance-bound subject. |
| `session_id` | Active call/runtime boundary; not a substitute for subject or instance identity. |
| `runtime_generation` | Monotonic generation for start/restart/redeploy; stale generations deny. |
| `writer_generation` | Fencing epoch for issue/revoke/runtime workers; stale workers cannot mutate. |
| `status` | At minimum `draft`, `published`, `disabled`, `revoked`, `quarantined`; only policy-eligible published state can authorize. |
| runtime evidence | `runtime_ref`, started/stopped times, heartbeat/lease or equivalent, artifact and process provenance without secrets. |

A call is positive only if the gateway resolves the exact instance and verifies definition, version, tenant, scope, artifact, subject/session and current runtime generation. A stale, duplicated, missing, cross-tenant or quarantined binding is deny and/or quarantine, never fallback to app-wide permission. `GrantSet` has no instance ID, so imported instance-scoped entries remain quarantined until this relation is enforceable.

### 4.2 Composite grant relation

For instance-bound canonical grants, the required relation is conceptually:

```text
(plugin_instance_id, app_definition_id, app_version_id, tenant_id,
 scope_kind, scope_id)
  -> dynamic_app_instances(id, app_definition_id, app_version_id,
                           tenant_id, scope_kind, scope_id)
```

The instance key must itself resolve all scope anchors through `scope_registry`. The plugin-instance relation is cyclic: the plugin registry row points to the instance, while the instance resolves its typed scope through the registry. Stage it explicitly: create tenant/cabinet/project registry rows and the instance table with nullable or deferred registry linkage; create/validate the instance unique key and runtime fields; backfill plugin-instance rows only with exact anchors; then add/validate the registry-to-instance and grant-to-instance composite FKs in that order. If the prerequisite instance table cannot expose this key in the first revision, stage the grant FK after the registry and unique instance key; positive writes begin only after both relations validate. An adapter assertion is not a permanent replacement for the database relation.

## 5. Status, archive and deletion policy

### 5.1 Registry states

`owner_state` is independent from product/runtime status:

| Registry state | Reads | Positive writes | Meaning |
|---|---|---|---|
| `active` | Allowed when RLS/context/parent checks pass | Allowed | All typed parents and lifecycle gates are valid. |
| `archived` | Migration/security/audit roles only, or historical read policy | Denied | Owner is retained for evidence; new grants/instances are forbidden. |
| `quarantined` | Migration/security roles only | Denied | Anchor, type, compatibility, digest or provenance is unresolved. |

Dynamic app definitions, versions and instances retain their existing `draft | published | disabled | revoked` lifecycle with explicit transitions. `disabled`, `revoked` and expired rows deny at authorization time and are not destructive deletes. A published definition/version/instance cannot be silently rewritten to repair ownership; create a forward transition and audit event.

### 5.2 Hard-delete policy

Default policy for tenant, cabinet, project, definition, version, instance and security evidence is archive-only plus `ON DELETE RESTRICT` on registry/security FKs. Existing `ON DELETE CASCADE` paths in companies, cabinets, projects and dynamic-app definitions must not be allowed to erase registry or authorization evidence after import.

Hard delete is permitted only for an explicitly approved pre-import/orphan cleanup class, after a dependency preflight proves:

```text
no active or historical registry reference
no grant/version/instance reference
no mapping, quarantine or audit evidence requiring the row
no retained source/provenance dependency
retention and export requirements satisfied
```

Derived operational children may use `CASCADE` only where the target map expressly allows it. Registry rows, grants, instances, mappings, quarantine and audit evidence use `RESTRICT`, archive transitions, or `SET NULL` only for optional evidence links. Operational rollback is a forward state transition, never deletion of owner rows.

## 6. Composite FK and consistency strategy

### 6.1 Required keys

The foundation revision must make the following keys real and unique before positive canonical writes:

```text
companies(id)
dynamic_app_definitions(id, tenant_id)
dynamic_app_versions(id, app_definition_id)
cabinet_instances(id)
projects(id, company_id, cabinet_id)       -- or equivalent typed owner relation
scope_registry(scope_kind, scope_id, tenant_id, cabinet_id, project_id, plugin_instance_id)
dynamic_app_instances(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)
```

A grant/resource/runtime table either uses a fixed typed owner contract with separate FKs or references the registry through a composite key. A generic FK on `scope_id` to one arbitrary parent table is forbidden.

### 6.2 Parent `EXISTS` is still required

Composite FKs prove row identity but do not by themselves prove lifecycle or authorization eligibility. Trusted writes and RLS policies must use parent `EXISTS` checks for:

- retained/active company status and `deleted_at IS NULL` where policy requires it;
- active cabinet status and active cabinet-company grant/owner relation;
- project status plus exact `(project.company_id, project.cabinet_id)` compatibility;
- definition status, tenant owner and scope registry state;
- version status, immutable digest and same-definition owner;
- instance status, artifact digest, subject/session binding and current runtime generation.

Copied tenant/cabinet/project columns are lookup/index aids only. Child values cannot widen access or replace the parent `EXISTS` proof.

## 7. RLS context and policy shape

Every tenant-owned target table uses transaction-local settings resolved by the server:

```text
app.tenant_id  = companies.id
app.cabinet_id = optional cabinet_instances.id
app.project_id = optional projects.id
app.user_id    = authenticated subject/employee
app.role       = resolved role
```

Missing, malformed or client-only context denies. Values are cleared at transaction end. `company_id`, tenant, cabinet, project, subject and instance claims from a client/plugin are assertions to compare, never authority.

New tenant-owned tables use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, with both `USING` and `WITH CHECK` policies. Normative policy shape:

```sql
tenant_id = current_setting('app.tenant_id', true)
AND EXISTS (
  SELECT 1 FROM scope_registry sr
  WHERE sr.scope_kind = target.scope_kind
    AND sr.scope_id = target.scope_id
    AND sr.tenant_id = current_setting('app.tenant_id', true)
    AND sr.owner_state = 'active'
)
```

Cabinet and project policies additionally match the corresponding GUCs and require active parent/membership `EXISTS`. Plugin policies match the exact instance, definition/version, runtime generation and subject/session gate. Audit, mapping and quarantine use narrower migration/security roles; unresolved rows are migration-admin only. Registry reads of archived/quarantined rows must not leak positive authorization and must be separated from ordinary tenant reads.

RLS is not a substitute for gateway checks: exact app/version, capability, operation/resource/domain, subject, runtime generation, lifecycle and artifact must be verified before adapter invocation. Gateway checks are not a substitute for typed FKs and RLS.

## 8. Backfill, quarantine and cutover gates

The M00-M06 labels below are the gates for this typed owner/registry track. They are not a replacement for the separate dynamic-app DDL M-order, where M06 denotes staged JSONB checks; evidence must be cross-referenced by scope and migration revision rather than by gate number alone.

### Gate M00: inventory and type alignment

- Inventory companies, memberships, cabinet instances/grants, projects, definitions, versions, grants, instances, runtime/session bindings and all tenant producers.
- Verify every dynamic-app tenant/scope/ID value against `String(40)` anchors; produce overlength, orphan, duplicate, deleted and cross-parent counts.
- Record source IDs, lifecycle, artifact/payload digests and parent tuples. Do not guess tenant from names, slugs or UI state.
- Create empty registry, mapping, quarantine and audit structures before positive writes.

### Gate M01: tenant/cabinet/project registry backfill

- Insert retained company rows as `active` or `archived` under the chosen status/deleted predicate.
- Insert cabinet memberships only when active cabinet/company relation or an approved owner relation is proven; nullable mirror columns alone quarantine.
- Insert projects only when project company, cabinet, registry and active cabinet access agree.
- Validate registry unique identity and typed shape. No positive grant/instance import while a required parent partition is ambiguous.

### Gate M02: definition/version owner foundation

- Align `dynamic_app_definitions.tenant_id` to `companies.id` and add/validate `(id, tenant_id)`.
- Resolve every definition scope through the registry and same tenant. Orphan/deleted/cross-tenant/overlong definitions quarantine.
- Validate immutable versions, artifact digests and `(app_version_id, app_definition_id)` before current-version or grant imports.
- Add current-version FK only after the known FK cycle and invalid historical rows are repaired/quarantined.

### Gate M03: instance/runtime foundation

- Backfill exact instance key with definition/version/tenant/scope proof.
- Validate artifact digest, runtime state, subject/session binding and runtime generation/fencing evidence.
- Mark missing runtime evidence `draft`, `disabled` or `quarantined` according to approved policy, never `published` by inference.
- Add composite instance key/FK before enabling instance-bound canonical grants.

### Gate M04: grants and dependent data

- Import through the adapter one source row per idempotent transaction, preserving source identity, canonical payload digest and checkpoint.
- Require registry scope, same-definition version, definition tenant, exact subject and instance/runtime proof where applicable.
- Keep denied, expired-without-provenance, unsupported, duplicate/conflicting, cross-tenant and malformed entries out of positive tables.
- Store quarantine reason, resolved anchors, first/last observation and checkpoint without secrets or raw tokens. Same source identity with a different digest is a conflict, not overwrite.

### Gate M05: parity and bounded cutover

Shadow compare legacy and target decisions on exact tenant, cabinet, project, definition/version, subject, session, plugin instance, runtime generation, capability, operation/resource/domain and lifecycle. Cutover requires:

```text
zero unexplained parity mismatches
zero unresolved positive bindings
zero unknown mappings for the partition
valid RLS/FK/parent EXISTS evidence
recorded source/target digest checkpoint
fenced legacy writer and drained final delta
```

Cut over by tenant + app definition, optionally narrower scope/instance partition. Stop legacy writes, fence in-flight mutations, apply final delta, switch canonical reads/writes, and retain `dynamic_app_grants` as read-only compatibility projection. Never run two authorization owners concurrently.

### Gate M06: cleanup eligibility

Legacy cleanup is a later forward migration and requires complete mapping or explicit owner-approved quarantine/retirement for every retained source row, valid audit replay/retention, no legacy authorization reader/writer, and an elapsed rollback window. Mapping, quarantine and audit evidence survive cleanup.

## 9. Failure handling and unresolved blockers

Before canonical read cutover, stop the affected backfill partition and leave legacy authoritative while retaining target rows/evidence. After canonical cutover, keep canonical ownership, fence the writer generation, default incomplete reconciliation to deny, and repair through forward canonical commands. Do not restore legacy writes as a second owner and do not delete rows to hide conflicts.

The following remain implementation blockers until the foundation revision and reviewed migration close them:

- current dynamic-app owner/scope IDs are `String(80)` while `companies`, cabinets and projects use `String(40)`;
- `dynamic_app_definitions.tenant_id` has no FK/owner composite key;
- cabinet/company access and project/company/cabinet compatibility are separate relations, not one enforced parent tuple;
- current instances lack canonical tenant, exact plugin subject/session relation and runtime generation/fencing fields;
- existing cascade delete paths can erase owners or evidence unless archive/restrict policy is installed;
- current canonical grant/instance DDL must be staged after registry and composite instance keys, not enabled by adapter assertions alone.

Until these blockers, gates and parent `EXISTS` policies are accepted and implemented in a separate code/migration PR, this document is target design only; no shared-environment DDL or positive canonical write is authorized.

## 10. Verification freeze

This is a documentation-only migration artifact. No tests, build, lint, format, compile, migration execution, or git checks are run. Verification remains frozen until the migration-wide implementation and review explicitly lift the freeze.
