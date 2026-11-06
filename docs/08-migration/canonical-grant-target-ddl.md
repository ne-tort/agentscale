# Canonical capability grant target schema contract

Статус: migration-stage target schema contract, только документация. Baseline: 2026-11-01.

Документ закрывает blockers из `dynamic-grants-binding-gaps.md` и `dynamic-grants-ownership.md` на уровне согласованного target DDL. Он не является Alembic-ревизией, не изменяет SQLAlchemy-модели, gateway, declarative-app v1, MCP envelope или core docs. Источник исполняемого DDL после согласования остаётся в моделях и отдельной migration PR.

## 1. Scope и decision

`dynamic_app_capability_grants` — единственный canonical owner effective authorization после bounded cutover. `dynamic_app_grants` остаётся read-only compatibility projection до отдельной cleanup revision.

Manifest `requestedCapabilities` и v1 `GrantSet` — requested/provenance contract, а не allow. Positive grant может появиться только после server-side resolution и проверки:

```text
authenticated gateway context
  -> tenant/cabinet/project + subject/session
  -> app_definition_id + app_version_id (same-definition proof)
  -> plugin_instance_id + generation/fencing (для plugin/runtime call)
  -> capability_key + bounded operations/resources/domains/constraints
  -> lifecycle/revoke + active generation
  -> immutable audit
```

Одна canonical row представляет одну capability для одного binding. Разные `GrantSet.name` не объединяются. `decision=denied`, неразрешимые constraints и неполные bindings не попадают в positive table; они идут в audit/quarantine.

## 2. Existing contracts that constrain the schema

### Current gateway

`CapabilityGatewayService` сейчас получает `app_id`, `manifest_version`, legacy `company_id`, `subject_id`, optional `cabinet_id`/`project_id`/`session_id`, а request добавляет `capability`, `operation`, `resource`, optional `domain`, quota bytes. Identity and scope derive from authenticated context; caller/plugin values are assertions only. Target adapter resolves `company_id` to canonical `tenant_id` and resolves `manifest_version` only under the already resolved definition:

```sql
SELECT id
FROM dynamic_app_versions
WHERE app_definition_id = :app_definition_id
  AND manifest_version = :manifest_version;
```

The current in-memory grant is subject- and capability-bound, while the current persistence row is not. Therefore the current row shape cannot authorize a v1 user/plugin call.

### Declarative-app v1

The immutable version stores `appId`, `version`, artifact digest and requested capabilities. A v1 grant entry contains `name`, `decision`, `methods`, `resources`, `domains`, `eventTypes`, `maxBytesPerRequest`, issuer and lifecycle times. `capability_key` preserves `name` exactly; `methods` are mapped to `operations` only by an approved versioned mapping. Missing methods are not wildcard. Unsupported constraints quarantine the entry.

The v1 implementation contract binds an effective grant to exact app/version, immutable artifact, tenant/cabinet/project, authenticated user, plugin instance and expiry. The plugin cannot mint, refresh, broaden or transfer it.

### MCP envelope and persistence target map

Declarative capability identity and MCP tool identity remain separate. If a call is correlated with MCP audit, the canonical audit record retains `trace_id`, `span_id`, `profile_id`, `tool_server`, `tool_name`, `tool_version`, canonical redacted `arguments_digest`, result status, error code and duration. The MCP lock identity remains project/profile/server/package/allowlist based and is linked as provenance, not converted into a declarative capability name.

Tenant-owned rows use transaction-local `app.tenant_id`, optional `app.cabinet_id`, `app.project_id`, `app.user_id`, `app.role`. Parent ownership anchors must exist before hard FKs are added. Polymorphic scope IDs are not treated as a FK to an arbitrary table.

## 3. Canonical grant table

The following is contract-level PostgreSQL DDL. Names and types must be aligned with the repository's canonical identity-anchor types in the implementation revision; the required relationships and checks are normative.

