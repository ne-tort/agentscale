# M05 concrete plugin-instance persistence adapter contract

Status: migration-stage persistence contract, documentation only. Baseline: 2026-11-01.

This document freezes the concrete persistence boundary for the M05 plugin-instance registry, runtime fencing, lease ownership and writer-generation CAS. It is reconciled with the current M05 application contracts and adapters, the typed owner registry, the target ORM task map, and the existing dynamic-app ORM rows. It does not change code, ORM models, Alembic revisions, core documentation, database state, or shared environments.

The M05 adapter proves that one concrete installation and one current runtime worker are the same identity at the point of a read or mutation. It is not an authorization grant and it cannot broaden scope, select a fallback instance, or turn a quarantine record into positive state.

## 1. As-built boundary and target responsibility

The current application contract in `application/plugin_instances` already defines:

- `PluginInstanceIdentity` with `plugin_instance_id`, definition/version, tenant, typed scope and artifact digest;
- `PluginInstanceRecord` with subject, session, active/archived/quarantined state, runtime and writer generations, and optional lease fields;
- `PluginInstanceRepository.get_exact`, `acquire_lease`, and `advance_writer` ports;
- fail-closed service decisions for identity, artifact, subject/session, lifecycle, lease and stale-generation mismatches.

The current `plugin_instances_adapter` maps a complete runtime row and delegates lease/writer mutation to a CAS port. A missing or malformed row is deny-only and may be quarantined. A returned CAS row is accepted only after the adapter compares the full identity and the expected fencing result. The current application state enum is `active|archived|quarantined`, while the legacy dynamic-app row uses `draft|published|disabled|revoked`; the target persistence must define and implement an explicit, reviewed mapping (including which target state maps to M05 `active`). Until that mapping exists, a DB row with `published` status is not directly consumable by the current adapter and remains a migration blocker.

The concrete persistence implementation must make those application assumptions true at the database boundary. The adapter port is not a substitute for a composite FK, RLS, transaction-local tenant context, or SQL affected-row check.

The current `dynamic_app_instances` ORM row is only a legacy baseline: its identity is `id` plus definition/version/scope, its scope is limited to `tenant|cabinet|project`, and it has no canonical tenant FK, exact plugin-instance registry relation, artifact digest, subject/session binding, runtime generation, writer generation, or lease evidence. The current canonical grant row also has only a simple version FK and no instance binding. Neither row is eligible for M05 positive persistence until the planned foundation and reciprocal FK revisions close these gaps.

The current read-only `plugin_instance_registry` application adapter is useful for exact snapshot resolution but intentionally exposes no mutation. M05 persistence adds the trusted repository implementation behind the existing fencing ports; it must preserve the read-only registry semantics and add atomic SQL CAS rather than implement `SELECT` then mutate.

## 2. Complete identity and row contract

`plugin_instance_id` alone is never a valid lookup or mutation key. Every M05 repository operation resolves and compares this complete identity:

```text
(plugin_instance_id,
 app_definition_id,
 app_version_id,
 tenant_id,
 scope_kind,
 scope_id,
 artifact_digest)
```

The database instance key required before positive writes is:

```text
(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)
```

`id` is the physical stable instance key and is exposed to the application as `plugin_instance_id`. It must not be replaced by `app_id`, `app_version_id`, `scope_id`, `session_id`, runtime reference, lease ID, or worker ID. The artifact digest is compared to the immutable version digest and is part of the application identity envelope even where it is not part of the physical unique key.

A persisted positive-eligible instance contains at least:

| Field | Contract |
|---|---|
| `id` / `plugin_instance_id` | Stable exact installation identity. Immutable after creation. |
| `app_definition_id`, `app_version_id` | Same-definition composite proof; version ID alone is insufficient. |
| `tenant_id` | Canonical `companies.id` value, with the aligned owner-anchor type. |
| `scope_kind`, `scope_id` | Exactly `tenant`, `cabinet`, `project`, or `plugin_instance`; resolved through the typed scope registry. |
| `cabinet_id`, `project_id` | Installation anchors where applicable; project requires a matching cabinet and tenant relation. |
| `artifact_digest` | Immutable `sha256:` digest equal to the selected version artifact. |
| `subject_kind`, `subject_id` | Exact authenticated subject binding where instance policy requires one. A blank subject is never a wildcard. |
| `session_id` | Exact runtime/session binding where required; it never substitutes for instance or subject identity. |
| `status` | Target persistence lifecycle may be `draft`, `published`, `disabled`, `revoked`, or `quarantined`; the current M05 application adapter accepts `active`, `archived`, or `quarantined`, so a reviewed repository mapping is mandatory. Only the mapped policy-eligible published/active state can pass positive admission. |
| `runtime_generation` | Non-negative monotonic restart/redeploy generation. A stale value denies. |
| `writer_generation` | Non-negative partition/instance fencing epoch. A stale worker must change zero rows. |
| `lease_owner` | Current worker/principal holding the runtime lease, when lease admission is required. |
| `lease_id` | Opaque exact lease identity; never inferred from owner or runtime reference. |
| `lease_expires_at` | Timezone-aware expiry. A missing or expired required lease denies. |
| runtime evidence | Runtime reference, start/stop/heartbeat timestamps and provenance without secrets. |

