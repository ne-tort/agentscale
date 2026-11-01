# Dynamic grants binding and audit gaps

Статус: migration-stage design/gap artifact, только документация. Baseline: 2026-11-01.

Документ описывает target envelope и обязательные границы переноса declarative-app grants. Он сверяет текущий capability gateway, dynamic-grants ownership, dynamic app models и declarative-app v1 protocol. Документ не меняет код, SQLAlchemy-модели, Alembic revisions или существующие core docs. Tests/build/lint/compile/git checks для этой задачи намеренно не выполняются.

## 1. Current contract и target decision

Текущий `CapabilityGatewayService` принимает `RequestContext` с `app_id`, `manifest_version`, legacy `company_id`, `subject_id`, optional `project_id`/`cabinet_id`/`session_id`/`trace_id`/`request_id`, а запрос добавляет `capability`, `operation`, `resource`, optional `domain` и quota bytes. Gateway проверяет protocol capability, requested capability из manifest, identity, scope, in-memory `CapabilityGrant`, quota и typed adapter; при любом пропуске доступ запрещается. Его `AuditEvent` пока содержит request/app/company/subject/capability/operation/resource/decision/code/grant, но не tenant, cabinet, project, session, app version id, plugin instance или нормализованный resource/trace envelope. MCP gateway envelope дополнительно несёт `span_id`, `profile_id`, tool server/name/version, timestamp и redacted arguments digest/result status/error/duration; эти поля должны быть сохранены или явно связаны с capability-аудитом, а MCP tool identity нельзя смешивать с declarative capability name.

Target authorization owner после cutover — `dynamic_app_capability_grants`, как зафиксировано в `dynamic-grants-ownership.md`. `dynamic_app_grants` остаётся read-only compatibility projection на rollback window. Однако текущая canonical row и `PersistenceCapabilityGrant` всё ещё не имеют `app_definition_id` для composite same-definition proof, `capability`, `subject_id` или `plugin_instance_id`; простой FK только на `dynamic_app_versions.id` не доказывает ownership definition. Поэтому наличие row не является достаточным доказательством v1 user/plugin-scoped allow. Adapter и gateway должны enforce missing bindings deny/quarantine до появления target schema/key.

Target invariant:

```text
request -> authenticated identity/context -> resolved app/version/tenant/instance
         -> requested capability + canonical grant identity
         -> scope/subject/instance/policy checks -> operation/resource constraints
         -> adapter invocation -> immutable audit envelope
```

Manifest `requestedCapabilities` — requested contract, не grant. `GrantSet` — protocol result/provenance, не самодостаточный authorization token: его app/version/grants недостаточно для определения tenant, cabinet, project, user, session или concrete plugin instance.

## 2. Target audit envelope

Каждая authorization attempt, issue/import/revoke/expiry observation, adapter failure, denied decision, quarantine, cutover and rollback event получает единый immutable envelope. Envelope строится из authenticated gateway context и server-side resolution; client/plugin input может быть только сопоставлен и проверен, но не может подменить ownership fields.

| Field | Required target meaning | Source and enforcement |
|---|---|---|
| `tenant_id` | Canonical tenant boundary | Resolve from authenticated principal/session and app-definition ownership; never trust a copied `company_id` or plugin value. |
| `cabinet_id` | Cabinet boundary, nullable only for tenant-scoped operations | Resolve from session/ownership relation; require it for cabinet/project context and check tenant parent. |
| `project_id` | Project boundary, nullable only where policy permits | Resolve from authenticated context; verify cabinet and tenant parent. |
| `user_id` / `subject_id` | Human/service subject making the call | Bind to authenticated principal, not `GrantSet` or request body. Preserve both only if `subject_id` is a stable authorization key and `user_id` is actor provenance; otherwise define one canonical representation. |
| `session_id` | Runtime/session authorization boundary | Resolve from running session and JWT/session claims; reject stale, absent or cross-tenant session. |
| `app_id` | Stable app-definition identity | Load from resolved `dynamic_app_definitions`; compare request and manifest `appId`. |
| `app_version_id` | Immutable version FK used for authorization | Resolve from definition plus `manifest_version`; never authorize by version string alone. |
| `manifest_version` | Protocol/display lookup and evidence | Preserve in envelope/provenance, but do not use as canonical FK or unique authorization identity. |
| `plugin_instance_id` | Exact isolated plugin/runtime installation | Resolve from `dynamic_app_instances` plus runtime/session binding; require it for plugin calls. A manifest plugin entrypoint alone does not identify a running instance. |
| `capability` | Declarative capability identity, e.g. `storage.files` | Preserve v1 `GrantSet.name` exactly; compare to manifest request and gateway registry. Never infer it from operation name. |
| `operation` | Concrete method/action being requested | Normalize and compare against canonical operations and protocol `constraints.methods`. Absence of methods is not wildcard. |
| `resource` | Exact or policy-defined resource identity | Normalize and compare against grant resources; no implicit global resource. |
| `domain` | Domain boundary used by current gateway/domain grant | Preserve and enforce exact match where required; unsupported domain semantics quarantine. |
| `trace_id` | Distributed trace/correlation identity | Propagate validated trace; generate server-side when absent. `request_id` and span/idempotency keys remain separate fields. |
| `decision` / `reason` | Allow/deny plus stable denial/quarantine code | Use gateway decision codes and persistence deny reasons; denied protocol entries remain outcomes, never positive rows. |
| `grant_id`, `instance`, `source_id` | Canonical and compatibility provenance | Include canonical grant id, source GrantSet/legacy id, instance id and mapping/schema version where present. |
| `occurred_at`, `issued_at`, `expires_at`, `revoked_at` | UTC, timezone-aware lifecycle times | Preserve source times; invalid or missing required provenance is quarantine, not import-time substitution. |
| `payload_digest`, `schema_version`, `redaction` | Replay/parity evidence without secrets | Hash canonical sorted payload; retain protocol/schema version and redaction marker, never raw tokens or secret arguments. For MCP calls, bind this to the envelope's canonical argument digest and retain `span_id`, `profile_id`, tool server/name/version, result status, error code and duration without storing raw arguments. |