```sql
CREATE TABLE dynamic_app_capability_grants (
    id                       text PRIMARY KEY,

    -- Stable app identity and same-definition proof.
    app_definition_id        text NOT NULL,
    app_id                   text NOT NULL,
    app_version_id           text NOT NULL,
    app_artifact_digest      text NOT NULL,

    -- Canonical tenant and optional narrower scope. These are resolved server-side.
    tenant_id                text NOT NULL,
    cabinet_id               text NULL,
    project_id               text NULL,
    scope_kind               text NOT NULL,
    scope_id                 text NOT NULL,

    -- Effective v1 identity and runtime binding.
    capability_key           text NOT NULL,
    subject_kind             text NOT NULL,
    subject_id               text NOT NULL,
    plugin_instance_id       text NULL,
    session_id               text NULL,

    -- Canonical bounded policy payload.
    operations               jsonb NOT NULL,
    resources                jsonb NOT NULL,
    domains                  jsonb NOT NULL,
    constraints              jsonb NOT NULL DEFAULT '{}'::jsonb,
    operations_digest        text NOT NULL,
    resources_digest         text NOT NULL,
    domains_digest           text NOT NULL,
    constraints_digest       text NOT NULL,

    -- Lifecycle, generation and fencing.
    issued_at                timestamptz NOT NULL,
    expires_at               timestamptz NULL,
    revoked_at               timestamptz NULL,
    issued_by                text NOT NULL,
    revoked_by               text NULL,
    revoke_reason            text NULL,
    grant_generation         bigint NOT NULL,
    writer_generation        bigint NOT NULL,
    idempotency_key          text NOT NULL,

    -- Provenance only; never raw token/secret material.
    source_kind              text NOT NULL DEFAULT 'canonical',
    source_id                text NULL,
    schema_version           text NOT NULL,
    payload_digest           text NOT NULL,
    audit_metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_cap_grant_version_definition
      FOREIGN KEY (app_version_id, app_definition_id)
      REFERENCES dynamic_app_versions (id, app_definition_id)
      ON DELETE RESTRICT,
    CONSTRAINT fk_cap_grant_definition
      FOREIGN KEY (app_definition_id)
      REFERENCES dynamic_app_definitions (id)
      ON DELETE RESTRICT,
    CONSTRAINT fk_cap_grant_definition_tenant
      FOREIGN KEY (app_definition_id, tenant_id)
      REFERENCES dynamic_app_definitions (id, tenant_id)
      ON DELETE RESTRICT,
    CONSTRAINT fk_cap_grant_instance
      FOREIGN KEY (plugin_instance_id, app_definition_id, app_version_id, tenant_id,
                   scope_kind, scope_id)
      REFERENCES dynamic_app_instances (id, app_definition_id, app_version_id, tenant_id,
                                         scope_kind, scope_id)
      ON DELETE RESTRICT,
    CONSTRAINT ck_cap_grant_scope_kind
      CHECK (scope_kind IN ('tenant', 'cabinet', 'project')),
    CONSTRAINT ck_cap_grant_scope_shape
      CHECK ((scope_kind = 'tenant' AND cabinet_id IS NULL AND project_id IS NULL AND scope_id = tenant_id)
          OR (scope_kind = 'cabinet' AND cabinet_id IS NOT NULL AND project_id IS NULL AND scope_id = cabinet_id)
          OR (scope_kind = 'project' AND cabinet_id IS NOT NULL AND project_id IS NOT NULL AND scope_id = project_id)),
    CONSTRAINT ck_cap_grant_subject_kind
      CHECK (subject_kind IN ('user', 'service', 'role')),
    CONSTRAINT ck_cap_grant_plugin_binding
      CHECK (plugin_instance_id IS NOT NULL OR session_id IS NULL),
    CONSTRAINT ck_cap_grant_capability_nonempty
      CHECK (length(btrim(capability_key)) > 0),
    CONSTRAINT ck_cap_grant_operations
      CHECK (jsonb_typeof(operations) = 'array' AND jsonb_array_length(operations) > 0),
    CONSTRAINT ck_cap_grant_resources
      CHECK (jsonb_typeof(resources) = 'array' AND jsonb_array_length(resources) > 0),
    CONSTRAINT ck_cap_grant_domains
      CHECK (jsonb_typeof(domains) = 'array' AND jsonb_array_length(domains) > 0),
    CONSTRAINT ck_cap_grant_constraints_object
      CHECK (jsonb_typeof(constraints) = 'object'),
    CONSTRAINT ck_cap_grant_expiry
      CHECK (expires_at IS NULL OR expires_at > issued_at),
    CONSTRAINT ck_cap_grant_revoke
      CHECK (revoked_at IS NULL OR (revoked_at >= issued_at AND revoked_by IS NOT NULL
                                    AND revoke_reason IS NOT NULL)),
    CONSTRAINT ck_cap_grant_generation
      CHECK (grant_generation > 0 AND writer_generation > 0),
    CONSTRAINT uq_cap_grant_idempotency
      UNIQUE (tenant_id, idempotency_key)
);
```