All timestamps are timezone-aware. Generations are non-negative; runtime generation increments only for an approved restart/redeploy transition, while writer generation advances only through the writer CAS contract. A row with missing required subject, session, artifact, generation or lease evidence is `draft`, `disabled`, or `quarantined` under the reviewed migration policy; it is never inferred to be published.

## 3. Typed scope and reciprocal registry anchors

The instance row and `scope_registry` must prove the same typed installation tuple. The registry has exactly these shapes:

```text
tenant:
  scope_id = tenant_id; cabinet_id = NULL; project_id = NULL

cabinet:
  scope_id = cabinet_id; tenant_id and cabinet_id required;
  project_id = NULL

project:
  scope_id = project_id; tenant_id, cabinet_id and project_id required

plugin_instance:
  scope_id = plugin_instance_id; plugin_instance_id = scope_id;
  cabinet_id/project_id mirror the installation scope;
  both may be NULL only for an explicitly approved tenant installation
```

The instance's `tenant_id`, definition/version, `scope_kind`, `scope_id`, cabinet/project anchors and artifact must agree with the registry and its parents. Separate simple FKs do not prove the project/company/cabinet tuple. The trusted repository and RLS policy must additionally require parent `EXISTS` checks for retained company status, active cabinet membership/owner relation, project compatibility, definition owner, immutable version and active registry state.

The registry/instance relation is cyclic and must be staged:

1. Create or validate tenant, cabinet and project anchors and the empty registry.
2. Align definition owner and version same-definition keys.
3. Add instance fields and the unique key `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)`.
4. Backfill only exact, representable instances; classify all other rows into draft/disabled/quarantine evidence.
5. Add named reciprocal composite FKs in a late constraint revision, using `use_alter`/explicit `op.create_foreign_key` where creation order requires it.
6. Validate every staged constraint before exposing published resolution or instance-bound grants.

The registry-to-instance and instance-to-registry constraints use `ON DELETE RESTRICT` or archive-only semantics. No deletion of a definition, version, instance, registry row, mapping, quarantine or audit record may silently erase M05 evidence. A nullable/deferred link is migration staging only; it is not a permanent adapter assertion.

## 4. Repository ports and transaction ownership

The concrete adapter implements the existing application ports through one transaction-scoped trusted unit of work:

```text
resolve authenticated principal/session
  -> resolve canonical tenant and typed scope
  -> resolve exact definition/version and immutable artifact
  -> resolve exact instance and registry tuple
  -> set transaction-local RLS context
  -> perform one SQL read or zero-row CAS mutation
  -> append quarantine/audit evidence when required
  -> commit or roll back
```

Required repository behavior:

| Operation | Required behavior | Forbidden behavior |
|---|---|---|
| `get_exact(identity)` | Query the complete composite identity and return one complete snapshot or no row. | Lookup by instance ID alone, version alone, scope alone, or fallback instance. |
| `acquire_lease(request)` | One SQL CAS that validates identity, active lifecycle, expected generations and lease preconditions, then returns the complete post-write snapshot. | `SELECT` then update; accepting a partial returned row; treating `None` as success. |
| `advance_writer(request)` | One SQL CAS that validates identity, current lease and expected runtime/writer generations, increments writer generation exactly once, and returns the complete post-write snapshot. | Client-side increment; stale-worker update; accepting a row with the wrong next generation. |
| malformed/conflicting row | Return deny/conflict and append redacted quarantine evidence. | Repairing by copying IDs, selecting a nearby row, or authorizing from quarantine. |

The adapter maps both `definition_id`/`app_definition_id` and `version_id`/`app_version_id` only when aliases agree. Missing or contradictory aliases, required fields, state, generations, lease fields or identity anchors are malformed evidence. The returned snapshot is revalidated against the request before a positive result is exposed.

