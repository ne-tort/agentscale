# Persistence target map: runtime, resources, capabilities, equipment

Статус: migration artifact, только документация. Дата baseline: 2026-11-01.

Документ связывает новые foundation-типы с будущими Alembic revisions. Он не является миграцией, не меняет ORM и не утверждает немедленный cutover. Источник DDL после согласования остаётся в SQLAlchemy-моделях; Alembic-файлы должны быть сгенерированы и проверены отдельным migration PR.

## 1. Scope and invariants

В карте участвуют четыре связанные области:

- runtime: `capability_bundles`, `project_runtime_revisions`, `materialization_entries`;
- resources: `resources`, `resource_versions`, `resource_bindings`;
- capabilities: typed capability manifests/grants and their immutable runtime snapshot, а не произвольный JSONB permission blob;
- equipment foundation: `equipment_v2_*` ingestion, catalog snapshots, requests, matching, tenders and budget allocations;
- agent control plane: durable `agent_runs` and human `approval_requests`, separate from session transcript lifecycle.

Общие инварианты:

1. Применённая runtime revision immutable. Sync/reload создаёт новую revision; старую не переписывает.
2. Resource root и versions append-only по смыслу. Binding может переключать version, но не мутирует опубликованный payload.
3. Capability manifest — requested/declared contract. Effective grant вычисляется policy-слоем и должен быть зафиксирован в capability bundle вместе с версией и digest.
4. OpenSearch остаётся rebuildable projection. Нормализованные PostgreSQL snapshots/history и raw artifacts являются source of truth для equipment.
5. Legacy `module_instances`/JSONB rows и новая equipment-схема сосуществуют на период миграции. Никакого dual-write без explicit cutover checkpoint и parity evidence.
6. Не добавлять FK «на будущее» к таблицам, которых ещё нет в core schema. Сначала появляется владелец/anchor, затем зависимая таблица или constraint отдельной ревизией.

## 2. Target revision chain

Ниже `M*` — логические имена будущих ревизий, а не готовые `revision` ids. При реализации они должны стать одной линейной цепочкой `down_revision`, согласованной с актуальным Alembic head.

| Порядок | Будущая ревизия | Содержимое | Предусловие | Rollback boundary |
|---:|---|---|---|---|
| M00 | `foundation_anchors` | Проверка/подготовка tenant/company, cabinet/project и actor ownership anchors; общие enum/check conventions; необходимые базовые RLS helper functions | актуальные `companies`, `cabinet_instances`, `projects`, identity | только если не создаёт destructive DDL; удалить только новые helper objects |
| M01 | `capability_bundles` | `capability_bundles` и digest uniqueness | M00; не зависит от project | drop таблицы допустим только до появления ссылок |
| M02 | `project_runtime_revisions` | `project_runtime_revisions`, project FK, revision number uniqueness, lifecycle checks/indexes | M01 и `projects` | downgrade только при пустой таблице либо после archival/export |
| M03 | `materialization_entries` | entries, revision FK cascade, `(revision_id, workspace_path)` uniqueness, lookup indexes | M02 | сначала удалить entries, затем таблицу; не удалять revision отдельно |
| M04 | `resource_roots_versions` | `resources`, `resource_versions`, root/version FKs, uniqueness and status indexes | M00; scope anchors должны быть определены | downgrade только до resource import; published versions require archive gate |
| M05 | `resource_bindings` | binding rows, target lookup, scope/session semantics and project filter index | M04; target scope contract documented | remove bindings before roots; never silently rebind on downgrade |
| M06 | `capability_snapshot_links` | explicit link/snapshot metadata from runtime bundle to resource versions and capability manifests, if required by final model; otherwise retain bundle JSONB with documented schema | M01, M04/M05 | remove only unreferenced links; bundle digest remains audit evidence |
| M07 | `equipment_ingestion_catalog` | `equipment_v2_ingestion_runs`, `equipment_v2_catalog_snapshots`, constraints/indexes and raw snapshot provenance | M00; tenant/source ownership contract | retain raw snapshot archive; rollback schema only after export |
| M08 | `equipment_offers_matching` | catalog offers, requests, request lines, candidate groups, selected-offer FK | M07; request/tender ownership policy | clear selection/candidate references before dropping child tables |
| M09 | `equipment_tenders_budget` | tenders and budget allocations; later add tender line/procurement tables if approved by domain contract | M08; lifecycle/status rules | detach/archive operational rows; do not cascade-delete source snapshots |
| M10 | `agent_control_plane` | `agent_runs` and `approval_requests`; idempotency boundary, durable status/heartbeat, approval expiry and decision audit | current `projects`, `agent_sessions`, `employees`; no transcript backfill required | stop new run creation, retain audit rows, restore legacy execution path; do not delete runs/decisions |
| M11 | `equipment_projection_cutover` | optional compatibility/import markers, parity checkpoints and projection ownership; no silent legacy deletion | M10; parity and import sign-off | disable new writer, restore legacy read path, keep snapshots and audit trail |