### Required identity rules

1. `app_id` is a denormalized lookup field, not ownership proof. The adapter loads the definition and checks equality with `dynamic_app_definitions.app_id`; a trusted-write trigger or service transaction must prevent drift.
2. `(app_version_id, app_definition_id)` is mandatory. The existing unique key `(id, app_definition_id)` on `dynamic_app_versions` makes the composite FK valid; a simple `app_version_id -> dynamic_app_versions.id` is explicitly forbidden.
3. `app_artifact_digest` must equal the immutable version artifact digest. A plugin instance additionally proves the exact instance/version/tenant relation and runtime artifact/generation outside the SQL FK.
4. `scope_kind/scope_id` is the effective scope selector. Explicit `tenant_id`, `cabinet_id`, and `project_id` are retained for RLS/indexing and must be proven consistent with the scope registry/ownership anchors. The definition-owner tenant FK must also prove `(app_definition_id, tenant_id)` consistency. A scope registry (or equivalent real typed owner relation) is mandatory before cabinet/project positive grants; a generic FK on polymorphic `scope_id` is invalid. `tenant`/`cabinet`/`project` scope is never inferred from JSONB.
5. `subject_kind/id` is an authenticated subject binding. Null or empty means no subject; unbound grants require a separately approved policy and a sentinel subject key, not SQL NULL uniqueness behavior. The target contract therefore uses `NOT NULL`.
6. `plugin_instance_id` is required for code-plugin/runtime calls. A declarative-only grant may be instance-independent only when policy explicitly says so; it still cannot be reused across another subject or scope. Instance relation must verify definition, exact version, tenant, artifact and runtime state.
7. `session_id` is the active call boundary and is resolved from session/runtime state. It is not a substitute for `subject_id` or `plugin_instance_id`.
8. `operations`, `resources`, `domains` are sorted, duplicate-free, canonical JSON arrays. `constraints` retains `eventTypes`, `maxBytesPerRequest` and any policy fields that are actually enforced. Their digests are computed over canonical serialization and exclude diagnostics.

If the repository's instance table cannot expose the composite `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)` key in the prerequisite revision, the instance FK is staged: first create a scope/instance registry with that unique key, then add this FK before positive canonical writes. No adapter-only assertion is accepted as a permanent replacement.

## 4. Indexes and active uniqueness

The active grant identity is the complete authorization tuple, not generated `id` or a fencing epoch. For an explicitly instance-independent grant, `plugin_instance_id` is represented as a fixed empty sentinel only for the uniqueness expression; authorization still requires the policy-approved instance-independent path:

```text
(tenant_id, app_version_id, capability_key, subject_kind, subject_id,
 COALESCE(plugin_instance_id, ''), scope_kind, scope_id,
 operations_digest, resources_digest, domains_digest, constraints_digest)
```

PostgreSQL partial indexes are required for lookup and active uniqueness. Expiry is evaluated at authorization time; a moving clock must not occur in an index predicate.

```sql
CREATE UNIQUE INDEX uq_cap_grant_active_identity
ON dynamic_app_capability_grants (
  tenant_id, app_version_id, capability_key, subject_kind, subject_id,
  COALESCE(plugin_instance_id, ''), scope_kind, scope_id,
  operations_digest, resources_digest, domains_digest, constraints_digest
)
WHERE revoked_at IS NULL;

CREATE INDEX ix_cap_grant_authorize
ON dynamic_app_capability_grants
  (tenant_id, scope_kind, scope_id, app_definition_id, app_version_id,
   capability_key, subject_kind, subject_id, plugin_instance_id)
WHERE revoked_at IS NULL;

CREATE INDEX ix_cap_grant_validity
ON dynamic_app_capability_grants (tenant_id, issued_at, expires_at, revoked_at);

CREATE INDEX ix_cap_grant_instance_generation
ON dynamic_app_capability_grants (plugin_instance_id, writer_generation, grant_generation)
WHERE plugin_instance_id IS NOT NULL;

CREATE INDEX ix_cap_grant_source
ON dynamic_app_capability_grants (source_kind, source_id, schema_version);
```

