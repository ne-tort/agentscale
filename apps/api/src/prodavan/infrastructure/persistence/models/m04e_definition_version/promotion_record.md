# MIG-M04E-01 Promotion Record

Status: `blocked`; ORM foundation updated for static review only. Record ID: `MIG-M04E-01:definition-version-foundation:static-review-v1`.

## Scope and ownership

This task owns `dynamic_app_definition_version.py` and this record. It does not own model package registration (`models/__init__.py`), the M04E-02 application migration/backfill tests, Alembic, DDL, data writes, or cutover. Legacy `dynamic_app_definitions` / `dynamic_app_versions` in `dynamic_apps.py` remain the existing rows and current read/write owner. The target uses separate `m04e_*` tables; no duplicate target table/class was found.

## Contracts inspected

- `docs/PRODUCT.md`: product source of truth; no dynamic-app migration write/cutover authorization.
- Legacy ORM: `models/dynamic_apps.py`. Definitions use tenant/app, typed scope, lifecycle, and a current-version pointer. Versions use definition ownership, semantic `manifest_version`, SHA-256 `artifact_digest`, optional artifact URI, JSONB manifest, separate `requested_capabilities`, lifecycle, publication time, and rollback snapshots.
- Domain contracts: `domain/dynamic_apps/types.py`. Manifest protocol is `1.0`; `appId` and `version` must agree with definition and version; artifact digest is `sha256:` plus 64 lowercase hex; publication timestamp is present iff version status is published; published definition requires a current version.
- Read adapter/service: `application/dynamic_apps_definition_adapter/repository.py` and `application/dynamic_apps_definition/service.py`. Definition lookup is tenant/app-scoped; versions are loaded within the resolved definition; full version history rejects duplicate manifest versions or artifact digests and requires a published selected version to match the published definition's current pointer.
- Migration contracts: `docs/08-migration/m04-persistence-adapter-contract.md`, `typed-scope-owner-registry.md`, `dynamic-app-migration-gaps.md`, and `migration-execution-backlog.md`. Owner/scope RLS and active typed parent proof remain upstream gates. `String(40)` tenant-anchor alignment requires inventory and quarantine of overlength/malformed legacy values.
- Existing M04-01 ownership record: `models/owner_registry/mig_m04_01_promotion_record.md`. That folder is not used for this task's artifacts.
- `apps/api/tests/migration/m04e_definition_version/` contains M04E-01's `m04e_01_foundation_acceptance.md` static fixture matrix (updated here) and M04E-02-owned application/backfill tests. The shared model-registration test is not a task-owned model executable fixture, so no test code was added.

## Model changes

- Retained the `m04e_*` table boundary and named tenant, same-definition, current-pointer, manifest-version, and promotion constraints.
- Added definition owner keys `(id, tenant_id)` and `(id, app_id)`, tenant/company `RESTRICT` FK, lifecycle, typed-scope, tenant-scope identity, nonempty scope, source-proof, and published-current checks.
- Added version tenant and app identity columns with composite `RESTRICT` FKs to definition `(id, tenant_id)` and `(id, app_id)`. Added same-definition version keys, deterministic `(definition_id, manifest_version)` uniqueness, and per-definition artifact-digest uniqueness.
- Added manifest semantic-version, protocol, app/version identity, optional plugin artifact digest equality, required v1 field shape, requested-capability equality, SHA-256, lifecycle/publication timestamp, and source-proof checks.
- Version manifest and requested-capability JSON are frozen on assignment and load. ORM `before_update` guards compare immutable identity, source, manifest, and artifact fields to stored rows; mutable lifecycle and current-version pointer fields are not frozen.
- The model module's `M04E_ACCEPTANCE_CASES` is a static review checklist, not executed evidence.

No edits were made to `models/__init__.py`, the legacy model, or M04-01 files. `models/__init__.py` already imports/exports the target rows, and `alembic/env.py` imports that package to register `Base.metadata`; these existing wires are not gate approval or DDL validation evidence.

## Acceptance matrix (not executed)

1. Definition tenant FK: matching `companies.id` passes; missing tenant fails. Cross-tenant/mismatched version tenant fails its composite definition FK. Overlength legacy tenant is quarantined before conversion, never truncated.
2. Definition `scope_kind` outside tenant/cabinet/project fails. Tenant scope with `scope_id != tenant_id` fails. Published definition with null current version fails.
3. Current pointer to missing version fails at deferred constraint validation; version owned by another definition fails the composite `(current_version_id, id)` FK.
4. Version tenant/app identity different from its definition fails composite FKs. Definition/version owner keys support exact downstream references.
5. Duplicate `(definition_id, manifest_version)` fails; same manifest version under different definitions is allowed.
6. Duplicate `(definition_id, artifact_digest)` fails. ORM update of artifact digest, URI, manifest, requested capabilities, manifest version, source identity/digest, tenant, app ID, definition, or creation time fails and preserves the stored snapshot. Direct SQL mutation must fail only after a reviewed database trigger/privilege implementation; that is not in this task.
7. Unsupported definition/version lifecycle, malformed SHA-256, non-semver version, wrong manifest protocol/appId/version, malformed required manifest fields, digest mismatch, or requested-capability JSON mismatch fails a named check.
8. Published version without `published_at`, non-published version with `published_at`, or published definition pointing to a non-published current version fails/quarantines. The last case needs trusted cross-row lifecycle proof, a reviewed trigger, or staged validation; the composite FK proves same-definition identity only.
9. Promotion record outcome is blocked/quarantined only and `canonical_positive_writes=false`.

## Blockers and promotion

- `MIG-M04-01` typed owner/scope, RLS, retention, and accepted tenant inventory are not proven. Definition `scope_kind/scope_id` does not yet reference `scope_registry`; typed parent equality and active state cannot be proven by these model checks alone.
- Definition tenant conversion and version tenant/app mirrors need an inventory/backfill mapping plan; no source snapshot or database was supplied. Historical mismatches must be quarantined, not implicitly repaired.
- ORM immutability does not protect bulk SQL/Core updates or other clients. Database trigger/permissions and validation evidence require a separately authorized Alembic/security task.
- Published definition/current-version lifecycle equality is cross-row and needs trusted transaction logic, a reviewed trigger, or staged validation; DDL is out of scope.
- Model package registration is outside this task and remains an explicit dependency for metadata/Alembic consumption. No Alembic revision/DDL was created.
- No executable model-specific test fixture was added: the M04E-01-owned path is a static fixture matrix, while nearby executable application/backfill tests are M04E-02-owned and the shared model-registration test is not assigned to this task. The exact cases are recorded in the owned static fixture and in the matrix above.

## Rollback and freeze

Before target evidence import, no writes were performed and rollback is a no-op. If a later import begins and fails, halt only that partition, preserve mappings/quarantine/audit and legacy state, and repair forward. Do not drop nonempty evidence or cut over. Legacy definition/version rows remain read/write owner; canonical positive writes remain false. Tests, builds, compilation, lint, formatting, Alembic, DDL, database writes, and git checks were not run per request.