A revision may be split for operational lock duration, but order must preserve parent-before-child creation and child-before-parent removal. Data backfills/imports are separate, explicitly named revisions or jobs; they must not be hidden in autogenerate output.

## 3. Foundation type to persistence map

### 3.1 Runtime revisions

| Type/model | Target table | Key columns and constraints | Required indexes | FK/RLS notes |
|---|---|---|---|---|
| `CapabilityBundleRow` | `capability_bundles` | `id` PK; `manifest` JSONB NOT NULL default `{}`; `digest` NOT NULL UNIQUE; `created_at` | unique btree on `digest`; optional GIN only after measured manifest query need | Bundle is content-addressed and not tenant-owned by current model. If manifests become tenant-private, add explicit owner/tenant anchor before RLS; do not infer ownership from JSONB. |
| `ProjectRuntimeRevisionRow` | `project_runtime_revisions` | `id` PK; `project_id` FK `projects.id` CASCADE; `revision` NOT NULL; `bundle_id` FK `capability_bundles.id` RESTRICT; unique `(project_id, revision)`; lifecycle state | unique `(project_id, revision)`; lookup `(project_id, state, revision DESC)`; optional `(project_id, created_at DESC)` if latest query is not covered | `project_id` is the tenant/cabinet access anchor through `projects`. `bundle_id RESTRICT` preserves audit snapshot. `applied_revision_id` currently is a logical self-reference and should become a self-FK only after state/creation semantics are settled; avoid a forward or cyclic migration surprise. |
| `MaterializationEntryRow` | `materialization_entries` | `revision_id` FK to revisions CASCADE; path uniqueness `(revision_id, workspace_path)`; `(revision_id, idempotency_key)` uniqueness; immutable operation digest; status/generation/hash/provenance | unique `(revision_id, workspace_path)` and `(revision_id, idempotency_key)`; lookup `(revision_id, status)`; `(revision_id, generation)` for reconcile | No direct project FK is necessary: tenant scope is inherited through revision. RLS policy should use `EXISTS` through `project_runtime_revisions -> projects`, or a denormalized project/tenant key only if performance proves it necessary. `idempotency_key` and operation digest must be derived from canonical desired payload/provenance and must exclude mutable apply diagnostics. |

State checks should be database-enforced where stable: runtime revision states `planned`, `validated`, `applying`, `ready`, `failed`, `superseded`; materialization states `planned`, `staged`, `applied`, `failed`. Transition legality remains application/service policy and must fail closed. The domain materialization contract additionally uses `applying` and `skipped`; the persistence adapter must either map those explicitly to the foundation lifecycle or reject them, never silently coerce them to `staged`/`planned`.

`ProjectRow` should eventually expose `desired_revision_id` and `applied_revision_id` as FKs to `project_runtime_revisions.id`. Add those columns only in a later revision after the revision table exists, backfill nulls, then add indexes and (if required) constraints. Do not make them non-null while legacy projects can still be launched without a revision.

### 3.2 Resources

