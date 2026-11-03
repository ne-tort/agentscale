# MIG-ORM-REGISTRY-INTEGRATION-02: metadata registration and residual blocker

Status: candidate ORM metadata is statically registered. This is not DDL,
Alembic, RLS, migration-readiness, or promotion approval.

## Registration decision

- Registered by `models/__init__.py`: all seven `owner_registry.py` rows, all
  three `dynamic_app_definition_version.py` rows, and all three
  `plugin_instance_registry.py` rows. Their declared FK target tuples have
  corresponding primary/unique keys in the static metadata contract, including
  the metadata-only ProjectRow key and the M05 reciprocal-link target keys.
- Not registered: `canonical_grants.py`. `CanonicalGrantRow` duplicates
  `dynamic_app_capability_grants`, whose registered owner remains the legacy
  `DynamicAppCapabilityGrantRow`. Resolve ownership in a separately authorized
  task before importing or exporting canonical grants.
- Registration only populates `Base.metadata`; no migration revision or DDL was
  generated or executed. M04/M04E/M05 evidence modules remain fail-closed and do
  not imply an active resolver or positive writer.

## Structural alignment

- Tenant type is the mapped `tenant_type` field on tenant, cabinet, project,
  and typed scope rows. Tenant/cabinet/project anchors enforce `company` and
  provide composite unique keys that include tenant type and owner state.
- Project anchors target the stable `(projects.id, projects.company_id,
  projects.cabinet_id)` compatibility key. Mutable `projects.status` is retained
  as project-anchor evidence and in the typed scope-to-anchor FK, not in the
  project-parent candidate key.
- Project and cabinet anchors separately reference their project/cabinet and
  company parents, and retain typed composite references to their cabinet/company
  grant and owner-anchor tuples. Active membership and mutable lifecycle still
  require trusted transaction checks; FKs do not establish authorization.

## Remaining blockers

- RLS is not represented by these ORM models. PostgreSQL ENABLE/FORCE RLS,
  policies (`USING`/`WITH CHECK`), transaction-local tenant context, role
  privileges, and cross-tenant/no-context denial require reviewed DDL and runtime
  evidence. Until then all candidate rows remain non-authoritative evidence.
- No reviewed Alembic DDL exists for these constraints. Review dialect-specific
  expressions, composite FK target keys, constraint ordering, deferred cycles,
  RESTRICT behavior, and downgrade/rollback before generating or applying any
  revision. PostgreSQL-only operators and JSONB assumptions must be explicit.
- M04 source inventory, accepted owner mappings, deterministic checkpoints,
  active membership proof, and partition-bound evidence are absent; M04 promotion
  remains blocked.
- M04E immutable-artifact database enforcement, deferred current-version FK
  review/validation, source snapshot inventory, artifact equality and completed
  handoff evidence are absent; definition/version promotion remains blocked.
- M05 upstream M04/M04E promotion evidence, source snapshot, typed-anchor proof,
  both reciprocal-FK validation results, runtime/writer fencing and audit
  atomicity evidence are absent; instance resolution/publication remains blocked.
- Canonical grants remain blocked by duplicate ownership described above.

## Rollback and freeze

No database state exists to roll back from this task. Before any DDL is generated,
revert only this task's `models/__init__.py` imports/exports and the tenant-type,
anchor-FK, composite-key, and ProjectRow compatibility-key edits in
`owner_registry.py`; retain unrelated edits. Do not change the M04E/M05 definitions
or legacy grant tables. If a reviewed revision is later applied but no evidence has
been imported, rollback requires a separately reviewed migration against actual
dependencies. After any evidence is imported, preserve all rows and repair forward;
never delete or rewrite evidence as rollback.

Static acceptance tests were authored but not run. No test, build, compile,
standalone lint, format, git check/action, Alembic command, DDL generation/execution,
or data operation was intentionally invoked. The editing tool automatically ran its
lint hook after four successful targeted model edits; it returned `ok`. This was
incidental tool behavior, not a requested or standalone lint run.
