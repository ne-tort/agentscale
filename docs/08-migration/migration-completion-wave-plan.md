# Migration completion — wave plan (continuing to functional replacement)

Status: active. Owner: orchestrator agent. Updated: 2026-11-04.

## Verification freeze is LIFTED

The user lifted the freeze. Tests are now executed, not merely authored, and a task
is not finished until its directory is green. The first full run collected **2739
tests** under `tests/migration` and turned a large set of "authored" acceptance
artifacts into evidence — and immediately found defects that reading had missed.

Run command:

```powershell
cd apps/api; $env:PYTHONPATH="apps/api/src"
python -m pytest tests/migration/<dir> -q --no-header -p no:cacheprovider
```

## What the first runs proved (production defects only)

Reading the code had not found these; running it did:

- `retry.py` / `reconcile.py` called legacy `column()`, which rejects
  `primary_key`/`nullable`, so both modules failed at import.
- `reconcile` reused a resolve-only reason code as a run `failure_code`, a field
  validated as a redacted `agent_run.error.*` code — a resumed failure could never
  be applied.
- `rls/context.py` and `tenant_context.py` read the readback row by GUC name while
  the SQL aliases columns without the prefix, so restoring previous values wiped
  the caller's live tenant context.
- `web_facade` contracts called `.value` on `RunStreamEvent.type`, which
  `use_enum_values` turns into `str`: the first canonical SSE frame raised on the
  live `run_chat` path.
- `migration_definition_version` adapter never awaited its RLS probe, so the
  reviewed-RLS gate passed unconditionally.
- `ScopeMigrationCoordinator.resume_checkpoint` checked for a missing port before
  lineage, reporting a foreign-tenant cursor as retryable instead of terminal.
- `scope_registry_adapter` returned a tuple where `ScopeResolutionEvidence` was
  required, and its port could not express the rls/actor evidence ACTIVE needs.
- `module_capabilities` lost a `params_schema` assignment (every legacy module
  failed normalization), built `_TranslatedPayload` without two required fields,
  treated `row_filter` keys as UI vocabulary, and required a stable ID for the
  platform `project_ids` column.
- `equipment` validated only the shape of a currency code, so `ZZZ` passed.
- `automation_service/__init__.py` re-exported names that `contracts` exposes only
  lazily, breaking the **first** import of `automation_persistence` in production.
- `parity_runner.run_key_for` keyed on the observed evidence digest, so a rerun with
  changed evidence was silently RECORDED instead of CONFLICT.
- `executor`'s rollback branch ignored `receipt.succeeded`, so a refusal could be
  recorded as an applied generation.
- `dynamic_apps` CRUD called `dataclasses.replace` on frozen pydantic models and
  used an unimported `GrantActor`.

## Standing lesson

Most remaining failures were fixtures modelling states the domain forbids
(`dataclasses.replace` on pydantic models, identities that raise in their own
constructor, "partial" runs with exhausted checkpoints, receipts the type refuses
to build). The rule applied throughout: fix the fixture, never loosen the
assertion, and where possible make the check stricter than before.

Dozens of gates were also found to be **vacuous** and were rewritten to check a
property rather than a constant: hardcoded chain heads, `inspect.iscoroutinefunction`
called on a `Signature`, raw-text scans matching their own docstrings, substring
checks against their own literals, and an anti-duplicate check that only compared
`__init__` imports.

## DDL chain (linear, single head `2026110310`, 193 revisions, no forks)

| Rev | Content |
|---|---|
| `2026110202` | agent control plane (pre-existing head) |
| `2026110301` | M04 owner registry, 7 tables + `uq_projects_scope_owner_parent_tuple` on retained `projects` |
| `2026110302` | M04E definition/version, deferred current-version FK, immutability trigger |
| `2026110303` | M05 plugin instance registry, 7 deferred reciprocal FKs |
| `2026110304` | canonical grants, 6 tables, artifact-pinned version FK |
| `2026110305` | equipment v2, 8 tables |
| `2026110306` | resources + runtime revisions + attempt/audit/head tables |
| `2026110307` | RLS policies from the shared registry |
| `2026110308` | `VALIDATE CONSTRAINT` for the deferred FKs (M04E, 7× M05, grant→instance) |
| `2026110309` | `agent_run_leases` — durable execution fence |
| `2026110310` | re-applies the RLS registry so tables created after 0307 get policies |

