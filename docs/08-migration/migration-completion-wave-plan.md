# Migration completion — wave plan (continuing to functional replacement)

Status: active execution plan. Owner: orchestrator agent. Baseline: 2026-11-04.
Single tracker for the remaining migration waves. Zones are exclusive so parallel agents
do not collide. Verification freeze is in force (see `AGENTS.md`).

## Landed in wave 1

### Alembic DDL chain (linear, single head)

| Rev | Content | State |
|---|---|---|
| `2026110202` | agent control plane (previous head) | landed earlier |
| `2026110301` | M04 owner registry, 7 tables + `uq_projects_scope_owner_parent_tuple` | done |
| `2026110302` | M04E definition/version, deferred current-version FK, immutability trigger | done |
| `2026110303` | M05 plugin instance registry, 7 deferred reciprocal FKs | done |
| `2026110304` | canonical grants, 6 tables, artifact-pinned version FK | done |
| `2026110305` | equipment v2, 8 tables | done |
| `2026110306` | resources + runtime revisions + attempt/audit/head tables | done |
| `2026110307` | RLS policies via shared registry `persistence/rls/policies.py` | done |

Design notes worth keeping:

- DDL agents verify composite-FK targets fail-closed inside the revision (`to_regclass`
  + named UNIQUE/PK check), so a missing target aborts the migration with a named blocker
  instead of leaving a silently invalid schema.
- M05 reciprocal FKs are `DEFERRABLE INITIALLY DEFERRED` and deliberately **not VALIDATED**.
  A late `VALIDATE CONSTRAINT` pass is required before any cutover claim.
- M04E DB trigger is stricter than the ORM guard: it also rejects `non-draft -> draft`
  rewinds, otherwise immutability is bypassable through `published -> disabled -> edit`.
  Legal "unpublish to draft" would need a separate decision and revision.
- Canonical grant ownership resolved: `dynamic_app_capability_grants` has exactly one
  owning model (`CanonicalGrantRow`); `dynamic_app_grants` stays a read-only projection
  (`AppGrantRow`). Decision recorded in `canonical-grants-ownership-decision.md`.

### Application packages

- `application/agent_runs/`: dispatcher, worker, lease, attempts, retry, reconcile.
- `infrastructure/persistence/agent_control_plane/intent_consumer.py`: durable consumer
  (`FOR UPDATE SKIP LOCKED`, DB-clock lease, fenced writes, redacted attempt evidence).
- `infrastructure/persistence/rls/`: trusted tenant context + policy registry.
- `application/migration/m04_scope/scope_persistence.py`: durable scope adapter.
- `application/migration/m04e_definition_version/`: backfill, parity, repository.
- `application/migration/m05_instance_runtime/runtime_service.py`: lifecycle, CAS, promotion.
- `application/migration/m06_grants_gateway/`: adapter, parity runner, composition, handoff.
- `application/migration/m06_cutover/cutover.py`: gates, writer lease, promotion records.
- `apps/web/src/shared/sse/`: parser, cursor, envelope with tests.
- `apps/web/src/features/dynamic-modules/`: list/detail/version/installation/grant screens.
- `apps/api/src/prodavan/api/v1/dynamic_apps.py` + `application/dynamic_apps_definition/`.
- `apps/api/src/prodavan/application/resources/` (+ `api/` subpackage), `application/content/`.

### Parent-owned fixes

- `tests/migration/agent_control_plane_persistence/test_wiring_gate.py`: the old
  "no consumer exists" assertion replaced by "exactly one zone-owned consumer".
- `persistence/agent_control_plane/WIRING.md`: consumer ownership, remaining broker
  publisher gap, and the missing append-only dispatch-audit table recorded as follow-ups.

## Open work

| ID | Task | Zone |
|---|---|---|
| MIG-M06-ADAPTER-FIX | repair truncated `adapter.py`/`evidence_store.py`, finish parity | `m06_grants_gateway/**` |
| MIG-RESOURCE-API-02 | HTTP router over existing resources application layer | `api/v1/resources.py`, `application/resources/**` |
| MIG-M05-BACKFILL-03 | bounded instance import + durable resolver | `m05_instance_runtime/**` |
| MIG-AR-API-02 | agent-runs API wiring behind feature gate | `api/v1/agent_runs.py`, `api/migration_agent_runs/**` |
| MIG-CONTENT-API-FIX | repair truncated test, finish content operations | `api/v1/content.py`, `application/content/**` |
| MIG-WEB-FACADE-02 | SSE transport route + server session resolution | `api/v1/web_stream.py`, `web_facade_adapter/**` |
| MIG-OWNERSHIP-CLEANUP-01 | delete duplicate grant model, update registration gates | `models/dynamic_app_grants.py`, `tests/migration/model_registration/**` |
| MIG-EQ-INGEST-03 | durable ingestion persistence over `equipment_v2_*` | `equipment_ingestion/**` |
| MIG-EQ-DURABLE-02 | offer selection + export on durable rows | `equipment_domain/**`, `equipment_api_adapter/**` |
| MIG-RES-ATTEMPT-02 | attempt store, resource-wide CAS, audit writer, caller wiring | `resource_materialization*/**` |
| MIG-CAP-GATEWAY-02 | exact-origin plugin isolation, capability/constraint boundary | `apps/web/src/capabilities/**` |

## Still to schedule

- Late constraint validation pass (`VALIDATE CONSTRAINT`) for M05 deferred FKs.
- Equipment: `project_id`/`cabinet_id` preparation revision and associative tables
  (`candidate_group_offers`, `tender_candidate_groups`) if the domain needs them.
- Web: dynamic-module screens ↔ real API verification pass; chat `runChat` cursor wiring
  against the new transport route.
- Cross-review of every promotion record and of the whole DDL set (read-only review task).
- Final verification gate: propose lifting the freeze, then run migrations/tests/builds.
