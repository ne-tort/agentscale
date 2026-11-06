# M04 persistence adapter contract: owner, scope, definition and version

Статус: migration-stage persistence contract, только документация. Baseline: 2026-11-01.

Документ задаёт конкретную границу persistence для M04 owner/scope registry и M04.e definition/version foundation. Он связывает T00 canonical anchors, T01 typed scope registry и M04.e definition/version adapters с ORM/Alembic/RLS, durable mapping/quarantine/audit и trusted transaction. Документ не является SQLAlchemy model, Alembic revision, backfill job, ORM implementation или разрешением на shared-environment DDL и positive writes.

## 1. Scope, ownership and fixed decisions

В этой границе участвуют четыре repository owner-а и один transaction owner:

| Owner | Canonical responsibility | Positive path |
|---|---|---|
| `OwnerAnchorRepository` (T00) | canonical tenant/company, cabinet and project parent proof; source anchor inventory | read/proof only until T01 gate |
| `ScopeRegistryRepository` (T01) | typed active scope identity, parent compatibility, owner state and scope mapping evidence | active scope may be returned only after T01 validation gate |
| `DefinitionVersionRepository` (M04.e public facade) | tenant-scoped definition ownership plus same-definition version resolution, manifest identity, immutable artifact and lifecycle | proof only; never an authorization decision |
| `DefinitionRepository` / `VersionRepository` (M04.e internal owners) | definition and version persistence slices behind the composite facade | no standalone caller-visible positive lookup or authorization decision |
| `TrustedPersistenceTransaction` | one transaction-local context, CAS/fencing, parent proofs, mapping/quarantine/audit and commit decision | sole boundary for any later positive child mutation |

`DefinitionVersionRepository` is the public composite facade over the definition and version owners. It must not expose a version lookup that can be used without definition and tenant proof. A simple `id`, `manifest_version`, `app_id` or copied `tenant_id` is an assertion or lookup input, never ownership proof.

Canonical tenant vocabulary is `tenant_id`; the physical parent is `companies.id`. Canonical owner anchors participating in the target FK/RLS contract use the physical `String(40)` shape after inventory. Existing longer values are classified and quarantined; they are never truncated, shortened, implicitly cast, hashed into replacement identity, or silently dropped. Stable legacy IDs that are not owner anchors retain their reviewed shape.

The only scope kinds are `tenant`, `cabinet`, `project` and `plugin_instance`. `(scope_kind, scope_id)` is a typed logical key, not a polymorphic FK. A registry row is eligible for ordinary resolution only when `owner_state = active`; `archived` and `quarantined` rows remain evidence for migration/security/audit roles and never satisfy authorization.

## 2. Shared repository value objects and outcomes

Every repository operation carries a server-created `MigrationEvidenceContext`:

```text
MigrationEvidenceContext = {
  tenant_id, cabinet_id?, project_id?, user_id?, role?,
  correlation_id, trace_id, span_id,
  source_kind?, source_id?, schema_version?, source_payload_digest?,
  expected_writer_generation?, checkpoint?
}
```

The context is resolved from authenticated server state and parent rows. Request/plugin `company_id`, tenant, cabinet, project, app, version, subject and instance fields are comparison assertions only. Missing, malformed, contradictory or caller-only context returns `deny`, `quarantine` or `system-failure`; it never selects a wider tenant.

Repository methods return a closed outcome vocabulary:

```text
allow | deny | quarantine | conflict | stale-generation | system-failure
```

`allow` at this boundary means only that the requested owner/definition/version proof is deterministic and eligible for the next gate. It does not authorize a capability, publish an instance or issue a grant.

The immutable proof objects must include source identity, canonical identity, source/canonical digests, checkpoint, parent evidence IDs and correlation. Downstream M05/M06 adapters consume the same digest-linked snapshot; they must not independently re-resolve a narrower identity and call that equivalent evidence.

## 3. Concrete repository contracts

### 3.1 T00 `OwnerAnchorRepository`

