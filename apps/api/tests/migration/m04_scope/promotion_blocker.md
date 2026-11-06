# MIG-M04-01 blocker and promotion record

Status: `blocked` for promotion/backfill; ORM foundation and acceptance fixtures are authored. No checks were executed under the verification freeze.

## Metadata registration blocker

`owner_registry.py` defines the tenant and typed-scope models, but the persistence registration surface is `apps/api/src/prodavan/infrastructure/persistence/models/__init__.py`. That file is outside MIG-M04-01's exclusive zone and currently imports only `ScopeRegistryRow`, `ScopeOwnerMappingRow`, `ScopeOwnerQuarantineRow`, and `ScopeOwnerAuditRow` from this module. `CanonicalTenantAnchorRow` and `ScopeOwnerProjectAnchorRow` therefore are not registered into normal `Base.metadata` through the package import path. This task intentionally did not edit `__init__.py` or Alembic wiring. A follow-up owner-authorized change must register both model classes before generating/reviewing a migration. No Alembic revision was created or executed.

RLS remains absent from ORM metadata. PostgreSQL `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, `USING`, and `WITH CHECK` policies and migration/security role privileges require a separate reviewed DDL revision. No-context and cross-tenant fixtures are review inputs, not proof of installed policies.

## Promotion record

- Task: `MIG-M04-01`
- Outcome: `blocked`
- Decision owner: legacy identity and access remain authoritative
- Canonical tenant: `companies.id`, logical field `tenant_id`, `String(40)`, `tenant_type='company'`
- Parent tuple: `projects(id, company_id, cabinet_id)` compatibility key plus typed project anchor
- Cabinet membership: composite cabinet/company identity is represented; active grant status must be checked in the same trusted transaction and RLS `EXISTS` policy because it is mutable
- Constraints delivered: typed shape, provenance/lifecycle checks, unique logical/anchor tuples, RESTRICT typed parent FKs
- Positive canonical writes: `false`
- Plugin-instance positive branch: `false` (quarantined-only)
- Inventory/source digest and valid/quarantined counts: unavailable; no inventory or backfill was requested or performed
- RLS/FORCE RLS evidence: missing
- Required registration and revision slot: blocked on authorized model-package registration and subsequent migration review

## Acceptance artifacts (not executed)

- `owner_registry_fixtures.py`: valid tenant/cabinet/project, malformed and overlength IDs, orphan parents, cross-tenant cabinet, project/company/cabinet mismatch, revoked membership, impossible shapes, missing/empty/cross-tenant RLS context.
- `test_owner_registry_contract.py`: static metadata contract assertions, authored but not run.

## Rollback and gate

Before any import, remove only newly introduced empty M04 objects after dependency review. After import, halt only the affected partition and preserve registry, mapping, quarantine, audit and source evidence; repair forward. Never cascade-delete canonical owner evidence or rewrite a scope identity. Promotion remains blocked until model registration, reviewed migration/RLS installation, deterministic source inventory, retained parent proof and membership lifecycle checks are available. Verification freeze remains in force.