`grant_generation` is incremented for a replacement/reissue identity. Renewal must atomically close/revoke or supersede the previous active row and insert the next generation under the unique boundary. `SELECT`-then-insert is not sufficient. `writer_generation` is a fencing epoch: a stale worker cannot issue/revoke if its epoch is not the current partition epoch. CAS/idempotency compares the payload/binding digest on conflict; same identity with a different digest is a conflict and goes to quarantine.

The active index deliberately excludes `expires_at`: expired rows remain evidence and may remain unique until an explicit revoke/supersede transaction closes them. Runtime authorization denies at `now >= expires_at`.

## 5. FK and `ON DELETE` policy

| Relation | Required action | Reason |
|---|---|---|
| grant → version + definition composite | `RESTRICT` | A published version is authorization provenance; same-definition proof must survive cleanup attempts. |
| grant → definition | `RESTRICT` | Prevent deleting the owner while grants/audit require its identity. |
| grant → plugin instance composite | `RESTRICT` | Do not silently erase instance-bound authorization history; disable/revoke the instance first. |
| grant → tenant/cabinet/project anchors | `RESTRICT` or explicit archive policy | Exact anchor semantics depend on the foundation revision; no orphan scope grants. |
| audit/quarantine/mapping → grant/version/definition | `SET NULL` for optional target, never cascade history | Security evidence survives ordinary cleanup. Store immutable source IDs/digests even if target is archived. |
| compatibility projection → canonical grant | `RESTRICT` or nullable diagnostic link | Legacy cleanup must not delete canonical authorization evidence. |

If an owner anchor is not yet a stable table/type, the target uses an approved scope registry with real keys. A generic FK on `scope_id` is not valid.

## 6. Audit and revoke contract

Authorization and lifecycle evidence is a separate append-only table. A mutable `revoke_reason` on the grant is a query convenience, not the audit history.

```sql
CREATE TABLE dynamic_app_capability_audit (
    id                   bigserial PRIMARY KEY,
    event_id             text NOT NULL UNIQUE,
    event_type           text NOT NULL,
    decision              text NOT NULL,
    reason_code          text NOT NULL,
    occurred_at          timestamptz NOT NULL DEFAULT now(),
    tenant_id            text NULL,
    cabinet_id           text NULL,
    project_id           text NULL,
    session_id           text NULL,
    subject_kind         text NULL,
    subject_id           text NULL,
    app_definition_id    text NULL,
    app_version_id       text NULL,
    capability_grant_id  text NULL,
    plugin_instance_id   text NULL,
    grant_generation     bigint NULL,
    writer_generation    bigint NULL,
    actor_id             text NULL,
    request_id           text NULL,
    trace_id             text NULL,
    idempotency_key      text NULL,
    capability_key       text NULL,
    operation            text NULL,
    resource             text NULL,
    domain               text NULL,
    payload_digest       text NULL,
    schema_version       text NOT NULL,
    redaction             text NOT NULL,
    mcp_span_id          text NULL,
    mcp_profile_id       text NULL,
    mcp_tool_server      text NULL,
    mcp_tool_name        text NULL,
    mcp_tool_version     text NULL,
    mcp_arguments_digest text NULL,
    mcp_result_status     text NULL,
    mcp_error_code        text NULL,
    mcp_duration_ms       integer NULL,
    metadata              jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT fk_cap_audit_grant
      FOREIGN KEY (capability_grant_id)
      REFERENCES dynamic_app_capability_grants (id)
      ON DELETE SET NULL
);

CREATE INDEX ix_cap_audit_tenant_time
  ON dynamic_app_capability_audit (tenant_id, occurred_at DESC);
CREATE INDEX ix_cap_audit_grant_time
  ON dynamic_app_capability_audit (capability_grant_id, occurred_at DESC);
CREATE INDEX ix_cap_audit_trace
  ON dynamic_app_capability_audit (trace_id, occurred_at);
```