| Type/model | Target table | Key columns and constraints | Required indexes | FK/RLS notes |
|---|---|---|---|---|
| `ResourceRow` | `resources` | `id` PK; `(scope_kind, scope_id, kind, slug)` UNIQUE; `kind`, `slug`, `scope_*`, metadata JSONB, nullable `active_version` | existing model indexes: unique scope/kind/slug and `(scope_kind, scope_id)`; add `(scope_kind, scope_id, kind, active_version)` only for a proven active lookup | `scope_id` is polymorphic by design, so PostgreSQL cannot enforce a normal FK. RLS must validate scope ownership in service/query policy; do not use a misleading FK to one owner table. `active_version` should become an FK to the resource's version only after a deferrable/self-cycle design is chosen. |
| `ResourceVersionRow` | `resource_versions` | `resource_id` FK `resources.id` CASCADE; unique `(resource_id, number)`; status/payload/checksum/published_at | existing unique `(resource_id, number)` and `(resource_id, status)`; optional partial index for published version if query pattern warrants it | RLS inherited through `resources`; policy must prevent cross-scope version insertion. Published payload is immutable by application policy; a DB trigger is optional and should be a separate security-reviewed revision. |
| `ResourceBindingRow` | `resource_bindings` | `resource_id` FK `resources.id` CASCADE; unique `(resource_id, target_scope_kind, target_scope_id, session_id)` for session-bound rows plus a partial unique index for `session_id IS NULL`; binding kind/version/project_ids | existing target index `(target_scope_kind, target_scope_id, session_id)` and partial uniqueness for session-less bindings; add `(resource_id, version_number)` only if resolving by pinned version is frequent; GIN on `project_ids` only if array containment is a measured hot path | `target_scope_*` is polymorphic. `version_number` is a logical reference to `resource_versions.number`, not a safe FK by itself; prefer nullable `version_id` FK in a later model revision if exact version integrity is required. RLS must enforce source resource visibility and target scope membership, including `session_id` and `project_ids`. |

Resource versions may reference content assets in a later step, but that FK must be added only after the content asset lifecycle and deletion semantics are agreed. A resource `payload` must not contain secret material; secret references remain descriptors/leases.

### 3.3 Capabilities

The typed domain contracts (`CapabilityManifest`, `CapabilityGrant`, `CapabilityRequest`, `CapabilityContext`, audit envelope) are not themselves persistence tables. Their persistence split is:

| Contract | Durable representation | Migration rule |
|---|---|---|
| Manifest (`capability`, `version`, operations, metadata) | canonical manifest inside `capability_bundles.manifest`; optional normalized registry later | First ship snapshot storage. Add a registry only when publication/version discovery requires relational queries. The bundle digest must cover the canonical serialized manifest. For MCP attachments, that canonical snapshot must also include project/profile/server identity, attachment id, package name/version, qualified package digest, and the normalized allowlist; a standalone runtime lock table is optional only if this snapshot linkage is preserved. |
| Effective grant | capability bundle snapshot plus future grant/policy tables | Do not persist a grant as an implicit boolean in resource JSONB. A future grant table needs explicit subject, scope, operations, issued/expiry and policy provenance. |
| Request/context | request/audit event stream or operational audit table | `company_id`, subject, project, cabinet, pod, session and trace identifiers must be captured without secrets. Capability checks remain server-side on every call. |
| Audit envelope | existing/new audit sink, separate from bundle | Audit retention and tenant visibility are separate from runtime rollback. Never cascade-delete security audit records when a revision is purged. |

Recommended future normalized tables, only if needed by query/load: `capability_definitions`, `capability_grants`, `capability_audit_events`. They must not precede the immutable bundle snapshot: runtime application needs a stable digest even if the registry later changes.

## 4. Agent control-plane map

The control plane is durable operational state, not a replacement for `agent_sessions`/`agent_events` transcript storage. A run may outlive a rotated or deleted session and must remain auditable.