The envelope must cover both deny before adapter invocation and deny after adapter lookup/failure. Audit insertion must be append-only and tenant-scoped; an audit write failure cannot turn a denied request into allow. For an allow, the envelope records the exact resolved version, tenant/scope, subject, instance and grant identity, not merely request strings.

## 3. Subject and plugin-instance binding

### 3.1 Subject binding

The current in-memory `CapabilityGrant` binds `app_id`, `manifest_version`, `subject_id` and `capability`, then operation/resource/domain. The persistence contract binds app/version/tenant/scope and lifecycle, but has no subject. These are not interchangeable scopes. A tenant/cabinet/project grant may be a policy ceiling, while an effective call still requires subject authorization from the authenticated gateway context.

Target binding rules:

1. `subject_id` is derived from verified session/JWT identity and is immutable for the request. A caller-supplied subject is an assertion to compare, never an authority source.
2. The adapter must decide explicitly whether a grant is `subject-bound`, `scope-bound`, or both. A subject-bound v1 decision cannot be imported into a row that only has tenant/scope columns.
3. A target subject binding must distinguish a specific subject from a bounded service/principal class. Empty/null means unbound only when policy explicitly permits a scope grant; it must not silently mean all users.
4. Authorization compares tenant, cabinet, project, subject, app, exact app version, capability, operation, resource and domain before adapter invocation. Cross-tenant or cross-scope mismatch is deny and audit.
5. Subject revocation/session termination must deny without deleting historical grants or audit records.

### 3.2 Plugin-instance binding

The v1 manifest can declare an isolated plugin entrypoint (`runtime: sandbox-v1`, artifact digest and RPC methods), but `GrantSet` has no instance identifier. `AppInstanceRow` identifies an installation by app definition, scope and version, but current fields do not express a stable runtime/plugin instance binding to subject/session or artifact process. Therefore a GrantSet cannot be treated as permission for every instance of an app.

The adapter/gateway must:

- resolve `plugin_instance_id` from a server-side instance/runtime registry and verify it belongs to the resolved app definition, exact `app_version_id`, tenant and scope;
- verify artifact digest and runtime generation against the immutable version and the running session;
- bind the instance to the authenticated session and subject for the call, including stop/revoke/quarantine state;
- reject a missing, stale, duplicated or cross-tenant instance binding;
- preserve instance id and runtime generation in the audit envelope;
- quarantine imported instance-scoped grants until the target instance key and relation are enforceable.

A plugin instance is not equivalent to `app_id`, `app_version_id`, `scope_id` or a user session. The migration must not collapse those identities to make a composite FK fit.

## 4. `manifest_version` and company-to-tenant resolution

`manifest_version` is a version string. Resolve it only inside the already authenticated app definition:

```sql
SELECT id
FROM dynamic_app_versions
WHERE app_definition_id = :app_definition_id
  AND manifest_version = :manifest_version;
```

The current model's `UNIQUE (app_definition_id, manifest_version)` (`uq_dynamic_app_version_manifest_version`) makes this lookup deterministic when data is valid. The adapter then checks:

- resolved version's `app_definition_id` equals the resolved definition;
- definition `app_id` equals request and manifest `appId`;
- manifest `version` equals the lookup value;
- artifact/status and instance references are compatible with the call;
- no second row or orphan/cross-definition result is observable.

`company_id` in current gateway context is a legacy naming boundary, not canonical tenant identity. The adapter resolves it through the authenticated ownership anchor (session/principal, cabinet/project relation or app definition tenant) to `tenant_id`, and requires all independent anchors to agree. It must reject missing, ambiguous, cross-tenant or client-only mappings. It must never query a version by `manifest_version` alone, copy `company_id` into `tenant_id` without ownership verification, or let a request tenant override the definition/session tenant.

Target audit retains `company_id` as source/provenance only when needed for migration diagnostics; authorization and FK paths use `tenant_id`.

## 5. GrantSet protocol limitations and identity preservation

Declarative v1 defines:

```text
GrantSet(appId, manifestVersion, grants[], issuedAt, expiresAt?)
CapabilityGrant(name, decision, constraints?, expiresAt?, revokedAt?, grantedBy)
```

A grant entry preserves capability `name`, decision and constraints: `resources`, `methods`, `domains`, `eventTypes`, and `maxBytesPerRequest`. The current canonical persistence shape has `operations`, `resources`, `domains`, `constraints`, but no capability column; `PersistenceCapabilityGrant.authorize` also has no capability argument. Therefore the following transformations are forbidden:

- dropping `name` or storing it only in unvalidated JSON metadata;
- merging entries for different capabilities into one row;
- treating `name` as an operation/resource or deriving it from a method;
- expanding absent `methods` into unrestricted operations;
- importing `decision: denied` as a positive grant;
- dropping `eventTypes`, `maxBytesPerRequest` or other constraints that the adapter cannot enforce;
- treating requested manifest capability as proof of an allow.

Preferred target is one canonical grant identity per capability (or an explicitly reviewed capability namespace/key). That requires a database key/constraint and an authorization method that compares capability identity. Until then, an entry is positive-import eligible only if a reviewed target mapping preserves name, all enforceable constraints, exact app/version and binding context. Otherwise it goes to quarantine with source payload digest and reason. `methods -> operations` is allowed only after a versioned mapping defines normalization and absence semantics; unsupported constraints remain in canonical constraints or force quarantine.

## 6. Denied and quarantine handling

`decision: denied` is a protocol decision and audit/provenance event, not an allow row. It may be retained in a compatibility mapping or denial ledger with `decision`, reason, source digest, issuer, times and binding context, but it must not satisfy an authorization query or active-grant uniqueness key.

Quarantine is mandatory for:

- unresolved or ambiguous app/version/tenant/company mapping;
- orphan or cross-definition/cross-tenant version, scope or instance;
- missing subject or plugin-instance binding where policy requires it;
- unsupported capability identity or constraint semantics;
- malformed JSON, invalid timezone/lifecycle, duplicate active identity or conflicting source digest;
- revoked/expired source state whose actor/provenance cannot be proven;
- any adapter mapping that would broaden permission.

Each quarantine record is visible, counted and retryable. It stores source id, schema version, reason code, payload digest, resolved identities (if any), first/last observed timestamps and migration checkpoint; it does not store secrets/raw tokens. Quarantine is fail-closed: no gateway read may use it, and a quarantine count or unknown mapping blocks the affected tenant/app/instance cutover. Repair is a forward mapping/import with the same idempotency key, not an overwrite or silent skip.

## 7. Canonical FK and unique strategy

The target schema must enforce identity at the database boundary while retaining the current ownership checks. Minimum FK strategy:

1. `dynamic_app_versions` keeps `app_definition_id -> dynamic_app_definitions.id` and unique `(id, app_definition_id)`.
2. Grant and instance rows use a composite `(app_version_id, app_definition_id)` FK to prove same-definition ownership; a simple version FK is insufficient.
3. Canonical grants add an explicit capability identity/key and, when subject/instance binding is materialized, FKs or registry references for tenant, subject and plugin instance. If polymorphic tenant/cabinet/project scope remains, ownership validation and a scope registry must substitute for an impossible generic FK.
4. Plugin instance relation must prove `(plugin_instance_id, app_definition_id, app_version_id, tenant_id)` consistency; runtime/session binding is a service gate in addition to database FK.
5. `tenant_id` must resolve from the parent ownership anchor. A duplicate tenant column may be retained for query/indexing only with a consistency constraint or trusted write path.
6. Audit/quarantine/mapping rows reference source and canonical identities without cascading away history on routine app/grant cleanup.

Canonical active grant identity is the normalized authorization tuple. Preferred unique components are:

```text
(tenant_id, app_version_id, capability_key, subject_binding,
 plugin_instance_binding, scope_kind, scope_id,
 operations_digest, resources_digest, domains_digest, constraints_digest)
```

Each digest is computed from canonical sorted, duplicate-free arrays/object. If policy permits reusable scope grants, explicit sentinel values for unbound subject/instance must be defined; SQL `NULL` uniqueness behavior must not accidentally allow duplicate active grants. Historical revoke/expiry rows remain auditable. Use a generation/mapping key and a partial active uniqueness strategy only after defining how renewals close or supersede an active generation; a moving clock cannot safely be embedded in a PostgreSQL partial index predicate. `SELECT`-then-insert is not sufficient: canonical write uses transactional unique conflict handling and compares payload/binding digests.

A migration compatibility mapping must be unique on `(source_kind, source_id, canonical_schema_version)` and store source/canonical digests, status, quarantine reason, checkpoint and target ids. Same source identity with a different digest is a conflict, never an overwrite.

## 8. Adapter enforcement contract

The adapter is the only compatibility boundary for GrantSet/legacy rows. It must perform, in order:

1. Parse and validate protocol version, required fields, timestamps, decision and constraints.
2. Resolve authenticated subject/session and tenant/cabinet/project; reject client/plugin ownership claims that disagree.
3. Resolve definition by stable `app_id` within tenant and resolve `manifest_version` to `app_version_id` within that definition.
4. Verify manifest `appId`/`version`, immutable artifact/version status, plugin entrypoint and runtime instance/artifact digest.
5. Preserve capability identity and map `methods` to operations only under the approved versioned rule; retain/enforce all constraints.
6. Check scope, subject and plugin-instance binding, lifecycle, expiry and revocation. Fail closed on absent binding or unavailable policy dependency.
7. Emit a canonical authorization decision envelope with source and target provenance; invoke typed adapter only after allow. If invocation then fails or is unavailable, emit a linked adapter-outcome event with the same request/trace correlation and final deny code; do not treat the pre-invocation allow event as successful execution.
8. Write positive canonical grant only through the canonical repository/transaction and compatibility mapping; denied/quarantined entries cannot enter the positive table.

The adapter must be idempotent and deterministic for the same source id/schema version/payload digest. It must distinguish `deny` (known policy outcome), `quarantine` (not safely representable/importable), and adapter/system failure in audit codes. No fallback to legacy JSONB, requested capabilities, wildcard expansion or second policy owner is permitted after canonical ownership.

## 9. Migration gates

### Gate A: contract and inventory

- Freeze the v1 GrantSet parser/schema and enumerate all constraints and denial semantics.
- Inventory definitions, versions, legacy grants, canonical grants, instances, subjects and runtime bindings by tenant/app/version/scope.
- Produce orphan, ambiguity, cross-tenant, duplicate-active, unsupported-constraint and missing-binding counts.
- Approve the target capability identity and subject/instance key shape before DDL.

### Gate B: schema and adapter readiness

- Add reviewed capability identity/key, binding relations or an explicit rejected/quarantine state; do not claim readiness with the current row shape alone.
- Add composite same-definition FK and tenant/scope ownership constraints/indexes.
- Create mapping, quarantine and immutable audit structures before import.
- Implement adapter resolution and enforce deny/quarantine semantics with stable reason codes.

### Gate C: backfill and parity

- Backfill through the adapter in resumable, idempotent transactions; preserve source ids, digests, times and provenance.
- Keep denied entries out of positive grants and quarantine every unrepresentable or unbound entry.
- Shadow-check legacy versus canonical decisions on exact tenant, cabinet, project, subject, session, instance, app/version, capability, operation/resource/domain and lifecycle.
- Any unexplained mismatch, unknown quarantine, or missing binding blocks that partition.

### Gate D: bounded cutover

- Stop new legacy writes for the tenant/app/instance partition and fence in-flight mutations.
- Complete delta backfill, resolve conflicts and record a zero-unknown parity checkpoint.
- Switch gateway reads and decisions to canonical grants; route management commands through the adapter.
- Keep legacy rows read-only as a projection for the rollback window. Never run two authorization owners concurrently.

### Gate E: cleanup

- Demonstrate complete mapping or owner-approved quarantine/retirement for every retained source row.
- Demonstrate no legacy authorization reader/writer, no unresolved binding, no unexplained parity mismatch and valid audit replay/retention.
- Remove legacy ownership only in a later reviewed migration; retain mapping, quarantine and audit evidence. Operational rollback after canonical cutover is forward reconciliation, not restoring legacy writes.

No dynamic-grants DDL or shared-environment migration is authorized until these gates are explicitly accepted. Verification freeze remains active for this documentation-only change.