```text
OwnerAnchorRepository.resolve_tenant(
  asserted_tenant_id?, asserted_company_id?, context
) -> TenantAnchorOutcome

OwnerAnchorRepository.resolve_cabinet(
  tenant_id, cabinet_id, context
) -> CabinetAnchorOutcome

OwnerAnchorRepository.resolve_project(
  tenant_id, cabinet_id, project_id, context
) -> ProjectAnchorOutcome

OwnerAnchorRepository.prove_parent_compatibility(
  tenant_id, cabinet_id?, project_id?, context
) -> ParentProofOutcome
```

A `TenantAnchor` proves exactly one retained `companies.id`, status/deletion policy and physical type compatibility. A `CabinetAnchor` proves the exact cabinet row plus an active `cabinet_company_grants(cabinet_id, company_id)` relation or a separately approved owner relation. Nullable `cabinet_instances.company_id`/`owner_company_id` mirrors do not prove ownership. A `ProjectAnchor` proves the exact `(projects.id, projects.company_id, projects.cabinet_id)` tuple, retained lifecycle and active cabinet/company compatibility. Independent simple FKs are insufficient.

The repository must return duplicate/ambiguous evidence instead of collapsing it into an arbitrary row. It must preserve legacy assertions for mismatch audit while deriving authority only from the resolved parent. T00 has no method to publish an active scope, definition, version, instance or grant.

### 3.2 T01 `ScopeRegistryRepository`

```text
ScopeRegistryRepository.resolve_active(
  tenant_id, scope_kind, scope_id, expected_anchors?, context
) -> ActiveScopeOutcome

ScopeRegistryRepository.record_mapping(
  source_identity, source_payload_digest, candidate, checkpoint, context
) -> MappingOutcome

ScopeRegistryRepository.append_classification(
  source_identity, digest, classification, evidence, context
) -> QuarantineOrAuditOutcome

ScopeRegistryRepository.archive_or_quarantine(
  registry_id, expected_state, reason, context
) -> TransitionOutcome
```

`resolve_active` returns exactly one row with typed anchors. T01 may resolve tenant/cabinet/project evidence only; the `plugin_instance` branch remains deny/deferred until M05 materializes the exact instance key, adds both reciprocal registry/instance constraints, and records their `VALIDATE CONSTRAINT` evidence. T00/T01 cannot publish or positively resolve an instance merely because a registry row has `scope_id = plugin_instance_id`.

```text
tenant:          scope_id = tenant_id; cabinet/project/instance = NULL
cabinet:         scope_id = cabinet_id; tenant and cabinet required
project:         scope_id = project_id; tenant, cabinet and project required
plugin_instance: scope_id = plugin_instance_id; instance equal scope;
                 installation cabinet/project anchors retained when present
```

A project without a cabinet is invalid. A plugin instance never silently inherits project scope. The plugin-instance relation remains deferred/deny until the M05 exact instance key and reciprocal FK are validated. Scope resolution checks parent `EXISTS`, active membership/compatibility, registry uniqueness and `owner_state`; copied columns alone are not proof.

Mapping is idempotent by `(source_kind, source_id, canonical_schema_version)`. The first observed source digest is retained. The same identity and digest replay returns the original mapping; the same identity with a changed digest is `conflict` and durable quarantine, never overwrite. `ON CONFLICT DO NOTHING` is forbidden before digest comparison.

### 3.3 M04.e `DefinitionVersionRepository`

```text
DefinitionVersionRepository.resolve_definition(
  tenant_id, app_id, expected_definition_id?, context
) -> DefinitionOutcome

DefinitionVersionRepository.resolve_version(
  tenant_id, definition_id, manifest_version,
  expected_version_id?, expected_artifact_digest?, context
) -> VersionOutcome

DefinitionVersionRepository.prove_pair(
  tenant_id, definition_id, version_id, manifest_version,
  artifact_digest, context
) -> DefinitionVersionProofOutcome

DefinitionVersionRepository.record_mapping(
  source_identity, source_digest, schema_version, checkpoint, context
) -> MappingOutcome
```

Definition resolution is by `(tenant_id, app_id)` and proves `(definition_id, tenant_id)` against the retained company and active typed scope. Version resolution is by `(definition_id, manifest_version)` and proves `(version_id, definition_id)` plus immutable artifact digest and lifecycle. `version_id` alone, manifest version alone, app name alone or a caller-provided tenant is insufficient.

The proof must establish (and persist the mapping/schema identity used to establish it):

