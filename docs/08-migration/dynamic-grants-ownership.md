# Dynamic grants ownership and migration

Статус: migration design artifact. Документ разрешает integration gap между legacy `AppGrantRow` из `dynamic_apps.py` и typed `DynamicAppCapabilityGrantRow` из `dynamic_app_grants.py`/`domain.dynamic_apps.grants`. Документ не изменяет ORM, domain code, Alembic revisions или existing core docs.

## 1. Decision

`DynamicAppCapabilityGrantRow` и соответствующий typed domain contract `PersistenceCapabilityGrant` становятся canonical owner для capability authorization. Они владеют:

- точной идентичностью `app_id` + immutable `app_version_id`;
- tenant и scope (`tenant`, `cabinet`, `project`);
- normalized allowlists `operations`, `resources`, `domains` и `constraints`;
- issued/expiry/revoke lifecycle;
- issuer/revoker и audit provenance.

`AppGrantRow` (`dynamic_app_grants`) не является вторым policy owner после cutover. Он остаётся read-only legacy compatibility projection до завершения rollback window, а затем удаляется отдельным cleanup migration. Его JSONB `grants` не должен использоваться для новых authorization decisions и не должен получать новые прямые записи после перехода ownership.

Canonical read path:

```text
request context
  -> grant adapter/repository
  -> dynamic_app_capability_grants
  -> PersistenceCapabilityGrant
  -> authorize(exact app/version/tenant/scope/operation/resource/domain)
  -> gateway capability-name/requested-capability and subject/instance checks
```

Manifest remains a requested contract. `dynamic_app_versions.requested_capabilities` is not an implicit grant and must not be treated as authorization. An absent canonical grant is deny-by-default.

The canonical owner is a target design, not an as-built claim: the current gateway still accepts `RequestContext.manifest_version` and `company_id`, and evaluates a capability name before it evaluates operation/resource/domain. The adapter must resolve `company_id` to the canonical `tenant_id` through the approved ownership anchor and resolve `manifest_version` to `app_version_id` before constructing a persistence grant. It must never trust a plugin/client-supplied tenant, user or scope value; the gateway contract derives those from authenticated context.

### 1.1 Declarative-app v1 protocol boundary

The v1 `GrantSet` is capability-shaped: each grant has a required `name`, decision, optional constraints (`methods`, `resources`, `domains`, `eventTypes`, `maxBytesPerRequest`), issuer and expiry. The gateway also requires the capability name to be requested by the manifest and records it in its decision/audit path. This is not equivalent to the persistence row's three typed arrays: `DynamicAppCapabilityGrantRow` currently has no `capability` column, and `PersistenceCapabilityGrant.authorize` has no capability argument.

Consequently, a protocol grant may be imported into a canonical row only when the target representation preserves the capability identity and all applicable constraints. Until a reviewed target schema or explicit namespace rule provides that identity, the adapter must reject/quarantine the entry and must not drop `name`, treat it as an operation, merge different capabilities, or broaden `methods` into unrestricted operations. A one-row-per-capability mapping is the preferred future shape, but it requires a capability key/constraint in the target DDL; this document does not authorize an ORM change. `decision: denied` entries are protocol outcomes, not positive authorization rows, and must be retained only as provenance/audit if imported.

The mapping must define how protocol `constraints.methods` becomes canonical `operations`, and how absent constraints are handled. Absence is not permission to invent a wildcard: either the policy contract defines a bounded expansion or the entry is quarantined. `eventTypes`, `maxBytesPerRequest`, and other constraints must remain in canonical `constraints` or be quarantined when the target policy cannot enforce them. The adapter must compare protocol `appId`/`version` and the manifest's requested capability name with the resolved definition/version before import.

The v1 implementation contract further binds an effective grant to user, instance and exact tenant/cabinet/project context. The current persistence row and `PersistenceCapabilityGrant` have no subject or instance field, while the current gateway's in-memory `CapabilityGrant` has `subject_id` but no tenant/scope fields. Until the target model and adapter define where subject/instance binding is enforced, a row must not be considered sufficient evidence for a user/instance-scoped v1 decision; gateway identity, scope, instance and resource checks remain mandatory and missing binding is deny/quarantine.

## 2. Why the two rows cannot be merged by shape

The rows represent different contracts:

