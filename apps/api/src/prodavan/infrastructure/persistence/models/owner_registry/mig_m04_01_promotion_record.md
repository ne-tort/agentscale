# MIG-M04-01 Promotion Record

Status: `blocked` for backfill; `ready-for-static-review` for empty foundation model. Record ID: `MIG-M04-01:baseline-2026-11-01:owner-registry-foundation-v1`.

## Scope and ownership

This record covers only the SQLAlchemy empty foundation in `owner_registry.py`. Canonical tenant identity is `companies.id` with `String(40)`. Registry rows and evidence are not an authorization owner and no positive writer/resolver is defined. Legacy owner state remains authoritative.

## Dependencies and source inventory

Consumed contracts: `docs/08-migration/canonical-tenant-scope-registry.md`, `typed-scope-owner-registry.md`, `m04-m06-readiness-matrix.md`, `m04-persistence-adapter-contract.md`, and `migration-execution-backlog.md`. Read-only source anchors inspected: `companies`, `memberships`, `cabinet_instances`, `cabinet_company_grants`, and `projects` ORM declarations.

Source snapshot/schema digest, row counts, accepted/quarantined ID counts, checkpoint, actor/time, and reviewer signatures are `NOT_AVAILABLE`: no source inventory or database snapshot was supplied or executed. This record must not be treated as inventory evidence. Planned Alembic slot is the next reviewed linear revision from the confirmed repository head; the head was not inspected.

## Schema and acceptance evidence

Model declarations provide named checks for scope kind, owner state, non-empty provenance identity/digests, typed shape and the deferred-only plugin branch; uniqueness for `(scope_kind, scope_id)` and the typed anchor tuple; `RESTRICT` tenant/cabinet/project FKs; and a deliberately unbound plugin-instance anchor. The shape requires a cabinet anchor whenever a plugin installation has a project anchor. Mapping source identity is unique by `(source_kind, source_id, schema_version)` and records payload digest separately. Quarantine and audit store redacted identity/digest/reason/checkpoint/correlation evidence without raw payload fields.

### RLS policy review artifact (not executed or approved)

No policy is installed in this ORM-only zone. The required migration/security review must cover both `USING` and `WITH CHECK`, with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`; missing/empty transaction-local `app.tenant_id` denies. Ordinary tenant visibility is limited to `tenant_id = current_setting('app.tenant_id', true)` and `owner_state = 'active'`, plus a retained-company parent proof. Cabinet rows additionally require exact `app.cabinet_id` and active cabinet/company grant or separately approved owner proof. Project rows require exact `app.project_id`, `app.cabinet_id`, matching `projects.company_id = tenant_id` and `projects.cabinet_id = cabinet_id`, and active cabinet access. Plugin rows are migration/security-only and always denied for positive resolution until M05 exact-instance and reciprocal FK validation; request-provided scope assertions never establish authority. Mapping/quarantine/audit require restricted migration/security roles; audit mutation is denied and corrections append a new event. Negative review cases: no tenant context, wrong tenant/cabinet/project, archived/quarantined row, project-company-cabinet mismatch, plugin positive lookup, and API-role update/delete of audit.

Static fixture matrix (designed, not executed):

| Case | Expected outcome |
|---|---|
| tenant with matching tenant ID and no narrower anchors | shape accepted |
| cabinet with matching cabinet ID and tenant | shape accepted; active membership proof still required |
| project with matching project ID, tenant and cabinet | shape accepted; compatibility proof still required |
| plugin with exact instance ID/scope ID and no project anchor | shape accepted only in quarantined state; positive resolution denied |
| plugin project anchor without cabinet anchor | rejected by named typed-shape check |
| plugin with attempted active/archived owner state | rejected by named deferred-plugin check |
| tenant carrying cabinet/project/instance anchor | rejected by named shape check |
| project missing tenant or cabinet | rejected by named shape check |
| plugin instance ID different from scope ID | rejected by named shape check |
| unsupported scope kind or owner state | rejected by named enum check |
| overlength/malformed/orphan IDs | inventory quarantine; no truncation or implicit conversion |
| cross-tenant or project/company/cabinet mismatch | quarantine; no active resolution |
| repeated source identity and same digest | future mapping implementation must be idempotent |
| repeated source identity with changed digest | conflict/quarantine; never overwrite |
| missing tenant context, archived or quarantined row | future RLS/resolver must deny |

RLS/FORCE RLS, `USING`/`WITH CHECK`, and append-only database privileges are not expressible in this ORM-only file and remain explicit Alembic/security-review gates. No database model registry import was changed because it is outside the exclusive file zone; model registration is a prerequisite before Alembic can consume this metadata and is a blocker for executable schema migration.

## Promotion, rollback and gate

`canonical_positive_writes=false`; no positive writer, resolver, plugin branch, grant table, shared DDL, backfill, or cutover is enabled. Promotion gate is blocked until model registration is coordinated in its owned zone, current Alembic head/revision slot is confirmed, source inventory classifies all IDs, active cabinet membership and project compatibility are deterministically proven, RLS/privileges are reviewed, and all planned constraints are validated.

Before import, remove only empty foundation objects after dependency inventory. After import, stop only the affected partition and repair forward while retaining mappings, quarantine and audit; do not rewrite scope identity or delete evidence. Legacy remains authoritative.

Unresolved risks: membership ambiguity and cabinet many-to-many access; independent project company/cabinet FKs do not prove compatibility; existing cascade paths can erase owner/evidence; no real RLS/policy layer; no inventory digest/counts; model package registration is not in this exclusive zone.

Verification freeze honored: tests, builds, lint, compile, formatting, migrations and git checks were not run.