```text
(app_definition_id, tenant_id) -> dynamic_app_definitions(id, tenant_id)
(app_version_id, app_definition_id)
  -> dynamic_app_versions(id, app_definition_id)
(app_definition_id, manifest_version) -> exactly one version
version.artifact_digest == supplied/instance artifact digest
```

`app_id` is a stable lookup identity within tenant; it is not an owner FK. Current-version links are resolved under the staged cycle contract and may remain nullable only during migration staging. A definition/version with orphan, cross-tenant, cross-definition, duplicate manifest version, invalid lifecycle, missing/conflicting artifact, overlength owner ID or invalid current-version relation returns quarantine. No resolver may repair ownership by selecting the first matching row or by guessing from a name/slug.

### 3.4 Trusted transaction API

```text
TrustedPersistenceTransaction.run(
  authenticated_context,
  operation,
  expected_writer_generation?,
  command
) -> TransactionOutcome
```

There is no caller-visible split between a positive child mutation and its required evidence. A later M05/M06 transaction must compose the M04 repositories inside this same boundary, but M04 repositories remain read/proof owners and cannot create a grant or publish an instance.

## 4. Trusted transaction protocol

The transaction owner performs the following sequence without an intermediate commit:

1. Resolve authenticated principal to one `companies.id`; reject caller-only tenant/company values and set transaction-local `app.tenant_id` from the server result.
2. Set optional `app.cabinet_id`, `app.project_id`, `app.user_id`, `app.role`, correlation/trace/span and expected writer epoch from trusted context. Clear all settings at transaction end.
3. Lock or CAS the bounded partition/writer generation when the operation can mutate state. A stale expected epoch must produce a zero-row mutation and `stale-generation`; a post-read check is insufficient.
4. Resolve T00 parent anchors and prove active cabinet membership/project tuple compatibility with parent `EXISTS` predicates.
5. Resolve T01 active typed scope and compare every supplied assertion. Ambiguous, archived or quarantined scope is deny/quarantine.
6. Resolve M04.e definition by tenant/app and version by definition/manifest version. Prove the same-definition composite keys, lifecycle and immutable artifact equality.
7. Compare source identity/schema/digest and checkpoint. Replay of the same digest is idempotent; a changed digest is a conflict/quarantine.
8. Apply only the operation permitted by the current gate. M04 proof may be persisted as mapping/evidence, but it cannot enable a child positive write before all downstream gates.
9. Append the required redacted audit event and, for unsafe input, the quarantine record in the same trusted unit of work. An audit/quarantine failure aborts the enclosing positive operation.
10. Commit only when RLS `USING`/`WITH CHECK`, named FK/check constraints, parent proofs, role privileges and all applicable validation gates succeed.

The transaction must never use `SELECT` then unconstrained `INSERT/UPDATE`. Every mutation carries the full identity tuple and expected generation in the SQL predicate. A successful mutation is evidenced by the expected affected-row count; zero rows means stale/conflicting/missing parent and is never converted into success by rereading the row.

## 5. ORM and Alembic mapping

The target is expressed first in SQLAlchemy models and then in one linear Alembic chain from the confirmed repository head. These are logical slices, not executable revision names:

### T00 anchor and registration slice

- Register target model modules deliberately in `Base.metadata`; missing registration is a release blocker.
- Inspect `companies`, `cabinet_instances`, `projects`, memberships and cabinet grants as existing parents.
- Record canonical `String(40)` anchor shapes and compatibility tuples. Do not alter unrelated legacy ID columns merely to make them match.
- Create no positive repository path and no guessed FK from a polymorphic `scope_id`.

### T01 scope foundation slice

- Create empty `scope_registry`, owner mapping, quarantine and audit structures with named keys/checks and immutable source provenance.
- Add typed parent FKs only after parent key/type inventory; use `ON DELETE RESTRICT` or archive transitions for owner/security evidence.
- Add unique `(scope_kind, scope_id)` and a typed anchor key sufficient for downstream composite proof. Defer `plugin_instance_id` FK until M05 exact instance key exists.
- Install indexes for active resolution, source mapping and checkpoint lookup. Do not encode moving time in partial uniqueness predicates.

### M04.e definition/version slice