| Concern | Legacy `AppGrantRow` | Canonical `DynamicAppCapabilityGrantRow` |
|---|---|---|
| Table | `dynamic_app_grants` | `dynamic_app_capability_grants` |
| Identity | `(app_version_id, scope_kind, scope_id)` | generated grant id plus exact app/version and tenant/scope |
| Capability payload | opaque `grants` JSONB (domain `AppGrant` contains protocol capability entries) | non-empty typed operation/resource/domain arrays plus constraints; capability identity is not currently a column and must be resolved by the target mapping |
| Version reference | composite FK `(app_version_id, app_definition_id)` | FK `app_version_id → dynamic_app_versions.id` |
| Tenant | implicit in app definition/scope context | explicit `tenant_id` |
| Lifecycle | issue + optional expiry | issue + expiry + actor-required revoke timestamp/reason |
| Audit | no issuer/revoker fields | `issued_by`, `revoked_by`, `revoke_reason`, `audit_metadata` |
| Authorization | no transport-neutral decision contract | exact-match `PersistenceCapabilityGrant.authorize` |

A JSONB-to-array conversion is therefore not a safe ownership decision. The adapter must parse and validate legacy entries, quarantine rows that cannot be represented, and never broaden a grant to make the import succeed.

## 3. Canonical identity and adapter mapping

The adapter is the only compatibility boundary. It accepts a legacy row or legacy command, resolves the app definition/version, validates ownership, and emits a canonical `PersistenceCapabilityGrant`/row. New callers use the canonical repository directly. A v1 `GrantSet` is not self-sufficient authorization evidence: it has no tenant, cabinet, project, user/subject or instance binding, so those values must come from the authenticated gateway context and ownership checks. A GrantSet entry without that binding is quarantined, not treated as globally applicable.

### 3.1 Version mapping

`manifest_version` is a display/domain version string, not the canonical foreign-key identity. Resolve it only within the app definition:

```sql
SELECT id
FROM dynamic_app_versions
WHERE app_definition_id = :app_definition_id
  AND manifest_version = :manifest_version;
```

The result is `app_version_id`. The lookup is safe because the model enforces `UNIQUE (app_definition_id, manifest_version)` (`uq_dynamic_app_version_manifest_version`). A version string without a resolved `app_definition_id` is ambiguous and must be rejected/quarantined; never search by `manifest_version` alone.

For a legacy row that already has `app_version_id`, the adapter loads that version and verifies both:

1. `dynamic_app_versions.id` equals the supplied id; and
2. its `app_definition_id` equals the legacy `app_definition_id`.

For a legacy payload that has only `manifest_version`, resolve the app definition from the authenticated tenant and stable `app_id`, then apply the scoped lookup above. If no row or more than one row is observable (including corrupt historical data), fail closed and emit a migration error.

The canonical row stores `app_version_id`; it does not copy `manifest_version` as a second mutable identity. Read models may join to `dynamic_app_versions.manifest_version` for display. The adapter also checks that the manifest's `appId` and `version` agree with the resolved definition/version before import.

### 3.2 Field mapping

| Legacy source | Canonical target | Adapter rule |
|---|---|---|
| `AppGrantRow.id` | `audit_metadata.legacy_grant_id` and mapping/provenance record | Preserve source identity; do not reuse it as canonical PK unless a separate reviewed compatibility rule says so. |
| `app_definition_id` | resolve `dynamic_app_versions.app_definition_id` | Required consistency check; mismatch is quarantine. |
| `app_version_id` | `app_version_id` | Preserve only after version/definition FK verification. |
| `manifest_version` (legacy command/payload) | lookup key only | Resolve within `app_definition_id`; never store as the authorization key. |
| app definition `app_id` | `app_id` | Load from the definition, do not trust a duplicate request value without equality validation. |
| `scope_kind`, `scope_id` | `scope.kind`, `scope.id` | Validate allowed kinds and scope membership under the tenant. `scope_id` is not a polymorphic FK; policy must resolve ownership. |
| definition tenant / resolved tenant | `tenant_id` | Resolve from the canonical ownership anchor. A copied tenant value cannot override the parent. |
| `grants` JSONB | capability identity plus `operations`, `resources`, `domains`, `constraints` | Use a versioned parser. Each resulting list must be non-empty, unique, and semantically typed. Preserve v1 `name` and constraint semantics; because the current canonical row has no capability column, entries are quarantined until a reviewed one-row-per-capability/key mapping exists. Unsupported, denied, or ambiguous entries are not positive grants. |
| `issued_at` | `issued_at` | Preserve source timestamp if valid and timezone-aware; otherwise quarantine, never silently use import time. |
| `expires_at` | `expires_at` | Preserve `NULL` as no expiry; enforce `expires_at > issued_at`. |
| legacy actor/provenance | `issued_by`, `audit_metadata` | Require a stable actor for newly issued canonical grants. Historical imports may use a documented migration actor only when source provenance is absent and the audit metadata says so. |
| legacy revoke state, if any | `revoked_at`, `revoked_by`, `revoke_reason` | Never delete a revoked source row; require actor/reason invariants. A missing revoke actor is quarantine, not an invented user. |