The repository must return explicit `allowed`, `not found`, `identity mismatch`, `archived`, `quarantined`, `lease missing`, `lease expired`, `lease owner mismatch`, `lease ID mismatch`, `runtime generation stale`, `writer generation stale`, `lease CAS conflict`, `writer CAS conflict`, or system-failure outcomes. It never converts a database no-row result into an inferred success.

## 5. Lease identity and expiry contract

A lease is a fencing fact for one exact instance and worker. When runtime admission requires a lease, all of these values must match server-side in one transaction:

```text
identity tuple
subject_id
session_id
runtime_generation
writer_generation
lease_owner
lease_id
lease_expires_at > transaction_now
```

`lease_owner` and `lease_id` are independent fields. Matching one does not prove the other. `lease_expires_at` is evaluated against a database transaction timestamp or an explicitly captured server timestamp; a client clock is not authoritative. Expiry denies at read/mutation time and does not delete historical evidence.

Lease acquisition/replacement may be represented by a reviewed forward transition, but it must preserve the old lease/fencing evidence in audit or history. A stale owner cannot refresh, release, or replace another worker's lease. A lease CAS result whose returned owner, ID, expiry, identity or generations differ from the request is a conflict and is quarantined as inconsistent persistence evidence.

## 6. Runtime and writer generation CAS

`runtime_generation` and `writer_generation` have different meanings:

- `runtime_generation` changes when the concrete installation restarts, redeploys, or otherwise receives a new runtime instance generation. A call carrying an older runtime generation is stale even if its writer is current.
- `writer_generation` fences mutation workers for the exact instance/partition. A worker may advance it only from the expected current epoch and only while holding the exact lease. The successful row has `writer_generation = expected + 1` and the same runtime generation.

The database must enforce the mutation with a single `UPDATE ... WHERE` statement and inspect the affected row count. A stale or cross-tenant request must produce zero affected rows, with no partial update and no positive result. `ON CONFLICT DO NOTHING`, an unconditional update followed by an application comparison, and `SELECT` then mutate are not CAS implementations.

### 6.1 Lease CAS shape

The exact column names may follow the implementation model, but the predicate and outcome are normative. The `status = 'published'` literal below is a target-schema placeholder only: the concrete repository must use the reviewed target-to-application lifecycle mapping and must not pass a raw legacy status to the current adapter:

```sql
UPDATE dynamic_app_instances AS i
SET lease_owner = :lease_owner,
    lease_id = :lease_id,
    lease_expires_at = :lease_expires_at,
    updated_at = :server_now
WHERE i.id = :plugin_instance_id
  AND i.app_definition_id = :app_definition_id
  AND i.app_version_id = :app_version_id
  AND i.tenant_id = :tenant_id
  AND i.scope_kind = :scope_kind
  AND i.scope_id = :scope_id
  AND i.artifact_digest = :artifact_digest
  AND i.subject_id = :subject_id
  AND i.session_id = :session_id
  AND i.status = 'published'
  AND i.runtime_generation = :expected_runtime_generation
  AND i.writer_generation = :expected_writer_generation
  AND (:current_lease_id IS NULL OR i.lease_id = :current_lease_id)
  AND (:current_lease_owner IS NULL OR i.lease_owner = :current_lease_owner)
  AND (i.lease_expires_at IS NULL OR i.lease_expires_at <= :server_now
       OR (i.lease_id = :current_lease_id AND i.lease_owner = :current_lease_owner))
RETURNING i.*;
```

The implementation must bind `:server_now` once and define whether same-owner renewal is permitted. If the policy does not permit renewal, remove that branch and require an expired/null lease. Exactly one returned row is success; zero rows are lease CAS conflict/deny. The adapter then maps and rechecks the complete returned row.

### 6.2 Writer-generation CAS shape

Writer advancement is a zero-row stale-worker barrier:

```sql
UPDATE dynamic_app_instances AS i
SET writer_generation = i.writer_generation + 1,
    updated_at = :server_now
WHERE i.id = :plugin_instance_id
  AND i.app_definition_id = :app_definition_id
  AND i.app_version_id = :app_version_id
  AND i.tenant_id = :tenant_id
  AND i.scope_kind = :scope_kind
  AND i.scope_id = :scope_id
  AND i.artifact_digest = :artifact_digest
  AND i.subject_id = :subject_id
  AND i.session_id = :session_id
  AND i.status = 'published'
  AND i.runtime_generation = :expected_runtime_generation
  AND i.writer_generation = :expected_writer_generation
  AND i.lease_owner = :lease_owner
  AND i.lease_id = :lease_id
  AND i.lease_expires_at > :server_now
RETURNING i.*;
```

