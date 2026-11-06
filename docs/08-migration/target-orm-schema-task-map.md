# Target ORM schema task map

Status: migration-stage implementation backlog, documentation only. Baseline: 2026-11-01. This map is derived from [`persistence-implementation-phase.md`](persistence-implementation-phase.md). It defines bounded ORM/model tasks and their repository handoffs; it does not change ORM, application code, Alembic, database state, or shared environments.

## 0. Non-negotiable execution rules

- The implementation must create one linear, parent-before-child Alembic chain from the confirmed repository head. The planned slots are `2026110101` through `2026110110`; they are not permission to execute Alembic or to rewrite an applied revision.
- Each task below owns only its listed model-file zone. A task may add imports to `models/__init__.py` only as the explicit registration step for its own new model module. It must not opportunistically change another task's model zone.
- Create empty target structures first. Inventory, conversion, backfill, quarantine, constraint validation, shadow parity, final-delta drain, fencing, and promotion are separately recorded gates. Backfill is never hidden in autogenerate.
- Never truncate, shorten, silently cast, or hash-replace an existing identifier. `String(40)` is mandatory only for canonical tenant and typed owner anchors that participate in the target FK/RLS contract. Stable legacy/application IDs that are not owner anchors retain their reviewed physical shape until a separate decision. Overlength or malformed values are quarantined with source identity and digest.
- No positive canonical grant, published plugin instance, runtime/materialization apply, resource binding, automation intent, outbox delivery ownership, or promotion may occur before all applicable gates pass atomically and an immutable promotion record enables the writer for the exact partition.
- `NOT VALID` is a staging state, not a passed gate. Every staged FK/check must have explicit validation evidence before nullability tightening or positive writes. `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, named `USING` and `WITH CHECK` policies, and trusted-role privileges are required for tenant-owned target tables.
- Security/provenance parents use `RESTRICT` or archive-only semantics. `CASCADE` is limited to intentionally derived children. Audit, mapping, quarantine, promotion, and provenance evidence never cascades away. Downgrade is not operational rollback after tenant data import; repair is forward-only.
- The target repository handoff is part of every task. Repositories must use the existing transaction-scoped unit of work, authenticated server context, RLS GUCs, and CAS/fencing. No repository exposes an unconstrained positive insert.

## 1. Shared anchor and registration task

**Task T00: reserve canonical anchor contract and model registration.**

- **Exclusive model-file zone:** `apps/api/src/prodavan/infrastructure/persistence/models/__init__.py` for deliberate imports only; no schema class ownership. Canonical parent tables (`companies`, `cabinet_instances`, `projects`) are inspected, not modified by this task.
- **Parent/FK order:** confirm that `companies.id`, `cabinet_instances.id`, and `projects.id` are available before any new registry or dependent FK. Record parent compatibility requirements: project company/cabinet tuple and active cabinet-company membership. Do not use independent simple FKs as proof of the tuple.
- **Type rule:** record `companies.id`, cabinet, and project anchor shape as `String(40)`. Classify every existing `String(80)`/`String(64)` column as canonical owner anchor or unrelated stable legacy ID before any conversion. No truncation or implicit cast.
- **Registration:** all new model modules are imported before model-derived Alembic generation. Missing `Base.metadata` registration is a release blocker.
- **Handoff:** provide `ScopeRegistryRepository` and later repositories with one canonical tenant vocabulary (`tenant_id` logically, `companies.id` physically), typed anchor proof, and migration provenance inputs. No positive repository path is enabled by T00.

## 2. Scope registry and owner mapping

**Task T01: create owner/scope registry foundation.** Planned revision: `2026110101_persistence_owner_scope_foundation.py`.

- **Exclusive model-file zone:** new `models/owner_registry.py`; `models/__init__.py` registration only. Do not edit dynamic-app classes in this task.
- **Rows and keys:** create `scope_registry` with exactly `tenant`, `cabinet`, `project`, and `plugin_instance` shapes; `scope_kind`, `scope_id`, canonical `tenant_id`, optional `cabinet_id`, `project_id`, `plugin_instance_id`, `owner_state`, source identity, schema version, payload digest, checkpoint, and timestamps. Enforce unique `(scope_kind, scope_id)` plus a unique typed anchor tuple sufficient for downstream composite FKs.
- **Parent-before-child:** add typed `tenant_id -> companies.id`, `cabinet_id -> cabinet_instances.id`, and `project_id -> projects.id` only after parent compatibility inventory. The plugin-instance FK is deferred until T03/T04; use a named late constraint for the cyclic edge. Registry rows must not pretend `(scope_kind, scope_id)` is a polymorphic FK.
- **Shape checks:** database checks reject tenant rows with cabinet/project/plugin anchors, cabinet rows without matching cabinet identity, project rows without cabinet/project identity, and plugin rows where `plugin_instance_id != scope_id`. Plugin installation scope must be explicit; no silent inheritance.
- **RLS/FORCE/validation:** enable and force RLS; active rows are eligible for ordinary positive reads/writes, archived/quarantined rows remain migration evidence. Any large FK/check may start `NOT VALID`, but T01 records a later validation task and blocks positive writes until `VALIDATE CONSTRAINT` evidence exists.
- **Indexes/uniqueness:** `(scope_kind, scope_id)` unique; typed anchor lookup indexes for `(tenant_id, cabinet_id, project_id)` and active-state resolution; source mapping uniqueness by `(source_kind, source_id, schema_version)` with digest conflict handling.
- **Retention/ondelete:** registry/security rows use `ON DELETE RESTRICT` or archive transitions. Mapping and quarantine rows are append-only and never cascade-delete.
- **Backfill/quarantine:** inventory and resumably map one source row per transaction by `(source_kind, source_id, schema_version, payload_digest)`. Missing, deleted, malformed, overlength, ambiguous, duplicate, cross-tenant, and cross-cabinet rows go to redacted quarantine and audit. Quarantine cannot satisfy scope resolution.
- **Handoff:** `ScopeRegistryRepository` returns one active typed row or explicit deny/quarantine/conflict. `OwnerMappingRepository` owns idempotent source mapping and checkpointing. No dynamic app, grant, resource, or automation positive writes use this registry until its parent and RLS gates pass.

## 3. Dynamic app definition/version anchors

**Task T02: align definitions, versions, and reciprocal definition/version identity.** Planned revision: `2026110102_persistence_definition_version_alignment.py`.

- **Exclusive model-file zone:** existing `models/dynamic_apps.py`, definition and version classes only. Do not change grant classes, instance classes, or canonical grant models here.
- **Definition owner key:** align canonical `dynamic_app_definitions.tenant_id` to `String(40)` only after inventory and quarantine; add real owner FK to `companies.id` with `RESTRICT`/archive semantics. Expose unique `(id, tenant_id)` for children. Preserve unrelated stable `id` shape if it is not the canonical owner anchor.
- **Scope proof:** definition `scope_kind/scope_id` resolves through `scope_registry`; enforce tenant/cabinet/project shape and equality with definition tenant. A generic FK from `scope_id` to any owner table is forbidden.
- **Version proof:** preserve/establish unique `(app_definition_id, manifest_version)` and `(id, app_definition_id)` keys. Add same-definition composite FKs for version consumers. `app_id` is stable lookup identity within tenant/definition, not an owner FK.
- **Parent-before-child and cycles:** create definition owner key, then version keys and checks. Stage `current_version_id` as nullable/deferred if required; add named `use_alter=True`/late FK only after version rows exist. Do not make current-version linkage non-null while legacy definitions can lack it.
- **Checks/indexes:** lifecycle/artifact checks; unique `(tenant_id, app_id)` where contract requires it; unique definition/version manifest key; lookup indexes by tenant, scope, lifecycle, and app ID. No uniqueness on `manifest_version` alone.
- **RLS/FORCE/validation:** owner tables receive `ENABLE/FORCE RLS` with deny-by-default context and parent `EXISTS`; validate all `NOT VALID` owner/same-definition checks before any instance/grant write.
- **Retention/ondelete:** definition/version parents referenced by grants, instances, runtime, or audit use `RESTRICT`; version history is retained. No cascade from definition to authorization evidence.
- **Backfill/quarantine:** resolve definitions and versions by exact parent identity; quarantine orphan, deleted, cross-tenant, malformed, overlength, ambiguous, and artifact-mismatch rows. Never infer a version from payload/name or search by manifest version alone.
- **Handoff:** `DefinitionVersionRepository` resolves `(definition_id, tenant_id)` and `(version_id, definition_id)`, lifecycle, and immutable artifact digest. It returns explicit conflict/quarantine and never supplies a positive version based only on `manifest_version`.

## 4. Dynamic app instance reciprocal keys and fencing

**Task T03: add exact plugin-instance identity and runtime fencing fields.** Planned revision: `2026110103_persistence_plugin_instance_foundation.py`.

- **Exclusive model-file zone:** new `models/plugin_instance_registry.py`; staged instance changes in `models/dynamic_apps.py` are limited to `dynamic_app_instances` fields/keys. Do not edit scope registry or grants.
- **Exact identity:** materialize `plugin_instance_id`; never substitute `app_id`, `app_version_id`, `scope_id`, or `session_id`. The instance exposes unique key `(id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id)` and exact registry-compatible anchors.
- **Fields:** immutable artifact digest, tenant/scope anchors, subject kind/id where required, exact session binding where required, lifecycle `draft|published|disabled|revoked|quarantined`, runtime reference without secrets, heartbeat/start/stop evidence, monotonic `runtime_generation`, CAS `writer_generation`, and lease evidence.
- **Parent-before-child:** require definition/version composite proof from T02 before instance positive eligibility. Create the instance unique key before reciprocal registry FK and grant FK. The registry-to-instance and instance-to-registry cycle remains staged.
- **FK/use_alter:** use named `ForeignKeyConstraint`s and `use_alter=True` where SQLAlchemy creation order requires it. Add reciprocal constraints with explicit `op.create_foreign_key` in T04, not by relying on ORM declaration order. Nullable/deferred registry linkage is migration staging only.
- **RLS/FORCE/validation:** tenant/scope RLS with `USING/WITH CHECK`; `NOT VALID` instance/definition/version checks must be validated before published status. Published is impossible for rows lacking exact artifact, scope, subject/session (when required), or current generations.
- **Indexes/uniqueness:** unique exact instance key; lookup by tenant/scope/status; `(id, writer_generation)` and runtime-generation reconciliation indexes; source mapping identity. Avoid a moving-clock partial uniqueness predicate.
- **Retention/ondelete:** instance/security evidence uses `RESTRICT`; runtime/session references are nullable only where historical retention requires it. No fallback deletion of instance evidence through app/version cascade.
- **Backfill/quarantine:** only exact app/version/tenant/scope/artifact matches can be staged. Missing subject/session, duplicate identity, stale generation, cross-tenant scope, or missing artifact goes to quarantine/draft/disabled, never inferred published.
- **Handoff:** `PluginInstanceRepository` resolves exact identity and lifecycle. `RuntimeFenceRepository` performs CAS on runtime/writer generations in one transaction; stale generations deny or return stale-generation and append fencing evidence. No `SELECT`-then-mutate path.

**Task T04: close reciprocal instance/registry FKs.** Planned revision: `2026110104_persistence_plugin_instance_fk_close.py`.

- **Exclusive model-file zone:** constraint-only closure for `models/plugin_instance_registry.py` and the staged instance columns in `models/dynamic_apps.py`; no new grant/resource/audit model fields.
- **FK order:** after T01 registry anchors and T03 instance unique key exist, add named reciprocal composite constraints. Registry plugin shape points to exact instance; instance scope resolves to registry. Add grant-facing instance key only after both directions are present.
- **Cycle handling:** use `use_alter=True` in ORM metadata where necessary and explicit late Alembic constraints. `NOT VALID` is allowed for lock-sensitive creation, but T04 must produce explicit `VALIDATE CONSTRAINT` evidence before tightening nullability and before the M05 gate.
- **Consistency:** enforce equality of instance definition/version/tenant/scope tuple across registry and instance; reject any alternate app/version/scope row. Parent deletion is `RESTRICT`.
- **Handoff:** `PluginInstanceRepository` may expose published instance resolution only after both FK directions and validation evidence are recorded. T04 does not enable publication by itself; the immutable promotion record remains required.

## 5. Audit V2 and trusted transaction boundary

**Task T05: create immutable audit V2 envelope.** Planned revision: `2026110105_persistence_audit_v2.py`.

- **Exclusive model-file zone:** new `models/audit_v2.py`; no edits to legacy audit or grant models.
- **Envelope:** persist event identity/type/time, decision/reason/result/error, tenant/cabinet/project/user/subject/session, definition/version/artifact, scope/instance/runtime/writer/lease, grant/source/mapping/schema identity, capability/payload/argument/snapshot digests, operation/resource/domain, request/trace/span/correlation, MCP profile/server/tool/version, redaction metadata, and duration. Never persist raw arguments, credentials, tokens, secrets, or unredacted payloads.
- **Parent/FK/delete:** optional historical target references use `SET NULL` only when genuinely optional; source IDs/digests remain. Audit never cascades away with grants, instances, runtime, or outbox rows. Do not create a required FK that prevents retention/archive.
- **Indexes/uniqueness:** unique event identity/idempotency key as defined by the audit contract; indexes by tenant/time, correlation, source identity, decision/result, and partition. Do not use audit as a mutable current-state table.
- **RLS/FORCE/append-only:** `ENABLE/FORCE RLS`, tenant read policy, trusted writer policy, no API-role `UPDATE/DELETE`. Add deny-by-default trigger or equivalent privilege boundary. Corrections are new linked events. Validate append-only privileges and policies before any positive mutation depending on audit.
- **Handoff:** `CapabilityAuditRepository` accepts full `AuditOutcomeEvidence` and derives `RedactedAuditOutcome`; direct narrow appends are non-authoritative telemetry. `TrustedAuditTransaction` must append audit atomically with the related positive mutation and roll back that mutation on audit failure. Promotion evidence uses the same repository.

## 6. Canonical grants, mapping, and quarantine

**Task T06: create canonical grant schema and compatibility mapping boundary.** Planned revision: `2026110106_persistence_canonical_grants.py`.

- **Exclusive model-file zone:** new `models/canonical_grants.py`; compatibility changes to `models/dynamic_app_grants.py` are limited to the implementation PR's read-only projection boundary and must not create a second owner.
- **Canonical row:** explicit definition/version/app identity, canonical `tenant_id`, typed scope anchors, capability key, subject binding, optional exact plugin instance/session, operations/resources/domains/constraints, artifact/binding/payload/mapping digests, lifecycle, grant generation, writer generation, issue/expiry/revoke identity and provenance.
- **Parent-before-child:** require T01 scope registry, T02 definition/version composite keys, T04 instance reciprocal key, and T05 audit append boundary. Add named composite FKs:
  - `(app_version_id, app_definition_id) -> dynamic_app_versions(id, app_definition_id)`;
  - `(app_definition_id, tenant_id) -> dynamic_app_definitions(id, tenant_id)`;
  - exact instance tuple -> M05 instance key, `ON DELETE RESTRICT`;
  - scope tuple -> registry key, with plugin-instance shape equality.
- **Capability identity:** one row per capability/key or another explicitly reviewed equivalent. Never drop v1 `GrantSet.name`, merge capabilities, broaden absent methods to wildcard, or treat requested manifest capability as authorization. Denied/unsupported/ambiguous entries are provenance/quarantine only.
- **Indexes/uniqueness:** enforce canonical active identity using capability key and sorted canonical digests, plus generation strategy that does not depend on a moving clock. Cover `(tenant_id, scope_kind, scope_id, app_id, app_version_id, capability_key)` and validity/revoke lookups. Mapping uniqueness is `(source_kind, source_id, canonical_schema_version)` with source/canonical digest conflict detection.
- **RLS/FORCE/validation:** target grant table has `ENABLE/FORCE RLS`, `USING/WITH CHECK`, tenant/scope parent `EXISTS`, and trusted-role-only positive mutation. Name all constraints; validate `NOT VALID` constraints before any grant issue/revoke/reissue. The final grant scope check must include every approved branch, including `plugin_instance` with `scope_id = plugin_instance_id` and installation-anchor equality; the current three-shape contract is an implementation blocker, not a target omission.
- **Retention/ondelete:** grants, mapping, quarantine, and provenance use `RESTRICT`/archive semantics. Expiry is read-time denial, not deletion. Reissue closes/supersedes prior history transactionally and increments generation.
- **Backfill/quarantine:** use adapter one source row per transaction, preserving IDs, timestamps, actor provenance, normalized digest, and source representation. Quarantine orphan versions, cross-tenant scopes, unsupported shapes, duplicates, invalid timestamps, missing revoke actor/reason, and missing instance/subject binding. Quarantine cannot authorize.
- **Handoff:** `CanonicalGrantRepository` is the only future policy owner after partition promotion; `GrantMappingRepository` and `GrantQuarantineRepository` expose idempotent conflict-safe imports; `LegacyGrantProjectionRepository` is read-only after cutover. No direct ORM insert and no legacy-only positive writer.

**Task T07: enable grants RLS and trusted writer epoch.** Planned revision: `2026110107_persistence_grants_rls.py`.

- **Exclusive model-file zone:** policy/index/privilege metadata for `models/canonical_grants.py`; no grant shape redesign and no edits to other model classes.
- **RLS/FORCE:** create named policies for no-context deny, wrong tenant/cabinet/project deny, active scope requirement, API-role read/write separation, and trusted writer epoch. Record `USING`, `WITH CHECK`, `ENABLE`, `FORCE`, and role privilege inventory.
- **Validation gate:** explicitly validate every staged FK/check and prove policy behavior for migration role versus API role before enabling the canonical issue path.
- **Handoff:** repository unit of work sets server-resolved `app.tenant_id`, optional cabinet/project/user/role, correlation, and writer epoch; missing/contradictory context returns deny. T07 still requires shadow parity and promotion record before positive authorization.

## 7. Runtime revisions, resources, and materialization

**Task T08: close runtime revision/materialization and resource persistence.** Planned revision: `2026110108_persistence_runtime_resources_materialization.py`.

- **Exclusive model-file zone:** existing `models/runtime_revisions.py` and `models/resources.py`; add `models/materialization_provenance.py` only if immutable/queryable provenance cannot remain in existing columns. Do not modify dynamic-app, audit, or automation model zones.
- **Parent-before-child order:** capability bundle immutable manifest/digest first; then project runtime revisions with `project_id -> projects.id ON DELETE CASCADE` only as the intentionally derived project-owned operational child, and `bundle_id -> capability_bundles.id ON DELETE RESTRICT` to preserve the immutable manifest snapshot; then materialization entries `revision_id -> project_runtime_revisions.id CASCADE`; resource roots before resource versions (`resource_id -> resources.id CASCADE`) and bindings. Add project `desired_revision_id`/`applied_revision_id` only after revision table exists, initially nullable, then backfill and constrain in a later validated step.
- **FK cycles:** stage resource active-version linkage and exact version FK after roots/versions exist; do not use `version_number` alone as referential proof. Use named late constraints/`use_alter=True` where active pointer or provenance creates a cycle.
- **Runtime/materialization fields:** immutable bundle digest/manifest; desired/applied revision, project, lifecycle, image digest, bridge reference, generation, rollback target and provenance; entry path, operation/idempotency digest, generation, explicit status (`planned|staged|applying|applied|failed|skipped`), content/source provenance, diagnostics. Partial apply is `failed`, never `ready`.
- **Resource fields:** stable root and explicit scope ownership; append-only immutable versions with checksum/status; explicit binding to target scope/session/project and pinned version identity. Polymorphic scope must resolve via registry or a documented policy-only boundary; no misleading FK to one owner table.
- **Indexes/uniqueness:** bundle digest unique; `(project_id, revision)` unique and state/latest indexes; `(revision_id, workspace_path)` and `(revision_id, idempotency_key)` unique; resource `(scope_kind, scope_id, kind, slug)` unique; version `(resource_id, number)` unique; binding target lookup and session-aware partial uniqueness. Add only measured hot-path indexes.
- **RLS/FORCE:** runtime/materialization ownership through `project_id -> projects` parent `EXISTS`; resources through active scope registry; both `USING/WITH CHECK`, `ENABLE/FORCE`. Secrets are prohibited; persist only image digests and short-lived bridge/projected-secret references.
- **Retention/ondelete:** materialization entries and resource versions may cascade only as intentionally derived children. Published bundles, revisions, resource payloads, snapshots, diagnostics, and provenance are immutable; rollback changes a pointer/binding to a prior ready version and never deletes evidence. Audit/provenance links use `RESTRICT` or nullable historical `SET NULL` where genuinely optional.
- **Backfill/quarantine:** import trusted manifests/versions by source and content digest; quarantine invalid paths, duplicate idempotency, cross-scope bindings, missing project/resource ownership, invalid status transitions, and secret-bearing payloads. Failed imports retain diagnostics/provenance and are retryable without overwrite.
- **Handoff:** `RuntimeRevisionRepository`, `MaterializationRepository`, `ResourceRepository`, and `ResourceBindingRepository` enforce exact revision/path/version/binding identity, CAS generation, target scope membership, and explicit status mapping. No runtime apply or resource binding is positive before grant/audit/promotion gates applicable to the partition.

## 8. Automation intent and outbox

**Task T09: create automation intent/outbox/delivery ownership schema.** Planned revision: `2026110109_persistence_automation_outbox.py`.

- **Exclusive model-file zone:** new `models/automation_outbox.py`; compatibility adapter metadata for existing `external_api.py` and `projects.py` is limited to projection IDs/ownership markers. Do not replace or mutate legacy webhook/project-trigger tables in this task.
- **Separate durable concerns:** verified subscription/endpoint intent and source identity; inbound event/request idempotency identity plus request hash; durable task intent linked to `agent_runs`, ordered event, or approval identity where applicable; outbound event/enqueue intent created with the business mutation; delivery status, attempts, lease epoch, worker, retry/backoff, terminal result, response/error digest; compatibility projection IDs for `project_triggers`/`webhook_deliveries`. Keep task/run identity, outbox/event identity, and delivery identity as separate uniqueness boundaries; a delivery must have a real task/run and stable source event identity, never a guessed timestamp or payload-only key.
- **Parent-before-child:** resolve tenant/scope registry and project/run/approval parents first; create intent/event roots, then outbox entries, then delivery lease/attempt children. Use `RESTRICT` for source/provenance/security parents; use `CASCADE` only for intentionally derived delivery-attempt children. Never treat legacy lease columns as a target FK or ownership proof. `project_triggers` remains a separate queue and requires its own generation/CAS or partition-fence decision; it must not be treated as safely fenced by the webhook delivery adapter.
- **Identity/uniqueness:** unique `(source_kind, source_id, schema_version, request_hash)` or the reviewed equivalent for exact idempotency; separate unique event/enqueue identity; partition-scoped ordered task/run linkage; indexes by tenant/scope/status/available-at, lease expiry, worker epoch, retry time, and terminal state. Same identity with another hash is conflict/quarantine.
- **RLS/FORCE/validation:** tenant/scope policies resolve through registry and parent `EXISTS`; `ENABLE/FORCE RLS`, `USING/WITH CHECK`; validate all staged source/parent constraints before claims. Endpoint/secret columns contain references only, never secret values.
- **Lease/fencing:** delivery claim is CAS on lease expiry and fencing epoch. Expired leases may be reclaimed; stale workers cannot mark delivery terminal. Worker identity and current generation are checked in the same transaction.
- **Retention/ondelete:** delivery evidence, terminal result, hashes, and audit references are retained under explicit policy; no cascade that erases security or business provenance. Cleanup is a later retention decision.
- **Backfill/quarantine:** legacy webhook/project-trigger rows are imported as shadow evidence with source identity and digest. Unknown tenant/scope, duplicate/hash conflict, missing run/approval linkage, invalid lease state, or secret-bearing payload is quarantined. Legacy workers remain authoritative until ownership checkpoint; do not dual-run workers for one partition.
- **Handoff:** `AutomationIntentRepository` verifies tenant/scope server-side and claims atomically; `OutboxRepository` commits business mutation plus intent in one trusted transaction; `DeliveryLeaseRepository` performs CAS/fenced claims; `AutomationPromotionRepository` records ownership. After promotion, target claims are sole eligible worker path and legacy projections are read-only.

## 9. Promotion record and final ownership gate

**Task T10: create immutable per-partition promotion records.** Planned revision: `2026110110_persistence_promotion_records.py`.

- **Exclusive model-file zone:** promotion record class in the task-owned model module selected by the implementation PR (prefer a dedicated `models/persistence_promotion.py`); registration only in `models/__init__.py`. No changes to target data shapes from T01–T09.
- **Partition key:** smallest bounded partition, normally `(tenant_id, app_definition_id)` narrowed by scope or exact instance when required. Store source/target/binding digests, source snapshot/schema, checkpoint/final-delta checkpoint, gate evidence, actor/time, decision owner, current reader/writer owner, fencing epoch, runtime/materialization/outbox generations, compatibility projection marker, correlation ID, rollback window, and decision `blocked|quarantined|ready|promoted`.
- **Parent/FK/delete:** promotion evidence references target/source identities only with `RESTRICT` or nullable historical links. It is append-only; corrections are new records linked to the prior record. No status flag substitutes for an immutable record.
- **Indexes/uniqueness:** immutable partition/checkpoint lookup; unique current decision identity or explicit generation key; indexes by tenant, definition, decision, owner generation, and correlation. Do not allow two current writer owners for one partition.
- **RLS/FORCE/validation:** promotion creation uses trusted transaction context, full Audit V2 append, validated FKs/checks, RLS/FORCE policies, and trusted privileges. A missing gate evidence row is deny.
- **Gate:** require zero unexplained parity mismatches, zero unknown retained mappings, zero unresolved positive bindings, validated composite FKs and RLS, drained final delta, fenced legacy writer/worker, exactly one current writer generation, and full audit. `blocked`, `quarantined`, and `ready` records are evidence only and retain `canonical_positive_writes=false`; only `promoted` for the exact partition enables the relevant positive writer/read owner. Automation promotion additionally requires the applicable legacy delivery/trigger worker fence, including the separate `project_triggers` queue decision.
- **Handoff:** `PromotionEvidenceRepository` exposes immutable record creation and read-only evidence. Before read cutover target writes remain disabled and legacy authoritative. After read cutover there is no dual-owner rollback; fail closed and repair forward through CAS/idempotent commands.

## 10. Repository handoff contract

All repository adapters are wired through the existing infrastructure persistence dependency/session composition. The required transaction order is:

```text
authenticated request
  -> canonical tenant/scope resolver
  -> definition/version resolver
  -> plugin-instance/runtime fence resolver
  -> trusted unit of work (RLS GUCs + CAS)
  -> grant / runtime / resource / automation repository
  -> full Audit V2 append
  -> promotion/gate evidence where applicable
  -> commit