The parser must define the legacy `grants` schema before backfill. A common shape such as `{operation, resource, domain}` may be converted one item at a time, but arbitrary strings, wildcard expansion, or requested capabilities are not valid evidence of authorization. The adapter must normalize ordering for hashing while retaining the original source representation in provenance.

## 4. Unique keys and concurrency

Canonical grant identity is an authorization tuple, not a generated id alone. The implementation migration must add a database uniqueness boundary before canonical writes are enabled. The preferred active-grant key is:

```text
(tenant_id, app_version_id, scope_kind, scope_id, operations_digest,
 resources_digest, domains_digest, constraints_digest)
```

where each digest is computed from canonical, sorted, duplicate-free JSON arrays/object. If the implementation instead stores a normalized capability key, the equivalent unique key must be explicitly documented and enforced. Do not use a uniqueness constraint on unsorted JSONB or on `manifest_version`.

Because revoke/renew history must remain auditable, the unique key applies to the currently active validity interval, not every historical row. Use a PostgreSQL partial unique index for rows with `revoked_at IS NULL` and either `expires_at IS NULL` or an application-maintained active marker. Since PostgreSQL partial indexes cannot evaluate a moving clock safely, expiry is a runtime authorization condition; an expired row may remain unique until a renewal/revoke operation closes it. Renewal must atomically close/replace the active row or use a deliberate grant-generation key. The adapter must define this choice before DDL; it must never rely on `SELECT`-then-insert.

For a migration-safe first phase, use a stable mapping key `(legacy_grant_id, canonical_schema_version)` with a unique constraint in a compatibility mapping table. The backfill transaction inserts the mapping and canonical row atomically; on conflict it reads the winner and compares canonical payload digest, version, tenant and scope. Same identity plus different digest is a migration conflict and is quarantined, not overwritten.

The canonical lookup indexes must cover `(tenant_id, scope_kind, scope_id, app_id, app_version_id)` and validity/revoke filtering. These are implementation acceptance requirements, not permission to edit the current models in this documentation-only change.

## 5. Migration and backfill sequence

### Phase A: inventory and empty target

1. Freeze the source contract: counts by tenant/app/version/scope, malformed JSON, duplicate scope rows, null/invalid dates, expiry and revoke state.
2. Verify every `AppGrantRow.app_version_id` resolves to one `dynamic_app_versions` row with the same `app_definition_id`.
3. Create the canonical table/index/mapping/audit structures through a reviewed Alembic migration. Do not change legacy readers or writers yet.
4. Record a migration checkpoint and source snapshot identifier. The backfill is rerunnable and idempotent.

### Phase B: import and quarantine

1. Backfill one legacy row at a time through the adapter, resolving `manifest_version` only within its app definition.
2. Insert canonical grants and source mapping in one transaction. Preserve source ids, timestamps, actor provenance and normalized payload digest.
3. Quarantine orphan app versions, cross-tenant scopes, unsupported grant shapes, duplicate active identities, invalid timestamps and missing revoke provenance. Quarantine is visible and counted; it is not a silent skip.
4. No canonical row is eligible for authorization until its mapping, version, tenant and scope checks pass.

### Phase C: dual-read parity, legacy owner

Legacy `AppGrantRow` remains the decision owner. The adapter performs canonical decisions in shadow mode and compares:

- exact app id and resolved `app_version_id`;
- tenant and scope;
- operation/resource/domain result;
- issue/expiry/revoke outcome;
- source and canonical payload digests.

Any mismatch blocks that tenant/app/scope partition. Parity must distinguish an intentional deny caused by quarantine from an authorization mismatch; do not turn quarantine into allow.

### Phase D: bounded canonical cutover

Cut over by tenant + app definition (and, if needed, scope partition), with an explicit ownership checkpoint:

1. stop new legacy grant writes for the partition;
2. drain or fence in-flight legacy mutations;
3. run a final delta backfill and zero-conflict parity report;
4. switch gateway reads and decisions to canonical rows;
5. translate legacy management commands through the adapter into canonical writes;
6. keep the legacy table as a read-only projection/mirror for the rollback window.

There must be one eligible writer and one authorization owner per partition. A row's mere presence in both tables is not a cutover signal.

### Phase E: canonical owner

Canonical grant rows and audit events are authoritative. Legacy rows may be refreshed only as a compatibility projection with source/target ids and checkpoint metadata. No new legacy-only grant or revoke may be accepted. New target-native APIs must not bypass canonical validation or issue a grant without an audit event.

## 6. Revoke, expiry and audit semantics