| Type/model | Target table | Parent FKs and delete semantics | Required indexes/uniqueness | RLS/ownership |
|---|---|---|---|---|
| `AgentRunRow` | `agent_runs` | `project_id → projects.id ON DELETE CASCADE`; `session_id → agent_sessions.id ON DELETE SET NULL`; `requested_by_employee_id → employees.id ON DELETE SET NULL` | unique `(project_id, idempotency_key)`; `(project_id, status, created_at)`; `(session_id, created_at)`; status lookup index | tenant resolved through project. `SET NULL` preserves run audit when session/employee disappears; project cascade is intentional current ownership policy. |
| `ApprovalRequestRow` | `approval_requests` | `run_id → agent_runs.id ON DELETE CASCADE`; `decided_by_employee_id → employees.id ON DELETE SET NULL` | unique `(run_id, tool_call_id)`; `(status, expires_at)`; status lookup index | tenant resolved through run → project. Decision actor is nullable historical identity; policy must require an authorized employee at decision time. |

`agent_runs.idempotency_key` is scoped to a project, not globally: retries of the same request must converge on one run while two projects may reuse a client key. Approval rows are never recreated for the same `(run_id, tool_call_id)`; expiry/decision is a state transition. Do not store secrets in `input_payload`, `output_payload` or `requested_payload`; persist references and redacted diagnostics.

RLS policies use parent `EXISTS` predicates (`agent_runs.project_id → projects`; `approval_requests.run_id → agent_runs → projects`) and must deny missing tenant context. A future compliance-retention change may replace the project cascade, but that requires a separate ownership/retention revision rather than weakening the FK during initial rollout.

## 5. Equipment foundation map

The current equipment v2 models are deliberately independent of legacy `mod_equipment` JSONB. They should be introduced in dependency order below.

| Type/model | Target table | Parent FKs | Required indexes/uniqueness | RLS/ownership |
|---|---|---|---|---|
| `EquipmentIngestionRunRow` | `equipment_v2_ingestion_runs` | none in current model | `(tenant_id)`; `(tenant_id, status, created_at DESC)` for worker claim/list; consider unique `(tenant_id, source_id, external_run_id)` only when idempotency key exists | tenant-only policy. `tenant_id` is currently a string without FK; resolve canonical tenant/company anchor before adding hard FK. |
| `EquipmentCatalogSnapshotRow` | `equipment_v2_catalog_snapshots` | `ingestion_run_id` FK RESTRICT | unique `(ingestion_run_id, content_hash)`; `(ingestion_run_id)`; `(source_id, captured_at DESC)` for latest snapshot | inherit tenant through ingestion run. RLS should use parent `EXISTS`, not trust a duplicated source id. RESTRICT protects provenance. |
| `EquipmentCatalogOfferRow` | `equipment_v2_catalog_offers` | `snapshot_id` FK CASCADE; `selected_offer_id` elsewhere may point here | unique `(snapshot_id, external_key)`; `(snapshot_id)`; `(supplier_key, external_key)` only if global supplier identity is explicitly canonical | inherit tenant through snapshot. `unit_price`/currency are authoritative snapshot facts; updates should create a new snapshot, not overwrite history. |
| `EquipmentRequestRow` | `equipment_v2_requests` | none currently; future project/cabinet/session FK must be explicit | `(tenant_id)`; `(tenant_id, status, created_at DESC)`; idempotency key unique when introduced | tenant-only initially. Before project-scoped cutover, add nullable project/cabinet/session anchors and backfill, then tighten RLS/NOT NULL in a later revision. |
| `EquipmentRequestLineRow` | `equipment_v2_request_lines` | `request_id` FK CASCADE | unique `(request_id, line_number)`; `(request_id)`; optional `(request_id, status)` if status is added | inherit tenant through request. Quantity/unit/specification are request facts and may be edited under request ACL. |
| `EquipmentCandidateGroupRow` | `equipment_v2_candidate_groups` | `request_line_id` FK CASCADE; `selected_offer_id` FK SET NULL to offers | `(request_line_id)`; optional `(request_line_id, status)`; validate selected offer belongs to a visible snapshot in service/constraint layer | inherit ownership through request line. Candidate groups express agent intent; they must not be allowed to author authoritative price/availability. `offer_ids` JSONB is a denormalized compatibility field; future normalized join table should be additive before removal. |
| `EquipmentTenderRow` | `equipment_v2_tenders` | `request_id` FK RESTRICT | `(tenant_id)`; `(request_id, status)`; `(status, closes_at)` for worker/readiness queries | tenant policy plus request ownership. `candidate_group_ids` JSONB should eventually become `tender_items` with FK; do not pretend array IDs are referentially constrained. |
| `EquipmentBudgetAllocationRow` | `equipment_v2_budget_allocations` | `request_id` FK CASCADE | `(request_id)`; `(request_id, status)`; `(cost_center, currency)` only if reporting needs it | inherit tenant through request. Amount/currency are financial facts; rollback must preserve an audit/export snapshot rather than silently recompute from changed offers. |

