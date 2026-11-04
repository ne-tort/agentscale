# MIG-AR-EXEC-02 — acceptance matrix

Status: authored only. **Nothing here was executed** (migration verification
freeze); behaviour is argued from the code, the rendered SQL and the written
case matrix.

Zone:

- `apps/api/src/prodavan/infrastructure/persistence/agent_control_plane/lease_store.py`
  — SQL `RunLeaseStore` (+ `cas_park` / `cas_fence` / `claim_scope`).
- `apps/api/src/prodavan/application/agent_runs/execution_adapter.py`
  — `AgentRunExecutionPort` composed from the lease store and the control plane.

## 1. Why two artifacts per case

The lease table, its CHECK constraints, its indexes and revision `2026110309`
belong to the persistence slice; this zone owns the *statements* and the
*composition*. So every case is covered twice where it matters:

| Layer | File | What it proves |
|---|---|---|
| SQL predicate | `test_lease_store_sql.py` | the rendered PostgreSQL of each statement: which columns fence it, that it is exactly one `UPDATE`, that `SKIP LOCKED` locks only the lease row |
| Fenced composition | `test_execution_adapter.py` | the real `DurableAgentControlPlane` + `DurableRunLeaseManager` over doubles that reproduce the store's row-count semantics |

`fakes.py` holds the doubles. They are test-only; `prodavan` never imports them,
and the adapter refuses to construct without a durable lease store and control
plane, so no double can reach a production path.

## 2. Required cases

### 2.1 Claim happy path

- `test_claim_happy_path_commits_start_in_one_transition` — one claim produces
  `generation=1`, `attempt=2`, `sequence=2`, a durable lease row holding owner,
  token and deadline, the run `running` with `sequence=2`, exactly one
  control-plane transition (`agent_run.start`, expected sequence 1, event
  `agent_run.started`), and exactly one claim scope (one pinned transaction for
  select + CAS).
- `test_claim_returns_none_when_nothing_is_claimable`.
- `test_adapter_satisfies_the_execution_port` — every `AgentRunExecutionPort`
  operation exists, `is_wired is True`, no wiring blocker.

### 2.2 Two workers do not take one run

- `test_two_workers_do_not_take_the_same_run` — the second claim finds nothing;
  the first owner and the single start transition survive.
- `test_losing_the_acquire_race_is_zero_rows_not_an_exception` — both workers
  plan from the same slot; the second `cas_acquire` returns `0`.
- `test_a_live_lease_is_not_stolen` — a live lease hides the run from the
  candidate query.

### 2.3 Expired lease is reused

- `test_expired_lease_is_reused_with_a_newer_generation` — a run left behind
  with `generation=3`, `attempt=2` and an expired deadline is claimed as
  `generation=4`, `attempt=3`, with a fresh token.
- `test_expired_lease_cannot_be_finished_or_renewed` — after the deadline the
  old worker gets `False` from both heartbeat and finish.

### 2.4 Stale generation → 0 rows, no exception

- `test_stale_generation_finish_writes_nothing_and_does_not_raise`.
- `test_stale_generation_schedule_retry_and_park_write_nothing`.
- `test_reconcile_unknown_refuses_a_stale_generation`.
- `test_cancellation_bumps_the_generation_and_fences_the_worker` — after a
  cancel the old lease cannot publish output.
- SQL: `test_acquire_is_one_generation_and_attempt_fenced_update`,
  `test_every_cas_statement_is_a_single_update`.

### 2.5 Renew / release without the token → 0

- `test_renew_and_release_without_the_token_affect_zero_rows` (renew, release and
  park all `0`; the real lease stays intact).
- `test_heartbeat_without_the_token_is_refused`.
- SQL: `test_renew_requires_token_owner_and_a_live_deadline`,
  `test_release_requires_generation_token_owner_and_the_asserted_status`,
  `test_park_keeps_the_generation_and_sets_the_retry_gate`.
- SQL: `test_fence_bumps_the_generation_for_the_tenant` — the one statement that
  is tenant-scoped instead of token-scoped (cancellation is performed by an actor
  that does not hold the lease).

### 2.6 `retry_at` in the future is not claimed

- `test_retry_at_in_the_future_is_not_claimable` — nothing is claimable before
  the gate, and the gate survives the claim that follows it.
- `test_schedule_retry_parks_the_lease_and_keeps_the_fence` — the durable gate is
  written, the worker stops holding the lease, and the generation (the fence the
  retry/reconcile path will present) is preserved.
- `test_schedule_retry_refuses_an_already_due_window`,
  `test_schedule_retry_rejects_free_text_failure_codes`.
- `test_retry_due_rows_are_not_claimed_until_the_requeue_exists` — a `retry_due`
  row is neither claimed nor allowed to block the queue behind it
  (:data:`RETRY_DUE_BLOCKER`); the `queued` run is claimed instead.
- SQL: `test_claim_candidate_applies_status_due_and_deadline_predicates`,
  `test_claim_candidate_can_be_narrowed_to_one_status`,
  `test_claim_candidate_refuses_an_empty_or_widened_status_set`.

### 2.7 Attempt increments

- `test_attempt_increments_on_every_claim` — `2 → 3` across two claims, with the
  generation moving `1 → 2`.
- SQL: `test_acquire_is_one_generation_and_attempt_fenced_update` — the attempt
  counter is part of the CAS predicate, so a claim from a stale observation is
  `0`.

### 2.8 Unknown blocks the next attempt

- `test_unknown_parks_the_run_and_blocks_a_new_provider_attempt` — the run stays
  `running` with no lease, a second worker cannot claim it, and the old worker
  can neither `finish` nor re-park it.
- `test_reconcile_unknown_resolves_a_parked_run_and_seals_the_fence` — a proven
  outcome settles the run, seals the generation (`+1`), and a replay with the old
  generation is refused.