The audit role is append-only: implementation must both revoke `UPDATE`/`DELETE` privileges from application/API roles and install a deny-by-default trigger (or equivalent security-reviewed write boundary) for mutation; only the gateway/audit writer may insert. Tenant admins receive tenant-scoped read. Events with unresolved tenant identity are migration-admin-only until resolution, so audit `tenant_id` is nullable only for those pre-resolution events and must never be client-supplied. Raw arguments, tokens, secrets and PII are excluded; MCP data is canonical digest plus the listed identity/status fields. Every issue/import/update/revoke/expiry observation, allow/deny, adapter failure, quarantine, cutover and fencing event is recorded. An audit failure cannot turn deny into allow.

Revoke semantics:

- revoke is a forward-only state transition; set `revoked_at` once and require authenticated `revoked_by` plus non-empty reason;
- same actor/reason/idempotency key is an idempotent replay; a conflicting second revoke is a conflict and audit event;
- expiry is read-time deny (`now >= expires_at`), never destructive deletion;
- reissue creates a new row and `grant_generation`; historical row remains auditable;
- `writer_generation` fencing prevents an old worker from revoking or issuing after cutover/rollback.

## 7. Compatibility mapping and quarantine

The source mapping is needed for resumable import and for proving that no legacy identity disappeared.

```sql
CREATE TABLE dynamic_app_capability_grant_mappings (
    id                      bigserial PRIMARY KEY,
    source_kind             text NOT NULL,
    source_id               text NOT NULL,
    canonical_schema_version text NOT NULL,
    source_payload_digest   text NOT NULL,
    canonical_payload_digest text NULL,
    target_grant_id         text NULL,
    status                  text NOT NULL,
    reason_code             text NULL,
    checkpoint              text NULL,
    first_seen_at           timestamptz NOT NULL DEFAULT now(),
    last_seen_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_kind, source_id, canonical_schema_version),
    FOREIGN KEY (target_grant_id)
      REFERENCES dynamic_app_capability_grants (id)
      ON DELETE SET NULL
);

CREATE TABLE dynamic_app_capability_grant_quarantine (
    id                    bigserial PRIMARY KEY,
    source_kind           text NOT NULL,
    source_id             text NOT NULL,
    schema_version        text NOT NULL,
    reason_code            text NOT NULL,
    status                 text NOT NULL DEFAULT 'open',
    source_payload_digest  text NOT NULL,
    resolved_tenant_id     text NULL,
    resolved_app_definition_id text NULL,
    resolved_app_version_id text NULL,
    resolved_scope_kind    text NULL,
    resolved_scope_id      text NULL,
    resolved_subject_kind  text NULL,
    resolved_subject_id    text NULL,
    first_seen_at          timestamptz NOT NULL DEFAULT now(),
    last_seen_at           timestamptz NOT NULL DEFAULT now(),
    checkpoint             text NULL,
    redacted_metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_kind, source_id, schema_version, source_payload_digest)
);

CREATE INDEX ix_cap_grant_quarantine_open
  ON dynamic_app_capability_grant_quarantine (status, source_kind, last_seen_at DESC);
CREATE INDEX ix_cap_grant_quarantine_tenant
  ON dynamic_app_capability_grant_quarantine (resolved_tenant_id, status);
```

Quarantine is visible, counted, retryable and fail-closed. It is mandatory for orphan/ambiguous/cross-definition/cross-tenant rows, missing required subject or instance, denied protocol entries, unsupported capability/constraint semantics, invalid lifecycle/provenance, duplicate active identity and any mapping that would broaden permission. A quarantine record is never read by authorization and never silently converted to an allow. Same source identity with a different payload digest is a conflict, not an overwrite.

## 8. RLS and trusted write path