- Align canonical definition tenant owner columns only after overlength/malformed inventory and quarantine; add `(definition_id, tenant_id)` and `(version_id, definition_id)` unique keys.
- Add deterministic `(app_definition_id, manifest_version)` uniqueness, lifecycle checks and immutable artifact proof.
- Stage `current_version_id`/other cycles as nullable or deferred only when required; use stable named `use_alter=True`/follow-up constraints after both keys and rows exist.
- Add composite downstream FKs only after parent keys exist. Existing simple version FK or cascading owner FK is not sufficient proof.

### RLS and privileges slice

All new tenant-owned target tables use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`. Named `USING` and `WITH CHECK` policies require transaction-local tenant context, active typed scope and parent `EXISTS` proof. Missing/empty/malformed GUC context denies. API roles cannot bypass RLS or mutate audit history. Mapping/quarantine writes are limited to migration/security/audit roles; unresolved evidence is migration-admin only.

Policy checks must compare definition/version tenant and scope, cabinet membership, project tuple, lifecycle and (when downstream rows are present) exact instance/artifact/generation. RLS is an additional boundary, not a replacement for repository proof.

## 6. Quarantine and audit contract

Every source candidate receives a deterministic outcome, including known deny and unsupported input. Durable mapping stores source kind/id, schema version, source digest, canonical digest where representable, target/evidence ID, status, reason and checkpoint. Required quarantine classes include:

- missing, orphaned, deleted, duplicate or ambiguous tenant/cabinet/project parent;
- malformed or overlength owner anchor; cross-tenant/cabinet/project relation;
- impossible typed scope shape or archived/quarantined owner state;
- orphan/cross-definition/duplicate definition or version; version ID without same-definition proof;
- missing/conflicting immutable artifact, invalid lifecycle or current-version cycle;
- changed source digest, duplicate active identity, unsupported representation or broadening transformation.

`QuarantineRecord` is immutable, redacted, visible, counted and retryable. It retains source identity, all available resolved anchors, reason, digest, checkpoint and correlation, but no raw credentials, tokens, secrets or unredacted payload. Retry keeps source identity/schema and compares the digest again; repair is a new forward mapping. Quarantine is never queried by authorization and never counts as positive parity.

`CapabilityAuditRepository`/the M04 audit owner appends import, mapping, deny, quarantine, conflict, parent-proof failure, stale-generation, validation and gate events. Audit is append-only: API roles have no `UPDATE`/`DELETE`; corrections are new linked events. Optional target links may use `SET NULL`, while immutable source IDs/digests remain. Audit failure rolls back a dependent positive mutation and cannot turn a deny into allow.

## 7. Zero-row CAS and parent proof requirements

A mutation is eligible only when its database predicate includes the complete relevant identity:

```text
WHERE tenant_id = :resolved_tenant_id
  AND definition_id = :definition_id
  AND version_id = :version_id
  AND scope_kind = :scope_kind
  AND scope_id = :scope_id
  AND writer_generation = :expected_writer_generation
  AND lifecycle/status is eligible
  AND parent EXISTS proofs hold