The model currently uses `tenant_id` strings while the live platform model prominently uses `companies`, `cabinet_instances` and `projects`. Before M07, choose one canonical ownership vocabulary and document the mapping. Options are: (a) introduce a tenant anchor and add real FKs, or (b) explicitly make `company_id` the tenant key and rename/add compatibility columns. Do not mix both in RLS predicates.

### Equipment relations that are intentionally deferred

The domain architecture calls for normalized sources, suppliers, product identity, offers, tender items, builds, procurement lines and export snapshots. The current `equipment_v2` foundation does not yet model all of them. Add them only as separate revisions after lifecycle contracts exist:

- `equipment_sources` / source credentials references and idempotent ingestion identity;
- normalized `catalog_items`, supplier identity and source accounts;
- `tender_items` replacing `candidate_group_ids` JSONB;
- build/slot/component relations;
- procurement and budget export snapshots with immutable source references.

OpenSearch index names, aliases and mappings are not Alembic DDL. Store only projection status/reference in PostgreSQL; create/rebuild/drop physical indexes through the search projection workflow with a revisioned projection marker.

## 6. RLS and FK design rules

### RLS

Use the established transaction-local context (`app.tenant_id`, optional `app.cabinet_id`, `app.user_id`, `app.role`). Every tenant-owned new table should have `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` in its implementation revision, with `USING` and `WITH CHECK` predicates. Policy shape:

- runtime revisions/materialization: resolve tenant/cabinet through `project_id -> projects`;
- resources: resolve ownership through an explicit scope registry or owner relation; polymorphic `scope_id` alone is not enough for a safe SQL policy;
- equipment ingestion/requests: direct tenant predicate only after the tenant key is canonical;
- equipment child rows: parent `EXISTS` predicates, so a copied or malformed child tenant value cannot widen access;
- capability audit: tenant read plus policy-controlled write; never grant API users a platform-admin bypass.

The migration must include RLS policy names in its rollback inventory. Policies should be created after tables/parent FKs and before application cutover. CI should later exercise no-context, wrong-tenant, wrong-cabinet and cross-project reads/writes.

### FK

- Prefer real FKs for stable same-schema parents: revision → project/bundle, materialization → revision, version/binding → resource, equipment children → request/snapshot/offer.
- Use `CASCADE` only for derived children whose loss is intentional (`materialization_entries`, request lines, offers within a snapshot). Use `RESTRICT` for audit/provenance parents (`capability_bundles`, ingestion runs, requests referenced by tenders). Use `SET NULL` only for optional historical selections.
- Do not use a FK for polymorphic `(scope_kind, scope_id)` or `(target_scope_kind, target_scope_id)` until a scope registry exists.
- Do not use a FK to an array of IDs in JSONB. Introduce a join table first.
- Add cross-parent consistency constraints (for example selected offer belongs to the same tenant/request) in a later migration or service transaction; a simple FK alone does not prove ownership alignment.

## 7. Data import and cutover ordering