Design invariants worth keeping:

- DDL revisions verify composite-FK targets fail-closed inside the revision, so a
  missing target aborts with a named blocker instead of leaving an invalid schema.
- M05 reciprocal FKs stay `DEFERRABLE INITIALLY DEFERRED` until `0308` validates them.
- M04E's trigger is stricter than the ORM guard: it also rejects `non-draft -> draft`
  rewinds, otherwise immutability is bypassable via `published -> disabled -> edit`.
- `agent_run_leases` exists because the execution contract needs `lease_generation`/
  `lease_token`/`lease_until` and no table carried them before.

## Landed application work

- **Agent runs**: dispatcher, worker, lease manager, attempt journal, retry,
  reconciler; durable intent consumer (`FOR UPDATE SKIP LOCKED`, DB-clock lease,
  fenced writes, redacted evidence); feature-gated dependency with ordered gates
  (flag → metadata → database) and post-commit dispatch with a bounded trigger.
- **RLS**: trusted tenant context plus a 30-table policy registry, including the
  equipment chain (tenant inherited through ingestion run / request) and the new
  lease table.
- **M04/M04E/M05/M06**: scope persistence adapter, definition/version backfill with
  parity and M05 handoff marker, instance lifecycle/CAS/promotion, grant adapter
  with parity runner and bounds, cutover gates, parity report store.
- **Equipment**: v2 DDL, post-commit search indexer, domain selection/export.
- **Content/Resources**: content contract and services, resource application layer
  and HTTP router.
- **Web**: SSE parser/cursor/envelope wired into chat, `Last-Event-ID` support,
  dynamic-module screens with service/validation/access, capability exact-origin
  boundary.
- **Ownership**: canonical grant duplicate declaration removed; registration gates
  updated; `AttributeError("retired")` + `ModuleNotFoundError` fail-closed.

## Done since this section was first written

| Item | Outcome |
|---|---|
| Host wiring for both poll loops | `AgentRunWorkerResource` registered in the lifespan, two loops under separate advisory locks, disabled by default. |
| Execution adapter + lease store | `SqlAlchemyRunLeaseStore` + `ControlPlaneAgentRunExecutionAdapter`; `retry_due` startable end to end. |
| Host dependency bundle | `agent_run_bundle` builds all five members from the session factory and publishes them on the seam; `wired` is True. |
| Equipment ingestion persistence | Durable run/snapshot/offer writes with resume and idempotent replay. |
| Cross-review of DDL + seams | Both reviews landed; every finding either fixed or recorded as a decision. |
| Legacy chat ↔ durable run binding | `run_binding` bridges `(project, session, turn_id) -> run_id`; stream frames are now an expressible operation. |
| Dynamic-module screens ↔ API | Router, application layer and web screens all present and green. |

## Remaining work — all five items closed

The table below was the open list. Every row is now delivered and verified against a
live, fully migrated database, so it is kept as a record rather than a backlog.

