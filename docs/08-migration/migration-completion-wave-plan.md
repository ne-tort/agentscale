# Migration completion — wave plan (continuing to functional replacement)

Status: active. Owner: orchestrator agent. Updated: 2026-11-04.
Verification freeze is in force (see `AGENTS.md`): acceptance artifacts are authored,
nothing is executed.

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

## Remaining work

| Item | Why it matters |
|---|---|
| Host wiring for both poll loops | Consumer and worker are libraries; nothing starts them in a delivered process. Use the `trigger_worker.py` pattern (advisory lock, `drain_once`, lifespan) + settings flags. |
| Execution adapter + lease store | In progress: `RunLeaseStore` SQL over `agent_run_leases` and `AgentRunExecutionPort` composition. |
| Equipment ingestion persistence | Durable run/snapshot/offer writes with resume and idempotent replay. |
| PostgreSQL concurrency suite | Concurrent idempotent reservation, aggregate CAS, transaction abort, claim races, deferrable cycle, partial unique, migration round-trip. |
| Cross-review of DDL + seams | Independent read-only review of the chain and of M04→M04E→M05→M06 ports. |
| Dynamic-module screens ↔ API | Verify the web service contract matches the implemented router. |
| Equipment tenant anchors | `project_id`/`cabinet_id` and associative tables if the domain needs them. |
| Host dependency bundle assembly | `AgentRunWorkerResource` exposes `register_host_dependency_provider` but nothing registers the bundle yet, so both loops idle with `agent_run_host_dependencies_unwired`. Needed: execution port (done), run source, attempt journal, turn runner. |
| Legacy chat ↔ durable run binding | The real chat path writes only `agent_events`; the canonical SSE index reads `agent_runs` + `agent_control_plane_events`, so canonical frame identity is unprovable until a run binding exists. |

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
- **Freeze still on.** No tests, builds, migrations or DDL have been executed.