1. **Inventory and freeze contract.** Record current Alembic head, model registration, ownership vocabulary, status enums and legacy row counts. This is a prerequisite, not part of the target DDL.
2. **Create empty foundation tables.** Apply M00–M05 with indexes and RLS. Do not change legacy readers/writers yet. Ensure `equipment_v2` models are deliberately registered in `Base.metadata` in the implementation change before autogenerate.
3. **Backfill/import immutable roots.** Import resource versions and runtime manifests only from trusted sources, preserving source IDs/digests. Import equipment raw artifacts and catalog snapshots first, then offers. Every run is idempotent by source/content hash.
4. **Import dependent domain data.** Requests → request lines → candidate groups; then selections, tenders and budget allocations. Invalid legacy references go to a quarantine report, not to guessed FKs.
5. **Build projections.** Rebuild OpenSearch from snapshots. Record projection index name, mapping version, source snapshot and completion marker. A failed projection keeps the database snapshot valid and does not mark runtime/equipment data as ready.
6. **Dual-read parity.** Read target typed services alongside legacy for a bounded checkpoint. Compare counts, ownership, statuses, prices/currencies, selection semantics and source hashes. Do not dual-write authoritative offers until conflict resolution is defined.
7. **Typed cutover.** Switch readers first where target data is complete; switch writers by bounded domain (resources/runtime, then equipment ingestion/matching, then tender/budget). Capability grants and RLS remain enforced on both paths.
8. **Retire compatibility.** After retention and rollback window, disable legacy writes, preserve an export/import marker, then remove legacy adapters in a later code change. Dropping JSONB rows or old indexes is never part of the first schema migration.

## 8. Rollback and failure handling

### Migration failure

Alembic `upgrade head` must leave the transaction or revision in a recoverable state. If a revision contains a non-transactional operation (for example an external projection call), split it: commit database DDL/data first, then run the projection job with an explicit marker and retry semantics.

### Runtime apply rollback

A failed runtime apply transitions the revision to `failed`; it does not delete the bundle or materialization provenance. Rollback selects the previous `ready` revision and records the transition. Superseded revisions can be garbage-collected only when no project/pod/audit reference remains and retention policy permits it.

### Resource rollback

Rollback changes a binding to a previous immutable version. It does not delete a published version or rewrite its payload. A failed resource import is quarantined by checksum/source identity and can be retried without changing the active version.

### Agent control-plane rollback

Agent control-plane rollback is forward-compatible and audit-preserving:

- stop creating new runs/approval requests behind a feature flag;
- leave existing `agent_runs` and `approval_requests` rows readable and resumable;
- route new execution to the legacy path if needed, while retaining idempotency keys and correlation IDs;
- never downgrade by deleting run or decision history; repair state with a forward revision or explicit reconciliation job.

### Equipment rollback

Equipment rollback is snapshot-oriented:

- stop the new ingestion writer or projection consumer;
- keep ingestion runs, raw artifacts and normalized snapshots;
- restore the previous projection marker/index from its snapshot, or rebuild it;
- restore typed reads to the prior completed checkpoint;
- preserve tender/budget audit/export records and never recompute historical financial facts from current offers;
- only after the rollback window, remove compatibility data in a separately approved cleanup revision.

A schema downgrade is not the normal operational rollback for imported tenant data. Prefer forward corrective revisions and projection rebuilds. Any destructive downgrade requires a preflight row-count/export check and an explicit retention decision.

## 9. Implementation handoff checklist

Before creating the first Alembic revision from this map, the implementation PR must answer:

- Is `Base.metadata` registration intentional for `equipment_v2` and all new tables?
- What is the canonical tenant key: `tenant_id`, `company_id`, or a new tenant anchor?
- Which runtime project columns receive `desired_revision_id`/`applied_revision_id`, and when can they become non-null?
- Is capability registry persistence needed now, or is bundle JSONB plus digest sufficient for Phase 1? If MCP lock rows are not separate, where is the canonical lock snapshot linked into the bundle digest?
- Are polymorphic resource scopes accepted with policy-only ownership, or is a scope registry required?
- Which adapter mapping preserves domain materialization statuses `applying`/`skipped` and the digest/idempotency identity without coercion?
- Which equipment legacy rows are imported, which are quarantined, and what is the parity acceptance threshold?
- Which indexes are justified by measured queries versus merely plausible?
- Which RLS policy tests and downgrade smoke checks will be executed after the migration verification freeze ends?

Until these answers are recorded, this document is the target map only; no shared environment should receive the foundation schema manually.