All four target tables (`grants`, `audit`, `mappings`, `quarantine`) have `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.

Normative policy shape:

- grant `USING`/`WITH CHECK`: `tenant_id = current_setting('app.tenant_id', true)` and cabinet/project context matches the transaction context; subject/instance must belong to the same tenant and exact app/version;
- audit: tenant-scoped reads; inserts only through the gateway/audit role; no tenant API role bypass;
- mapping/quarantine: migration service and security/audit roles may write; tenant read is limited to rows resolved to that tenant; unresolved rows are migration-admin only;
- no context or malformed context denies by default;
- parent `EXISTS` checks validate tenant/cabinet/project and instance ownership, even when denormalized IDs are present;
- service writes derive tenant/scope/subject/instance from authenticated context and resolve app/version server-side. Client/plugin-supplied values are compared, never trusted.

RLS policy names and helper functions are part of the migration rollback inventory. Policies are created after prerequisite anchors/FKs and before canonical cutover.

## 9. Staged implementation order and blockers closed

1. Prepare tenant/cabinet/project/subject anchors and a scope registry (or equivalent real typed owner relation); define canonical identity types. Add the prerequisite unique `(id, tenant_id)` key on app definitions before enabling the grant tenant FK. No cabinet/project positive grant is eligible without a registry membership proof.
2. Ensure `dynamic_app_versions` has unique `(id, app_definition_id)` and immutable artifact identity; retain existing definition/version composite conventions.
3. Extend/introduce the plugin-instance registry unique key `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)` and runtime generation/status relation; this exact key is required before positive instance-bound grants.
4. Create mapping, quarantine and audit tables before positive imports.
5. Create canonical grants with composite same-definition FK, capability key, explicit subject, scope, instance and generation/fencing fields.
6. Create checks/indexes/RLS and install the trusted gateway writer. Reconcile all legacy/protocol entries through the adapter.
7. Backfill only rows with exact app/version/tenant/scope/subject/instance proof. Quarantine everything else and record a zero-unknown checkpoint before a partition cutover.
8. Switch gateway reads/writes by tenant + app definition partition. Keep legacy rows read-only and never run two authorization owners concurrently.
9. Cleanup is a later forward migration; audit, mappings and quarantine survive legacy table removal.

This closes the prior blockers as follows:

| Prior blocker | Contract decision |
|---|---|
| `app_version_id` could point to another definition | composite `(app_version_id, app_definition_id)` FK to version unique key |
| capability name could be lost | required `capability_key`, one row per capability |
| user/service identity absent | required `subject_kind`, `subject_id` |
| plugin grant transferable to another runtime | `plugin_instance_id`, composite instance relation, session/runtime gate |
| tenant/cabinet/project scope ambiguous | explicit scope columns, scope shape check, anchor/RLS policy |
| operations/resources/domains/constraints under-specified | non-empty canonical arrays, constraint object, four digests, versioned mapping |
| revoke/audit could be deleted or overwritten | append-only audit, `RESTRICT` provenance, forward-only revoke |
| renewal race and stale workers | active partial unique index, `grant_generation`, `writer_generation`, idempotency/CAS |
| legacy/unrepresentable data silently skipped | unique mapping plus visible retryable quarantine |
| MCP identity mixed with declarative capability | separate MCP audit fields and digest linkage |

## 10. Explicit non-goals and unresolved implementation risks

- This document does not choose whether the canonical tenant key is a new `tenants.id` or an existing `companies.id`; the foundation anchor revision must choose one and use it consistently in FKs/RLS. The implementation must also expose a unique definition-owner key `(id, tenant_id)` for the grant tenant FK.
- It does not invent hard FKs for polymorphic scope IDs before a scope registry exists; cabinet/project positive grants remain blocked until that registry (or typed equivalent) is present.
- It does not define a wildcard operation/resource semantics. Absence remains deny/quarantine until a bounded, versioned policy mapping is approved.
- It does not claim current gateway audit is already durable or that current MCP `mcp_audit_log` is linked; the implementation must add the linkage or preserve a deterministic correlation record.
- It does not authorize ORM/Alembic changes, backfill, shared-environment migration, or legacy cleanup.

Verification freeze is explicit: no tests, build, lint, format, compile, migration execution, or git checks are run for this documentation-only change. Those checks are deferred until the migration-wide freeze is explicitly lifted.
