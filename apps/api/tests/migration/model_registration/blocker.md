# MIG-ORM-REGISTRY-INTEGRATION-02: metadata registration and residual blocker

Status: **resolved** by `MIG-OWNERSHIP-CLEANUP-01`. Single ownership of
`dynamic_app_capability_grants` is now unconditional: the retired narrow
duplicate module is deleted from the tree, not merely unregistered. This is
still not DDL, Alembic, RLS, migration-readiness, or promotion approval.

## Resolution (MIG-OWNERSHIP-CLEANUP-01)

- `apps/api/src/prodavan/infrastructure/persistence/models/dynamic_app_grants.py`
  is deleted. It was the last module in the tree that re-declared
  `__tablename__ = "dynamic_app_capability_grants"`; importing it after the
  models package would have forked `Base.metadata` with a second `Table`.
- `models/canonical_grants.py::CanonicalGrantRow` is the only owner of the
  physical table, and all six canonical classes are registered by
  `models/__init__.py`:
  `CanonicalGrantRow`, `CanonicalGrantMappingRow`,
  `CanonicalGrantQuarantineRow`, `CanonicalGrantAuditRow`,
  `CanonicalGrantWriterEpochRow`, `M06CanonicalGrantPromotionEvidenceRow`.
- `DynamicAppCapabilityGrantRow` remains absent from `models.__all__` and from
  the package imports. The module-level `models.__getattr__` still raises
  `AttributeError` containing `retired`, so a stale import fails closed; the
  retired module path itself now fails with `ModuleNotFoundError` instead of
  the previous duplicate-`Table` `InvalidRequestError`.
- `models/dynamic_apps.py::AppGrantRow` / table `dynamic_app_grants` was not
  touched. It is a different physical table and stays the read-only legacy
  compatibility projection, never an authorization source.

## History of the decision

1. **Initial registration (MIG-ORM-REGISTRY-INTEGRATION-02).** The 13
   owner-registry, dynamic-app-definition-version and plugin-instance-registry
   rows were registered. `canonical_grants.py` was deliberately left out,
   because `CanonicalGrantRow` was read as duplicating
   `dynamic_app_capability_grants`, whose owner was assumed to be the legacy
   `dynamic_app_grants.py::DynamicAppCapabilityGrantRow`.
2. **Ownership re-decision (MIG-DDL-M06-01).** The duplicate was identified
   correctly: the *legacy narrow model* was the second description of the
   table, not the canonical one. Canonical grants became the owner, the legacy
   class was removed from `models/__init__.py` imports/exports and replaced
   with the fail-closed `__getattr__`, and the file itself was left in the tree
   as a recorded cross-zone debt.
3. **Cleanup (MIG-OWNERSHIP-CLEANUP-01).** The debt was paid: the file was
   deleted, the model-registration gates were flipped to the new ownership, and
   the ownership test was tightened from "one documented residue" to "no
   residue".

## Registration decision

- Registered by `models/__init__.py`: all seven `owner_registry.py` rows, all
  three `dynamic_app_definition_version.py` rows, all three
  `plugin_instance_registry.py` rows, and all six `canonical_grants.py` rows.
  Their declared FK target tuples have corresponding primary/unique keys in the
  static metadata contract, including the metadata-only ProjectRow key, the M05
  reciprocal-link target keys, and the canonical three-column version key
  `(id, definition_id, artifact_digest)`.
- Registration only populates `Base.metadata`; no migration revision or DDL was
  generated or executed. M04/M04E/M05 evidence modules remain fail-closed and
  do not imply an active resolver or positive writer.

## Measured ownership state (read-only introspection)

Read-only `python -c` against the models package, no DB write, no migration:

| Measurement | Before cleanup | After cleanup |
|---|---|---|
| `len(Base.metadata.tables)` | 102 | 102 |
| `len(Base.registry.mappers)` | 102 | 102 |
| duplicate table names across mappers | none | none |
| tables in `Base.metadata` without a mapper | none | none |
| modules declaring one `__tablename__` twice (top-level model files) | 1 (`dynamic_app_capability_grants` → `canonical_grants.py`, `dynamic_app_grants.py`) | 0 |
| owner mapper of `dynamic_app_capability_grants` | `canonical_grants.CanonicalGrantRow` | `canonical_grants.CanonicalGrantRow` |

The table count is unchanged because the deleted module was never imported;
only the latent duplicate declaration disappeared.

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

## Gates updated by this task

- `tests/migration/model_registration/test_metadata_registration.py`: the
  owner of `dynamic_app_capability_grants` is asserted to be
  `CanonicalGrantRow`; all six canonical classes are in `READY_MODELS`; the
  declared `M06_TABLE_OWNERS` map must match metadata one-to-one; the retired
  name is absent from `__all__`, absent from `hasattr`, absent from
  `Base.registry.mappers`, and raises `AttributeError` matching `retired`.
- `tests/migration/model_registration/test_static_contract.py`: the imports of
  `models/__init__.py` must contain `canonical_grants` with all six classes,
  `canonical_grants.py` must be the single declaring source of each canonical
  table, `dynamic_app_grants.py` must not exist, and neither the retired module
  nor the retired class name may appear in the package imports or `__all__`.
- `tests/migration/canonical_grants/test_ownership_metadata.py`: the residue
  allowance is gone — `offenders == {}` — and the retired module must be absent
  from disk and raise `ModuleNotFoundError` on import while the retired
  attribute keeps raising `AttributeError` matching `retired`.
- This file was rewritten from an open blocker to a resolved record.

## Remaining blockers and debts (unchanged unless stated)

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
- Documentation and message debts left by the cleanup, all outside this task's
  file zone: `models/__init__.py` (comment above the canonical import and the
  `__getattr__` message still instruct to "delete models/dynamic_app_grants.py
  in a dynamic-app follow-up task"), `models/canonical_grants.py` (module
  docstring and the `M06_RETIRED_DUPLICATES` entry are now historical),
  `tests/migration/canonical_grants/acceptance.md` (§3 still describes the file
  as present and predicts `InvalidRequestError`), and
  `docs/08-migration/canonical-grants-ownership-decision.md` §8 items 1–2,
  which are now closed. Older design docs (`docs/08-migration/m06-integration-readiness.md`,
  `docs/08-migration/persistence-implementation-phase.md`,
  `docs/08-migration/target-orm-schema-task-map.md`,
  `docs/08-migration/dynamic-grants-ownership.md`,
  `docs/08-migration/typed-scope-owner-registry.md`) still describe
  `DynamicAppCapabilityGrantRow` as the current model.
- Legacy projection debt: `models/dynamic_apps.py::AppGrantRow` still carries
  `ON DELETE CASCADE` on its version FK, which contradicts the retention
  requirement. Different physical table, outside this zone.

## Rollback and freeze

No database state exists to roll back from this task. Rollback of the cleanup is
"restore the deleted module from history", and that is only safe together with
reverting the gates in this task: the pre-cleanup tests assert the retired
duplicate as an allowed residue, so a partial rollback (file restored, gates
not) reintroduces a failing contract, and the reverse (file absent, gates not
reverted) leaves the fail-closed assertions in place. Do not change the
M04E/M05 definitions, `canonical_grants.py`, or the legacy `dynamic_app_grants`
tables as part of a rollback. If a reviewed revision is later applied but no
evidence has been imported, rollback requires a separately reviewed migration
against actual dependencies. After any evidence is imported, preserve all rows
and repair forward; never delete or rewrite evidence as rollback.

Static acceptance tests were authored and the gates were updated but not run. No
test, build, compile, standalone lint, format, git check/action, Alembic command,
DDL generation/execution, or data operation was intentionally invoked. The only
execution performed was the migration-protocol-allowed read-only `python -c`
model import that printed `Base.metadata` counts, mapper counts, and duplicate
`__tablename__` declarations.