```

For a narrowed/plugin operation, include exact `plugin_instance_id`, artifact digest, subject/session, runtime generation and lease identity as required by policy. The repository must treat `rowcount = 0` as the only safe result for stale writer, missing parent, changed artifact, mismatched scope, expired lease or concurrent state transition. It returns `stale-generation`, `deny`, `conflict` or `quarantine` with audit; it must not retry against another instance, app, version or scope.

Parent proof is required even where copied denormalized IDs are equal:

- tenant exists, is retained and matches `companies.id`;
- cabinet exists and has active tenant access/owner relation;
- project exists with exact `(company_id, cabinet_id)` compatibility and retained lifecycle;
- scope registry row is unique, active and shape-valid;
- definition belongs to the resolved tenant and active scope;
- version belongs to that definition, has deterministic manifest identity and immutable artifact;
- later instance rows prove exact composite identity and current runtime/fence state.

No `SELECT`-then-mutate sequence, application-only equality check or returned-row inspection can replace these atomic predicates.

## 8. No truncation, NOT VALID and VALIDATE gates

The migration line must preserve source evidence before changing shape:

1. Inventory every candidate owner ID and classify valid, retained/archived, malformed, overlength, orphan, duplicate and cross-parent values.
2. Persist source identity, original length/value digest, proposed canonical value only when it is exactly representable, and redacted quarantine for all invalid values.
3. Convert only canonical owner anchors whose values already fit the target type and whose parent proof is deterministic. No truncation, substring, silent cast, hash replacement or collision-prone normalization is allowed.
4. Create large FKs/checks as `NOT VALID` only as an operational staging tactic. `NOT VALID` applies no historical proof and is never a passed gate.
5. Keep new-write enforcement active while historical rows are classified. Invalid historical rows are quarantined/archived; they do not make new rows eligible.
6. Run a separately recorded `VALIDATE CONSTRAINT` gate for every staged FK/check, including named late reciprocal constraints and current-version cycles. Record constraint name, revision, partition, row counts/digests, validator identity, timestamp and result.
7. Tighten nullability, enable published/active positive paths or expose downstream repository eligibility only after all required validations and parent evidence exist.

A missing validation record, unknown constraint status or unresolved quarantine blocks the affected partition. Validation of one partition never silently promotes another.

## 9. Gates, handoff and rollback

M04/T01 handoff is ready only when each affected partition has deterministic tenant/cabinet/project parent proof, exactly one active typed scope where required, validated scope checks/RLS and zero unknown required parent mappings. M04.e handoff additionally requires `(definition_id, tenant_id)`, `(version_id, definition_id)`, deterministic manifest resolution, immutable artifact equality, lifecycle proof, validated staged constraints and zero unresolved required definition/version rows.

The handoff explicitly carries `canonical_positive_writes = false`. M05 cannot publish an instance and M06 cannot issue a grant solely because M04 resolution succeeded. Any later partition handoff must link the immutable source/canonical/binding digests, mapping schema/version, checkpoint, fence epoch and audit evidence to the immutable promotion record; `ready` or `blocked` evidence never enables a writer, and only an exact `promoted` record may do so after final-delta/parity and legacy-writer fencing gates.

Before any later cutover, legacy definitions/versions and legacy readers/writers remain authoritative. If a partition fails, stop its mapping/backfill, retain mappings/quarantine/audit and repair forward. Before tenant data import, empty newly created objects may be removed after dependency review and recorded rollback inventory. After import, operational rollback is not schema downgrade and never deletes owner, mapping, quarantine or audit evidence. After canonical read cutover there is no dual-owner rollback: fence the canonical writer, fail closed, keep canonical reads authoritative, and repair only through forward fenced CAS/idempotent commands linked to a new promotion/audit record.

## 10. Acceptance artifacts and unresolved risks

The implementation PR must provide static acceptance artifacts for:

- T00 type/length inventory proving no owner-anchor truncation and no shortening of unrelated IDs;
- T01 four-shape registry checks, duplicate/ambiguous parent classification, project compatibility and plugin-instance deferral;
- M04.e composite definition/version keys, manifest uniqueness, artifact equality and current-version cycle;
- no-context, wrong-tenant, wrong-cabinet, cross-project and archived/quarantined RLS denial;
- source-digest replay/conflict, quarantine isolation, append-only audit and audit-failure rollback;
- stale writer/runtime/lease zero-row CAS and parent proof in the same mutation predicate;
- `NOT VALID` staging followed by named `VALIDATE CONSTRAINT` evidence before eligibility;
- handoff records with source/canonical digests, checkpoint, identity snapshot, correlation and `canonical_positive_writes=false`.

Known blockers remain: current dynamic-app owner IDs may be wider than `String(40)`; existing cabinet/project mirrors do not independently prove ownership; current definition/version/instance adapters may not carry shared durable evidence; plugin-instance scope and reciprocal keys are deferred to M05; PostgreSQL RLS, composite FK, append-only trigger and zero-row CAS behavior require implementation review. No positive child write is safe while any blocker, unresolved parent/version, unknown mapping, unvalidated constraint or unfenced writer remains.

## 11. Verification freeze

Для этой documentation-only задачи tests, build, lint, format, compile, Alembic execution, migration execution, database changes, environment changes and git checks intentionally were not run. Code, ORM, core docs, shared environments and databases were not changed. Acceptance artifacts are design requirements only; verification remains frozen until the migration is implemented, reviewed and an explicit coordinator decision lifts the freeze.