Authorization uses the domain contract's half-open validity rules:

- before `issued_at`: deny `NOT_YET_ISSUED`;
- at or after `expires_at`: deny `EXPIRED`;
- at or after `revoked_at`: deny `REVOKED`;
- all timestamps are timezone-aware UTC instants at the persistence boundary.

Expiry is automatic denial at read time; it is not a destructive delete and does not require a worker to run before access stops. A renewal creates a new generation or replaces an expired active identity according to the chosen unique-key strategy, retaining the old row for audit. Revoke is an append-preserving state transition: set `revoked_at` once, require `revoked_by`, preserve `revoke_reason`, and reject a second conflicting revoke. Revoke must be idempotent for the same actor/reason under the command idempotency key, while a different reason/actor is an explicit conflict requiring review.

Every issue, import, grant update, revoke, expiry observation, deny due to revoked/expired state, rollback, quarantine and cutover must emit an audit envelope containing at least:

- tenant, app id, app definition id, app version id and scope;
- canonical grant id and legacy source id where applicable;
- event type, decision/result and reason;
- actor/service identity, correlation/request id and migration checkpoint;
- issued/expiry/revoke timestamps and canonical payload digest;
- redacted provenance and schema version.

Audit rows/events are immutable and must not contain secrets or raw capability tokens. They survive grant deletion/cleanup and are not cascade-deleted with compatibility projections. Authorization audit volume may be sampled only for repeated denies if policy explicitly retains the aggregate and every issue/revoke decision remains complete.

## 7. Rollback and failure handling

Rollback is a forward ownership/routing change, not a destructive schema downgrade.

### Before canonical read cutover

Disable canonical writes/backfill for the affected partition, keep legacy reads/writes authoritative, and retain canonical rows, mappings, quarantine and audit evidence. Repair conflicts and rerun parity before retrying. Do not delete imported rows to hide a mismatch.

### After canonical read cutover, before legacy removal

Do not let both paths authorize or mutate concurrently. Stop canonical grant mutations/claims for the affected partition, fence the canonical writer generation, and keep canonical reads authoritative while repairing through the canonical adapter. The legacy table is a compatibility mirror, not a rollback authority after the read boundary.

### After canonical ownership

1. stop canonical issue/revoke workers for the partition and fence their generation;
2. let in-flight transactions finish or fail under CAS/idempotency rules;
3. continue authorization from the last valid canonical snapshot, defaulting to deny on incomplete reconciliation;
4. repair or replay through canonical commands using the same grant id and operation idempotency key;
5. never restore legacy writes for a canonical-owned partition and never issue a second grant to compensate for an unknown outcome;
6. record rollback checkpoint, affected scope, reason and any quarantined rows.

Database downgrade is allowed only before tenant data import and only after a preflight proves no dependent rows. After import, use forward corrective revisions. Never drop canonical rows, audit history or mappings as a rollback mechanism.

## 8. Removal criteria and cleanup order

Remove `AppGrantRow` writes, compatibility adapter and legacy table only after all of the following are evidenced for the declared retention and rollback window:

- every non-retired legacy row is mapped to exactly one canonical app version, tenant, scope and grant payload, or has an owner-approved quarantine/retirement decision;
- canonical and legacy shadow decisions have zero unexplained mismatches for the observation window;
- no legacy grant/revoke writer, worker or authorization reader remains eligible for canonical-owned partitions;
- expiry, revoke, renew and audit replay/retention checks pass from canonical rows;
- unique-key conflict reports are empty, and all mapping rows have immutable source/target identities and payload digests;
- rollback has been rehearsed as a forward canonical repair with no dual-owner interval;
- product/security owners approve removal and the rollback window has ended.

Cleanup order is dependency-safe:

1. disable legacy writes and remove legacy authorization reads;
2. retain the adapter in read-only diagnostics during the final retention period;
3. retain source mapping, audit and quarantine evidence;
4. remove legacy projection refreshes and then the compatibility adapter;
5. archive/export any required legacy rows;
6. drop `dynamic_app_grants` and its indexes only in a separate reviewed Alembic revision after no FK/reader depends on it.

Canonical `dynamic_app_versions`, canonical grants and audit history are not removed as part of legacy cleanup. A failed criterion means the old table remains retained and the partition stays canonical or compatibility-owned according to its last explicit checkpoint; no implicit global promotion is allowed.

## 9. Acceptance and verification freeze

This artifact is documentation-only. No tests, build, lint, format, compile, migration execution, or git verification checks are part of this change. Before implementation, the owning migration PR must add focused adapter/backfill/parity/revoke/expiry/rollback tests and database constraints, then run them only after the migration verification freeze is explicitly lifted.