| Item | Status |
|---|---|
| `TrustedPrincipalResolver` | **Done.** `principal_resolver.py` ships `AuthBoundaryPrincipalResolver` over the durable `auth_sessions` table (revision `2026110317`), written from `api/deps.py::get_principal` behind `AUTH_SESSION_STORE_ENABLED`. |
| Cutover-state tables | **Done.** `2026110316` creates `m06_partition_cutover_evidence` and `m06_partition_cutover_source_tail`, so `SqlCutoverRepository` no longer fails closed on missing tables. It still refuses a *positive* cutover, which is the gate, not a schema gap. |
| Migration composition root in the lifespan | **Done.** `core/wiring.py` registers `MigrationChainResource`; with `MIGRATION_CHAIN_ENABLED=true` the chain is composed by a delivered process (verified by listing the lifespan resources). |
| PostgreSQL concurrency suite | **Executed.** `tests/migration/postgres_integration` runs 37 passed / 2 skipped against live PostgreSQL: `SKIP LOCKED`, aggregate CAS, transaction abort and the deferrable cycle. |
| Equipment tenant anchors | **Done.** `2026110319` adds `project_id`/`cabinet_id` to the three `equipment_v2` operational tables plus the two association tables, re-applies the RLS registry and verifies it in the same transaction; the downgrade restores the pre-anchor predicate instead of leaving the tables unpolicied. |

## How an operator runs this

The chain is reachable from a delivered process (`MigrationChainResource`, behind
`MIGRATION_CHAIN_ENABLED`) and, since the operator CLI landed, from a command:

```text
python -m prodavan.migration_cli partitions
python -m prodavan.migration_cli plan  --tenant <id> --definition <id> [--stage <name>|all]
python -m prodavan.migration_cli run   --tenant <id> --definition <id> --stage <name> --execute
python -m prodavan.migration_cli cutover-status --tenant <id> --definition <id>
```

Four properties, each pinned by a test:

- **Dry run is the default.** `run` without `--execute` produces a plan and writes
  nothing; forgetting the flag cannot silently migrate.
- **Refusal is first-class.** Exit 0 means the command completed, not that the migration
  succeeded. A correctly refused cutover is a successful command; `--strict` is what CI
  uses to make a refusal fail.
- **Output is codes, never payloads.** A blocker prints its code and reason — never a
  DSN, a manifest or tenant data.
- **`all` is plan-only for `--execute`.** Several dependent stages run for real in one
  command would make a partial failure indistinguishable from a refusal.

`partitions` also reports the legacy source honestly: `dynamic_app_definitions` predates
the migration and no revision creates it, so on a revision-only database it is absent and
the command says so rather than reporting "0 partitions" as though it had looked.

## Verification baseline (measured, not asserted)

On a database created empty and migrated with `alembic upgrade head` to `2026110319`:

| Suite | Result |
|---|---|
| `tests/migration tests/unit` | 6187 passed, 0 failed |
| `tests` (all, live PostgreSQL, excluding `tests/e2e`) | 6519 passed, 34 skipped |
| `tests/integration` | 191 passed, 1 skipped |
| `tests/migration/rls` | 510 passed |
| `tests/migration/postgres_integration` | 37 passed, 2 skipped |
| `apps/web` vitest | 596 passed, 82 files |
| `apps/web` tsc / vite build | 0 errors / builds |
| Playwright smoke | 21 passed, 7 skipped (live layer needs credentials) |

The one full-suite failure class seen in this session — two `principal_resolver` tests
that expired only in runs longer than twenty minutes — is fixed and proved by simulating
the drift rather than by re-running and hoping.

## Still not done, and why it needs a human decision

- **No real backfill or cutover has ever run.** Every promotion record stays `blocked`;
  `DECISION_OWNER` and `CANONICAL_READ_OWNER` remain `legacy` and
  `CANONICAL_POSITIVE_WRITES` is `False`. Rows in a test database prove the pipeline
  works; they do not authorise running it against real tenant data.
- **Which database** the first rehearsal targets is an operator decision, not an
  engineering one.
- **`tests/e2e/live`** needs a healthy deployed contour: it points at `127.0.0.1:8088`,
  which answers `/health/live` but returns 503 from `/health/ready`, so those tests fail
  on the environment rather than on the code.

## Boundary decisions recorded in this session

- **`LifespanManager.lifespan` forwards the ASGI app** to resources exposing
  `bind_app`, before startup. A per-zone subclass would have left the pod surface on
  the old behaviour, so the seam lives in the shared manager instead.