- `test_reconcile_unknown_refuses_a_live_lease_or_a_wrong_tenant`,
  `test_finish_refuses_unknown_and_retryable_results`.

## 3. Fail-closed cases beyond the required list

| Case | Test |
|---|---|
| A claim whose `agent_run.start` did not apply gives the lease back (run stays `queued`, generation preserved, next claim is `+1`) | `test_a_failed_start_gives_the_lease_back` |
| A run with no resolvable employee actor is never written and never left leased | `test_a_run_without_a_resolvable_actor_is_never_written` |
| A compensation that cannot be applied is not fatal and leaves no open scope/lock | `test_claim_compensation_survives_a_control_plane_that_keeps_failing` |
| Cancellation with a stale sequence or out-of-scope project writes nothing | `test_cancellation_refuses_a_stale_sequence`, `test_cancellation_refuses_an_out_of_scope_or_invalid_request` |
| Renewal never shortens an existing deadline | `test_renewal_extends_the_deadline_and_never_shortens_it` |
| The adapter does not expose a half-wired `release_lease` | `test_adapter_does_not_pretend_to_release_a_lease` |
| An incomplete dependency set is refused at construction | `test_adapter_refuses_an_incomplete_dependency_set` |

## 4. Statement-shape guarantees (`test_lease_store_sql.py`)

- `claim_candidate_statement`: `LIMIT 1`, `FOR UPDATE OF agent_run_leases SKIP
  LOCKED` (never locks `agent_runs`), deterministic `ORDER BY retry_at,
  lease_until, run_id`, inner joins on `agent_runs` **and**
  `agent_control_plane_aggregates` (a run without a committed cursor is not
  claimable — there is no default sequence), status/due/deadline predicates.
  Its optional `statuses` argument is validated to be a non-empty **subset** of
  `CLAIMABLE_STATUSES`, so narrowing cannot widen eligibility by accident.
- `acquire_statement`, `renew_statement`, `release_statement`,
  `park_statement`, `fence_statement`: exactly one `UPDATE`, no `INSERT`, no
  `DELETE`, and the fence columns are asserted by rendering the `WHERE` clause.
- `release_statement(status="queued")` also clears `retry_at`, so a released run
  is immediately due again; every other release keeps the parked gate.
- `test_store_holds_no_in_memory_state` — the only mutable attribute is the
  pinned transaction; there is no fallback store.
- `test_lease_table_is_the_row_the_persistence_slice_authored` — the store
  targets `agent_run_leases`, `run_id` PK with `FK agent_runs.id ON DELETE
  CASCADE`, the four named CHECK constraints and the two named indexes.

## 5. Deliberate gaps (carried as blockers, not hidden)

1. `agent_run_requeue_transition_unwired` — there is no `agent_run.requeue`
   transition, so the adapter does **not** implement `release_lease` and
   `schedule_retry` leaves the run `running` with a durable gate. The retry
   scheduler's requeue must be that transition; the gate is already durable so
   the claim side needs no change when it lands.
2. `agent_run_retry_due_start_transition_unwired` — `RETRY_DUE_STATUS` is in
   `CLAIMABLE_STATUSES` but `RunTransitionCommand` only accepts `AgentRunStatus`
   and validates `queued` → `running` for `agent_run.start`, so a `retry_due` row
   has **no expressible start transition**. The adapter therefore narrows its
   claim predicate to `queued` in SQL, so a `retry_due` run neither burns a fence
   generation per poll nor blocks the queue. Closing this needs a
   `retry_due` → `running` start (or the requeue above) in the control plane.
3. `agent_run_unknown_case_journal_unwired` — the ambiguous attempt is parked
   durably but no row is written to the reconciliation journal
   (`agent_runs/reconcile.py` owns that table, no revision exists).
4. `agent_run_control_plane_actor_unresolved` — the control plane re-authorises a
   live employee on every write, so a run whose requester no longer resolves (and
   no service actor is configured) cannot be started, finished or cancelled.
5. The claim and its `agent_run.start` are **not one SQL transaction**:
   `ControlPlaneAtomicWrite` opens its own session, so the adapter orders the two
   steps lease-then-start and compensates a failed start by releasing the lease
   (generation preserved). A crash between the steps leaves an expiring lease —
   the documented expired-lease path. Closing this needs a control-plane port
   that accepts an externally supplied transaction.

## 6. Not run

`pytest`, `vitest`, builds, `tsc`, compile, lint, format, `alembic upgrade`,
DDL, database access and every git command were intentionally **not** executed
for this artifact. Only native file tools plus read-only `python -c` imports
(model/statement metadata) were used.

## 7. Composition recipe (for the DI/composition slice)

```python
leases = SqlAlchemyRunLeaseStore(session_factory=control_plane_session_factory)
repository = SqlAlchemyAgentControlPlaneRepository(session_factory=...)
control_plane = DurableAgentControlPlane(transaction=repository, tenant_id=tenant_id)
port = ControlPlaneAgentRunExecutionAdapter(
    leases=leases,              # FencedLeaseStore: lease store + park/fence/claim_scope
    control_plane=control_plane,  # or a tenant_id -> control plane factory
    runs=repository,            # AgentControlPlaneReadPort; also satisfies RunInputSource
    service_actor_id=None,      # only if runs may have no requester
)
worker = AgentRunWorker(port=port, runs=repository, journal=..., runner=..., config=...)
```

Both objects must point at the same database: the lease store joins
`agent_run_leases` to `agent_runs` and
`agent_control_plane_aggregates`. `prodavan.application.agent_runs.__init__`
does **not** re-export the adapter (that file is outside this task's zone);
import it from `prodavan.application.agent_runs.execution_adapter`.