A successful row must have the exact identity, subject/session, lease owner, lease ID, lease expiry bound to the request, unchanged runtime generation and `writer_generation = expected_writer_generation + 1`. The SQL predicate must bind the expected lease identity and requested expiry semantics, not merely test that some lease is currently unexpired. A zero-row result is `writer_cas_conflict` or a more specific stale/lease denial after server-side classification; it must not be retried by reading another instance. The SQL affected-row check and returned-row recheck are both required because a driver result alone does not prove the full application envelope. The current application repository adapter does not independently recheck every writer-return field (its service layer performs additional checks), so the concrete persistence adapter must close that gap before claiming this contract is implemented.

Any runtime restart transition that changes `runtime_generation` must itself be a trusted CAS/transaction and must fence old writer epochs according to the reviewed lifecycle policy. It cannot be simulated by changing a client token or by overwriting a row without audit.

## 7. RLS, FORCE RLS and privileges

The instance table and typed registry are tenant-owned security boundaries. Their implementation must use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, with named `USING` and `WITH CHECK` policies. Policies are installed after parent/reciprocal keys exist and before any positive M05 write.

The effective policy requires all of the following:

```text
tenant_id = current_setting('app.tenant_id', true)
registry row is active and matches scope_kind/scope_id
company is retained/eligible
cabinet/project anchors match transaction context and parent compatibility
instance definition/version belongs to the same tenant
instance artifact equals immutable version artifact
instance status is target-positive and maps explicitly to M05 application state active
subject/session and current runtime generation match the trusted request
trusted role and current writer generation are present for mutation
```

`published` is a legacy/product lifecycle value, not the current M05 adapter state token; the trusted query/map must translate it only under the approved policy.

A missing, blank, malformed or contradictory `app.tenant_id`, `app.cabinet_id`, `app.project_id`, `app.user_id` or writer context denies by default. Context values are server-resolved and transaction-local; request/plugin `company_id`, tenant, scope, instance, subject and lease claims are assertions to compare, never authority.

Ordinary tenant reads expose only active, same-tenant rows. Archived and quarantined rows are reserved for migration/security/audit roles and cannot satisfy `get_exact` for positive admission. Migration roles may classify and append evidence under an explicit role boundary. API roles cannot bypass RLS, update generation columns directly, mutate quarantine into active state, or delete retained instance/security evidence.

RLS does not replace exact gateway/service checks. Conversely, service checks do not replace typed FKs, `FORCE RLS`, parent `EXISTS`, or role privileges.

## 8. Quarantine and conflict persistence

Quarantine is durable, visible, counted, retryable and fail-closed. It is separate from the positive instance registry and is never queried by authorization or runtime admission. A quarantine row stores redacted evidence sufficient to repair forward:

```text
source_kind/source_id and schema_version
source/payload digest
resolved tenant, scope, definition, version and instance IDs where known
subject/session and runtime/writer generations where known
lease owner/ID/expiry metadata where safe
stable reason code and redacted detail
first_seen_at, last_seen_at, checkpoint and correlation ID
```

Never persist raw tokens, credentials, secrets, raw plugin payloads or unredacted runtime arguments. Mandatory quarantine/conflict cases include:

- missing, malformed, overlength, duplicate, orphan or cross-tenant identity;
- definition/version/scope/registry tuple mismatch;
- artifact digest mismatch or unapproved version lifecycle;
- missing subject/session/runtime/lease evidence where required;
- archived, revoked, disabled or already quarantined instance presented as positive;
- stale runtime generation, stale writer generation, expired lease, owner mismatch or lease ID mismatch;
- SQL CAS zero-row result when the request cannot be classified as an ordinary not-found;
- returned CAS row whose full identity or next generation does not match the request;
- same source identity with a changed payload digest.

A same-source/schema/digest replay may be idempotently classified as the original outcome. A changed digest is a conflict and must never overwrite the prior mapping or row. Repair is a forward mapping/import with the same source identity and a new evidence event. Quarantine does not authorize, does not count as positive parity, and blocks the affected partition when it could change an allow/deny result.

## 9. Trusted transaction and audit boundary

A positive lease, writer-generation or lifecycle mutation commits only inside one trusted transaction that:

1. resolves the authenticated subject and canonical tenant;
2. resolves or receives the immutable `shared_identity.IdentityEnvelope` for the exact tenant/scope/definition/version/artifact/instance/subject/session/generation/lease context, and verifies its envelope digest and promotion partition rather than reconstructing those values downstream;
3. sets transaction-local RLS context from server evidence;
4. resolves and verifies registry, definition/version, artifact, subject/session and exact instance anchors;
5. executes the relevant zero-row CAS and checks affected rows;
6. maps and rechecks the complete returned snapshot;
7. appends immutable redacted audit evidence for success, deny, stale generation, lease conflict, quarantine or system failure;
8. commits only when all checks, privileges, RLS `USING`/`WITH CHECK`, and audit append succeed.

Audit failure rolls back the positive mutation and returns deny/system failure. Corrections are new append-only events. A trusted positive mutation must not be considered complete if its linked M06 evidence transaction cannot commit the unchanged `IdentityEnvelope`-backed evidence. A quarantine outcome uses the separate durable quarantine route and cannot be promoted by the trusted transaction. Audit, mapping and quarantine records survive instance/archive cleanup through `RESTRICT` or genuinely optional `SET NULL` links; they are never erased by a runtime cascade.

A successful M05 runtime admission is only a handoff to later M06 policy evaluation. It does not authorize a capability. M06 must receive the same immutable `shared_identity.IdentityEnvelope` (including tenant, scope and cabinet/project anchors, definition/version, artifact, subject/session, exact instance, runtime/writer generation, lease identity/owner/expiry, correlation/trace IDs, promotion partition and envelope digest), or a validated equivalent that preserves that envelope digest; it must not reconstruct those values from a plugin or request. The M06 evidence transaction must reject tenant, identity-digest, timestamp, correlation/trace, lineage or snapshot mismatches, and quarantine evidence must remain outside the trusted allow path.

## 10. Acceptance and migration gates

Before the M05 persistence gate is enabled, the implementation evidence must show:

- canonical owner/tenant values were classified against `companies.id`/`String(40)` without truncation or implicit casts;
- definition/version same-definition keys and typed registry parent compatibility are validated;
- the exact instance unique key exists and both reciprocal registry/instance constraints are named and validated;
- all four scope shapes, including `plugin_instance`, reject impossible anchor combinations;
- published instances have artifact, lifecycle, subject/session and required runtime/lease evidence;
- lease and writer mutations use one SQL CAS each, inspect zero-row outcomes, and return complete post-write snapshots;
- stale runtime, stale writer, expired lease, wrong owner, wrong lease ID and cross-tenant requests produce no positive mutation;
- `ENABLE/FORCE RLS`, `USING`, `WITH CHECK`, role privileges and parent `EXISTS` checks deny missing/wrong context;
- malformed, conflicting and unresolved rows are isolated in durable quarantine and never read as positive state;
- full redacted audit evidence is appended atomically for both positive and negative fencing outcomes;
- M05-to-M06 handoff preserves one immutable context and no fallback instance exists.

The M05 gate remains closed while any reciprocal FK is deferred/unvalidated, any required owner mapping is unresolved, any stale-generation path can mutate, any RLS policy allows missing context, or any quarantine row could be used as positive runtime evidence. Legacy `dynamic_app_instances` and `dynamic_app_grants` remain compatibility/as-built surfaces until the bounded partition promotion record enables a reviewed target owner.

## 11. Unresolved risks and verification freeze

Known blockers are the current `dynamic_app_instances` schema lacking canonical tenant/registry, artifact, subject/session, generations and lease columns; current legacy cascade paths that can erase evidence; the unresolved physical alignment of dynamic-app owner columns with `companies.id`; the cyclic registry/instance FK that still requires staged closure; the lifecycle enum mismatch between the current M05 adapter (`active|archived|quarantined`) and legacy/target persistence states (`draft|published|disabled|revoked|quarantined`); the fact that the application CAS ports do not themselves prove concrete SQL affected-row behavior; and the current repository adapter's incomplete independent writer-result recheck. The M06 handoff additionally requires persistence of the unchanged shared `IdentityEnvelope`/digest and evidence transaction boundary, which is not provided by the M05 ports alone. The current v1 `GrantSet` also does not carry a concrete plugin-instance ID, so instance-scoped imports remain quarantine-only until an external exact binding is proven.

This document is documentation-only. Tests, builds, lint, formatting, compilation, Alembic execution, migration execution, environment changes, database writes, and git checks were intentionally not run under the migration verification freeze. No code, ORM model, core document, or migration file was changed.