- **`run_row_id` is `run_window_prefix(...) + digest`.** The earlier derivation
  hashed only the window, so a run id never matched its own `LIKE '<prefix>%'`
  admission scan and two tenants could collide on one primary key.
- **`Retry_due` is a first-class `AgentRunStatus`.** It was listed in
  `CLAIMABLE_STATUSES` with no enum member and no start transition, so a retryable
  run could never be claimed again.
- **Retained agent tables stay outside RLS** (see gaps below), and
  `agent_run_provider_attempts` is covered by the registry like the rest of the new
  schema.

## Evidence-schema decisions (reviewed, not accidental)

The four evidence ports declare five schema blockers. Each is classified below so a
future reader can tell a missing table from a deliberate trade-off.

| Blocker | Verdict |
|---|---|
| `m04_scope_promotion_evidence_missing` | **Fixed** by `2026110313` (new `m04_scope_promotion_evidence` table). This was the only reason `load_partition` could never succeed. |
| `promotion_outcome_cannot_express_ready` | **Fixed for new rows.** The older promotion tables are CHECK-limited to `blocked|quarantined`, which is why the true status lives in the redacted envelope. The new table admits `ready` directly. Existing tables keep their constraint: rewriting an applied CHECK would be a data migration, not a schema fix. |
| `m05_promotion_record_carries_no_source_inventory` | **Accepted design.** M05 holds counts only; the M06 inventory is reconstructed from the mapped grant sources of the exact partition, which is what `AcceptedPartitionEvidence.admits` actually compares. Storing a second copy would create two inventories that can drift. |
| `m05_writer_generation_zero_has_no_m06_epoch` | **Correct fail-closed.** `advance_writer_generation` increments from an expected value, so a *published* instance always has `writer_generation >= 1`; a promotion record at generation 0 contradicts its own lifecycle. The port refuses instead of normalising `0 -> 1`, because normalising would mint a fence token nobody granted. |
| `m04e_backfill_has_no_checkpoint_or_source_mapping_table` | **Accepted compromise.** The page cursor and first-mapping ledger live in the redacted envelope of the append-only promotion row, with `revision_id` acting as the cursor CAS token through `uq_m04e_promotion_revision`. Append-only and CAS-safe, but a plain-SQL cursor query is impossible; a dedicated table would be better and is a candidate for the next schema wave. |

## Known, named gaps (not oversights)

- **RLS on retained agent tables is absent.** `agent_runs` and
  `agent_control_plane_*` have live callers; installing policies before every caller
  sets the tenant GUC would break production traffic. Only the new
  `agent_run_leases` and `agent_run_provider_attempts` are covered.
- **`claim` and `agent_run.start` are two transactions.** `ControlPlaneAtomicWrite`
  opens its own session, so the order is lease → start with compensation, and a
  crash between the steps leaves an expiring lease. Needs a control-plane port that
  accepts an external transaction.
- **No `requeue` transition**, therefore `release_lease` is deliberately not
  implemented — a release without the aggregate transition would desynchronise
  status from lease.
- **Unknown-outcome cases are parked but not journalled** in the reconcile journal.
- **Legacy control frames (`_session`, `_turn_complete`)** are rejected by the
  canonical adapter (502, fail-closed) rather than translated.
- **No production promotion anywhere.** Every package keeps its promotion record
  `blocked`; canonical positive writes stay disabled until gates, RLS review and an
  explicit decision.
- **Alembic has now been exercised against a live database.** `upgrade head` on an
  empty database applies all 199 revisions to `2026110319` and the result carries 86
  policies with no name over 63 characters; `downgrade 2026110318` and re-upgrade were
  run twice on `equipment_anchors` and once on the RLS rename, both deterministic. The
  PostgreSQL concurrency suite runs rather than skips. Nothing has been applied to any
  shared environment.