```

Every operation returns an explicit `allow`, `deny`, `quarantine`, `conflict`, `stale-generation`, or `system-failure` outcome. Adapter assertions do not replace composite FKs, RLS/FORCE, validation evidence, or audit. Legacy readers/writers are compatibility surfaces only; after each partition's promotion they cannot authorize, deliver, or become a second owner.

## 11. Acceptance and verification freeze

The implementation PR must provide, without running them during this freeze, acceptance artifacts for:

- canonical owner-anchor `String(80|64) -> String(40)` classification, overlength inventory, no truncation, and proof that unrelated stable IDs were not shortened;
- all four registry shapes, parent compatibility, `use_alter`, reciprocal composite instance FKs, staged `NOT VALID` constraints, and explicit `VALIDATE CONSTRAINT` evidence;
- RLS/FORCE behavior for no context, wrong tenant/cabinet/project, migration role, API role, and cross-scope children;
- audit append-only mutation denial and full-vs-redacted evidence equality;
- exact instance artifact/subject/session/runtime-generation/writer-generation fencing and stale-worker zero-row CAS;
- canonical capability identity, grant uniqueness/reissue, mapping digest conflicts, and quarantine isolation;
- materialization partial failure/replay/rollback, resource version immutability, explicit target binding, and secret-reference rejection;
- atomic business mutation plus outbox intent, lease expiry/reclaim, stale-worker denial, retry/terminal states, duplicate/hash conflict, and legacy-worker fencing;
- shadow parity, final delta, immutable promotion record, pre-cutover rollback, and post-cutover forward-only repair.

Verification freeze is active: tests, builds, lint, formatting, compilation, Alembic execution, migration execution, environment changes, and git checks are intentionally not run for this documentation task. No ORM, application adapter, database, backfill, positive write, cutover, cleanup, or truncation was performed.
