# MIG-AR-WORKER-01 — durable claim/lease/fencing worker (acceptance contract)

Status: **implementation authored, delivery blocked** — verification freeze is in force, so nothing
here was executed. Blockers are listed at the end with exact reasons.

Scope of this task (file zone):

| File | Role |
|---|---|
| `apps/api/src/prodavan/application/agent_runs/lease.py` | claim / renew / release policy, generation fencing, CAS result type, `RunLeaseStore` seam |
| `apps/api/src/prodavan/application/agent_runs/attempts.py` | durable provider-attempt journal contract, redaction, unknown-outcome policy |
| `apps/api/src/prodavan/application/agent_runs/worker.py` | claim → execution → terminal transition loop, heartbeat, cancellation, shutdown, real turn-runner adapter |
| `apps/api/tests/migration/agent_runs_worker/**` | acceptance artifacts (this file + `fakes.py` + three test modules) |

Nothing outside this zone was edited. `execution.py`, `service.py`, `retry.py`, `reconcile.py` and
`api/v1/agent_runs.py` are untouched.

---

## 1. What was studied

- `application/agent_runs/execution.py` — `AgentRunExecutionPort`, `AgentRunLease`,
  `ProviderExecutionResult`, `ProviderOutcome`, `validate_retry_result`, `AgentRunExecutor`.
- `application/agent_runs/service.py` — `EXECUTION_BLOCKERS` (the two blockers this task is named
  after), `AgentRunsQueryService`.
- `application/agent_control_plane/contracts.py` — `ControlPlaneWriteCommand`,
  `ControlPlaneAggregateVersion`, `ControlPlaneAtomicWrite`, transition validation.
- `application/agent_control_plane/service.py` — `DurableAgentControlPlane.start_run/succeed_run/
  fail_run/cancel_run/timeout_run`, transition idempotency keys, event payload shape.
- `infrastructure/persistence/agent_control_plane/repository.py` + `models.py` — one transaction for
  state/event/outbox/intent, aggregate-sequence CAS, the full `expected_run_events` /
  `expected_run_transitions` table (this is what fixes which transitions exist today).
- `application/agent/session_service.py`, `detached_turn.py`, `agent/control_plane_ports.py` — the
  real bootstrap path: `start_detached_turn` reserves a durable `agent_turns` row by `turn_id` and
  runs an autonomous task; `iter_turn_tail` yields persisted events; `_detached_events` records
  `error_code` (`CANCELLED`, `INTERRUPTED`, `INTERNAL`, …).
- `application/migration/automation_outbox/durable.py` — the in-repo precedent for
  claim/generation/token CAS and `OutboxBlockedError` semantics, reused as the design template.
- `tests/api/agent_runs/readiness_blockers.md` — blocker №4 that this task addresses.

---

## 2. Architecture decisions

### 2.1 Three tiers, no in-memory production path

1. **pure planning** (`plan_acquisition`, `plan_renewal`, `plan_release`, `inspect_lease`,
   `plan_settlement`, `next_state`) — no I/O, so "unknown never becomes success" is a unit-testable
   property rather than a hope about call order;
2. **orchestration** (`AgentRunWorker`, `DurableRunLeaseManager`, `ProviderAttemptJournal`) — talks
   only to protocols;
3. **adapters** (`RunLeaseStore`, `ProviderAttemptJournalPort`, `AgentTurnRunner`,
   `SessionAgentTurnRunner`, `RunInputSource`).

`AgentRunWorker.__init__` raises `WorkerUnavailable` when any required port method is missing, so a
host with no durable claim/lease cannot start a worker that silently drains process memory.

### 2.2 Claim semantics

- Only `queued` and the reserved `retry_due` are claimable, and only when due and not live-leased
  (`CLAIMABLE_STATUSES`, `is_claimable`).
- A claim **is a transition**: it increments `generation`, increments `attempt`, and the returned
  lease carries the aggregate sequence the claim commits (`observed_sequence + 1`, i.e.
  `agent_run.start`). The adapter must write lease + start event in one transaction; see
  `LEASE_STORAGE_BLOCKER`.
- `generation` is assigned **only** by the lease store. Nothing in the worker, the journal or the
  turn runner ever writes it, so a restart cannot reset it (asserted by
  `test_restart_does_not_reset_the_generation`).
- Leases carry an unguessable `secrets.token_urlsafe(32)` token; tokens are never derived from ids
  or time.
- Deadlines are produced from the **database** clock (`RunLeaseStore.database_now`), never from the
  API process clock.

### 2.3 CAS and "0 rows"

`CasUpdate` is the only CAS result type: it accepts `0` or `1`, treats `0` as `lost_race` (a normal
result, never an exception) and refuses any other count. Every port method in the worker returns a
boolean the same way: `False` means "another generation won", never "retry me".

### 2.4 Fencing before every write

`_fence_is_fresh` requires a heartbeat confirmed no later than one `heartbeat_interval` before the
write (or, before the first beat, that the lease is still inside its TTL). A heartbeat that returns
`False`, raises, or is missed past the deadline calls `_lose_fence`, which sets a sticky
`fence_reason` and cancels the turn. After that point:

- `plan_settlement(..., fence_ok=False)` returns `DISCARD`;
- `finish` / `schedule_retry` / `finish_unknown` are never called;
- the attempt is recorded as `discarded_stale` with `agent_run.lease_lost` — evidence, not state.

Cancellation by an operator bumps the generation (port contract), so the old worker's token no longer
matches: it observes `heartbeat() == False` on the next beat and discards its result.

### 2.5 Unknown outcomes

`ProviderOutcome.UNKNOWN` is only reachable through `finish_unknown`, which parks the run
(`FAILED` + `agent_run.provider_outcome_unknown` under the current status set — see blocker 3). It is
**never** retried blind (`validate_retry_result` already refuses `retry_at` on unknown) and **never**
recorded as `SCHEDULED`/`SUCCEEDED`.

`unknown` is reachable from:

- an explicit `ProviderOutcome.UNKNOWN` result;
- a turn runner that raises after dispatch (a crash mid-stream is ambiguous, not a failure);
- a cancellation requested by shutdown (the provider may have been called);
- an unresolved attempt left by an earlier generation (`STARTED`/`UNKNOWN` in the journal) — the
  worker parks the run instead of calling the provider twice.

### 2.6 Attempt journal

- Key: `provider-attempt:<sha256(tenant, run, generation, attempt)[:40]>`; id = derived from the key.
  A re-claim is a new generation, therefore a new attempt record; the old record keeps its outcome.
- `insert_start` is insert-once: same key + same `request_digest` replays; same key + different
  digest raises `AttemptConflict`. A stored attempt is never overwritten.
- `cas_finish` is one guarded `UPDATE`; `0` rows means the attempt already moved on.
- Metadata is allow-listed (`REDACTED_METADATA_KEYS`), forbidden keys are dropped even if they appear
  in the allow-list, and values are bounded scalars. Prompts, model output, credentials and payloads
  are stored only as sha256 digests (`prompt_digest`, `request_digest`, `lease_token_digest`,
  `assistant_text_digest`). `ProviderAttempt.__post_init__` re-redacts on every construction, so a
  caller cannot smuggle a secret through a later layer.
- `unknown` requires `finished_at` but is *not* terminal — only `reconcile` may move it
  (`CLOSED_ATTEMPT_STATES` vs `TERMINAL_ATTEMPT_STATES`).

### 2.7 Real bootstrap path (no stub)

`SessionAgentTurnRunner` calls `AgentSessionService.start_detached_turn(...)` with a system
`Principal(sub="system:agent-run-worker:<owner>", roles={platform.admin})`, then consumes
`iter_turn_tail` in a cancellable task. `turn_id` is `run:<run_id>:<generation>`, which maps onto the
existing `agent_turns(session_id, turn_id)` unique row, so a replayed send is an observation of the
same durable turn rather than a second provider call. Error codes are classified:

| agent error code | run result |
|---|---|
| `AGENT_PROVIDER_NETWORK`, `AGENT_PROVIDER_RATE_LIMIT`, `AGENT_PROVIDER_UNAVAILABLE`, `AGENT_RUNTIME_UNAVAILABLE`, `BRIDGE_UNREACHABLE`, `BRIDGE_EMPTY_STREAM`, `POD_NOT_RUNNING` | `RETRYABLE_FAILURE` with a `retry_at` |
| `INTERRUPTED`, `CANCELLED`, `BRIDGE_SEND_FAILED`, `AGENT_RUNTIME_ERROR`, `INTERNAL` | `UNKNOWN` (ambiguous) |
| any other code | `FAILED` with `agent_error.<CODE>` |
| no error code | `SUCCEEDED` with the assistant text + event count |

A cancelled tail returns `UNKNOWN`, never `FAILED` and never `SUCCEEDED`.

---

## 3. Acceptance vectors (authored, not executed)

Every row maps to a test in this directory.

### 3.1 Lease tier — `test_lease_fencing.py`

| # | Vector | Test |
|---|---|---|
| L1 | only `queued`/`retry_due` are claimable; every other status is refused | `test_claim_only_accepts_queued_and_retry_due` |
| L2 | `retry_due` before its `retry_at` is not claimable; after, it is | `test_retry_due_is_not_claimable_before_its_retry_time` |
| L3 | a live lease blocks a second claim; expiry opens it | `test_live_lease_blocks_a_second_claim_and_expiry_opens_it` |
| L4 | a claim increments generation/attempt and books `sequence + 1` for `agent_run.start` | `test_claim_increments_generation_and_books_the_start_sequence` |
| L5 | **restart does not reset the generation** (7→8, 9→10) | `test_restart_never_resets_the_generation` |
| L6 | tokens are random and unique (64 draws) | `test_claim_tokens_are_unguessable_and_unique` |
| L7 | deadline must be after the claim time | `test_deadline_after_now_is_required` |
| L8 | TTL bounds (5 s…1 h) are enforced | `test_lease_policy_bounds_are_enforced` |
| L9 | **stale generation is refused** with `GENERATION_STALE` | `test_verify_lease_refuses_a_stale_generation` |
| L10 | token and owner mismatch are refused | `test_verify_lease_refuses_a_mismatched_token_or_owner` |
| L11 | **heartbeat after the deadline** is refused (`EXPIRED`) | `test_verify_lease_refuses_after_the_deadline` |
| L12 | a terminal run refuses every lease write | `test_verify_lease_refuses_a_terminal_run` |
| L13 | a missing row is `NOT_LEASED` | `test_verify_lease_refuses_a_missing_row` |
| L14 | renewal extends without changing generation; never shortens | `test_renewal_extends_...`, `test_renewal_never_shortens_...` |
| L15 | renewal after expiry is refused, not resurrected | `test_renewal_after_expiry_is_refused_not_resurrected` |
| L16 | `inspect_lease` classifies superseded / terminal / expired | `test_inspect_reports_supersession_and_cancellation` |
| L17 | release keeps the generation and needs a domain event | `test_release_keeps_the_generation_and_needs_an_event` |
| L18 | **0-row CAS is a value**, `2`/`-1` are rejected | `test_zero_row_cas_is_a_value_not_an_exception` |
| L19 | unknown status / partial lease shape is refused | `test_lease_slot_rejects_unknown_statuses_and_partial_leases` |
| L20 | **a lost claim race returns `None`, not an exception** | `test_manager_returns_none_when_it_loses_the_claim_race` |
| L21 | release → re-claim keeps generation monotonic (1 → 2) and rotates the token | `test_manager_claim_then_release_then_claim_keeps_generation_monotonic` |
| L22 | **renew after expiry writes nothing** (`cas_renew` never called) | `test_manager_renew_after_expiry_reports_expired_and_writes_nothing` |

### 3.2 Provider attempt journal — `test_attempts.py`

| # | Vector | Test |
|---|---|---|
| A1 | attempt key is stable for `(tenant, run, generation, attempt)` | `test_attempt_key_is_stable_...` |
| A2 | a new generation is a new attempt key | `test_generation_increments_the_attempt_key_...` |
| A3 | `begin` is idempotent and never overwrites | `test_begin_is_idempotent_and_never_overwrites` |
| A4 | same key + changed digest is a **conflict**, not an overwrite | `test_same_key_with_a_different_digest_is_a_conflict` |
| A5 | `begin` refuses an already finished attempt | `test_begin_rejects_an_already_finished_attempt` |
| A6 | metadata is allow-listed (prompt/text/auth/nested dropped) | `test_journal_metadata_is_allow_listed` |
| A7 | records carry no prompt, token or payload (`repr` check) | `test_attempt_records_carry_no_prompt_token_or_payload` |
| A8 | prompt is reduced to a sha256 digest | `test_prompt_is_reduced_to_a_digest` |
| A9 | **unknown can only leave through reconciliation** | `test_unknown_can_only_leave_through_reconciliation` |
| A10 | terminal attempts are immutable | `test_terminal_attempts_are_immutable` |
| A11 | result → outcome mapping keeps `unknown` unknown | `test_result_mapping_keeps_unknown_unknown` |
| A12 | **an unknown attempt cannot be marked successful** | `test_an_unknown_attempt_cannot_be_marked_successful` |
| A13 | reconciliation is the only success path from unknown | `test_reconciliation_is_the_only_success_path_from_unknown` |
| A14 | reconciliation cannot conclude unknown | `test_reconciliation_cannot_conclude_unknown` |
| A15 | reconciling a *started* attempt is illegal | `test_reconciling_a_started_attempt_is_not_reconciliation` |
| A16 | failure transitions need a bounded machine code | `test_failed_attempts_require_a_bounded_failure_code` |
| A17 | inconsistent state/`finished_at` pairs are rejected | `test_attempt_record_rejects_inconsistent_state_pairs` |
| A18 | both blockers are declared | `test_blockers_are_declared` |

### 3.3 Worker — `test_worker_execution.py`

| # | Vector | Test |
|---|---|---|
| W1 | success writes exactly one terminal transition and one journal outcome | `test_successful_tick_writes_exactly_one_terminal_transition` |
| W2 | no eligible run → idle, no writes | `test_idle_when_no_run_is_eligible` |
| W3 | **no durable port → worker refuses to start** | `test_worker_refuses_to_start_without_a_durable_port` |
| W4 | TTL must exceed two heartbeat intervals | `test_config_requires_lease_ttl_beyond_two_heartbeats` |
| W5 | **repeated claim of the same run** is refused while leased | `test_a_second_claim_of_a_leased_run_is_refused` |
| W6 | **restart keeps the generation** (1 → 2) | `test_restart_does_not_reset_the_generation` |
| W7 | **cancel mid-flight → stale worker discards, 0 run writes, evidence recorded** | `test_stale_generation_cannot_write_and_is_recorded_as_discarded` |
| W8 | a superseded worker does not even record unknown | `test_superseded_worker_does_not_record_unknown_either` |
| W9 | **heartbeat after expiry fences the worker out** | `test_heartbeat_after_expiry_fences_the_worker_out` |
| W10 | a raising heartbeat fences the worker out | `test_heartbeat_that_raises_fences_the_worker_out` |
| W11 | confirmed heartbeats keep a long turn alive | `test_confirmed_heartbeats_keep_a_long_turn_live` |
| W12 | **cancellation during execution** cancels the turn and discards the result | `test_cancellation_during_execution_...` |
| W13 | a cancelled turn is parked as unknown, not succeeded | `test_cancelled_turn_reports_unknown_not_success` |
| W14 | **unknown outcome is never marked success** (no finish, no retry) | `test_unknown_outcome_is_never_marked_success` |
| W15 | a runner exception after dispatch is unknown | `test_runner_exception_after_dispatch_is_unknown` |
| W16 | an unresolved prior attempt blocks a second provider call | `test_unresolved_prior_attempt_blocks_a_new_provider_call` |
| W17 | a known retryable failure schedules a durable retry | `test_known_retryable_failure_schedules_a_durable_retry` |
| W18 | retry beyond the policy fails instead of scheduling | `test_retry_is_refused_once_the_policy_is_exhausted` |
| W19 | unknown + `retry_at` is unrepresentable | `test_unknown_result_cannot_carry_a_retry_time` |
| W20 | success without output is a failure | `test_success_without_output_is_a_failure_not_a_success` |
| W21 | a known failure keeps the provider failure code | `test_known_failure_uses_the_provider_failure_code` |
| W22 | settlement planner never turns unknown into success | `test_settlement_planner_never_turns_unknown_into_success` |
| W23 | settlement planner discards after a lost fence | `test_settlement_planner_discards_after_a_lost_fence` |
| W24 | run input requires prompt text | `test_run_input_requires_prompt_text` |
| W25 | missing prompt fails without calling the provider | `test_missing_prompt_fails_the_run_without_calling_the_provider` |
| W26 | a run outside tenant scope writes nothing | `test_run_invisible_in_tenant_scope_writes_nothing` |
| W27 | a run without a session never reaches the provider | `test_run_without_session_writes_nothing_to_the_provider` |
| W28 | **graceful shutdown without release keeps the lease** | `test_shutdown_without_release_support_keeps_the_lease` |
| W29 | shutdown with release returns the run to the queue, generation preserved | `test_shutdown_with_release_support_returns_the_run_to_the_queue` |
| W30 | `run_forever` drains and stops cleanly | `test_run_forever_drains_and_stops` |

---

## 4. Attempt-journal storage model (design only — no ORM here)

The table does not exist. This task's zone forbids ORM/migration edits, so the model is specified
here for the infrastructure slice that will add it.

`agent_control_plane_provider_attempts`

| Column | Type | Notes |
|---|---|---|
| `attempt_id` | `String(64)` PK | `pat_<sha256(key)[:30]>`; derived, never random |
| `idempotency_key` | `String(96)` UNIQUE | `provider-attempt:<sha256(...)[:40]>` |
| `tenant_id` | `String(64)` NOT NULL | scope, also FK `companies.id` |
| `project_id` | `String(40)` NOT NULL | FK `projects.id` |
| `run_id` | `String(40)` NOT NULL | FK `agent_runs.id` ON DELETE CASCADE |
| `session_id` | `String(40)` NULL | FK `agent_sessions.id` ON DELETE SET NULL |
| `generation` | `Integer` NOT NULL | ≥ 1 |
| `attempt` | `Integer` NOT NULL | ≥ 1 |
| `sequence` | `Integer` NOT NULL | run aggregate sequence at dispatch |
| `provider` | `String(64)` NOT NULL | e.g. `agent-runtime` |
| `provider_request_id` | `String(160)` NULL | opaque provider id, not a secret |
| `model_digest` | `String(64)` NULL | sha256, no model name |
| `request_digest` | `String(64)` NOT NULL | sha256 of the request shape |
| `prompt_digest` | `String(64)` NULL | sha256 of the user text |
| `lease_token_digest` | `String(64)` NOT NULL | sha256 of the lease token |
| `state` | `String(24)` NOT NULL | `AttemptState` values |
| `failure_code` | `String(128)` NULL | bounded machine code |
| `metadata` | `JSONB` NOT NULL default `{}` | redacted allow-list only |
| `metadata_dropped` | `Integer` NOT NULL default `0` | observability for redaction |
| `started_at` | `DateTime(tz)` NOT NULL | |
| `deadline_at` | `DateTime(tz)` NULL | watchdog deadline for the attempt |
| `finished_at` | `DateTime(tz)` NULL | required iff state is closed |
| `created_at` | `DateTime(tz)` server default | |

Constraints and indexes:

- `UNIQUE (idempotency_key)`;
- `CHECK (state IN (...))` over `AttemptState`;
- `CHECK ((state IN ('known_succeeded','known_failed','known_retryable','reconciled_succeeded',
  'reconciled_failed','abandoned','discarded_stale','unknown')) = (finished_at IS NOT NULL))`;
- `CHECK (generation >= 1 AND attempt >= 1 AND sequence >= 1)`;
- `INDEX (run_id, generation)` — the worker's unresolved lookup;
- `INDEX (state, started_at)` — the reconciler's unresolved scan;
- append-only for `started` rows: no `UPDATE` may change `idempotency_key`, `request_digest`,
  `generation`, `attempt`, `started_at` (enforced by the port's single-column `cas_finish`).

Port implementation contract (mirrors `attempts.ProviderAttemptJournalPort`):

- `insert_start` — `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` then read; if the stored
  `request_digest` differs, raise `AttemptConflict`;
- `find_unresolved` — newest `state IN ('started','unknown')` row for the run;
- `cas_finish` — `UPDATE ... SET state=?, finished_at=?, failure_code=?, provider_request_id=?,
  metadata=?, metadata_dropped=? WHERE attempt_id=? AND state=?` returning `rowcount`.

---

## 5. Unresolved risks and blockers

1. **`agent_run_lease_storage_missing`** — `agent_runs` has no `lease_generation`, `lease_token`,
   `lease_until` or `available_at` columns, and no Alembic revision adds them. This task's zone
   excludes `infrastructure/persistence/**` and `alembic/versions/**`, so `RunLeaseStore` is a seam
   with a written contract but no SQL implementation. Until the infrastructure slice lands:
   `AgentRunWorker` cannot be constructed against a real database and `claim_next` has no adapter.

2. **Claim must co-commit `agent_run.start`** — `DurableAgentControlPlane.start_run` needs
   `expected_sequence` and writes state + event + outbox + queue intent atomically. A pure lease
   store cannot write events, so the adapter must compose the lease CAS with that transition in one
   transaction. `LeaseSlot.sequence` (observed) and `lease.sequence` (`+1`, the start transition) are
   exposed for exactly this composition.

3. **`agent_run_reconciled_terminal_transition_unwired`** — `AgentRunStatus` has no
   `needs_reconciliation` member and the repository's `expected_run_transitions` table accepts no
   such operation. An unknown outcome is therefore parked as `FAILED` +
   `agent_run.provider_outcome_unknown`; it is fail-closed (never success, never blind retry) but it
   is not yet a *distinguishable* state. A dedicated status plus `agent_run.outcome_unknown` /
   `agent_run.reconciled` transitions are required for the reconciler to resume a parked run.

4. **`agent_run_requeue_transition_unwired`** — nothing may move a `running` run back to `queued`.
   Two consequences: (a) graceful shutdown can only release the lease when the adapter exposes
   `release_lease`, which the current port protocol does not define — without it the worker logs the
   blocker and lets the TTL expire (safe, but the run waits out the TTL); (b) claim covers only
   `queued`/`retry_due`, so a run left `running` behind an expired lease is *not* taken over by the
   worker; a reconciler must requeue it (with the requeue event) first.

5. **`agent_run_provider_attempt_journal_unwired` / `..._table_missing`** — the durable attempt
   journal is an abstraction; the table (§4) and its revision are a separate slice. The worker
   refuses to construct without a journal.

6. **Journal write is not co-committed with the run transition.** The port has no method that writes
   both atomically. The worker writes the run transition first, then the attempt outcome. If the
   second write fails, the attempt stays `started`, and the next claim parks the run for
   reconciliation (fail-closed, but one extra reconciliation step). A future port method
   `finish_with_attempt(lease, result, attempt_outcome)` would close this window. The reverse order
   was rejected on purpose: an attempt marked finished with no run transition loses the outcome
   entirely.

7. **Retry scheduling** — `schedule_retry` is a port requirement only; the durable retry scheduler
   (moving `retry_due` runs back into the claim window) does not exist yet, and `AgentRunStatus` has
   no `retry_due` member (the lease tier accepts the reserved value, the port must persist it).

8. **`SessionAgentTurnRunner` is wired but unverified against a live runtime** — it reuses the
   production `start_detached_turn` / `iter_turn_tail` path, including the process-local
   `detached_turns` registry that `detached_turn.py` documents as single-replica. Multi-replica
   workers require the durable `agent_turns` ledger to be the only authority for cancellation (it
   already is for idempotency), which is worth an explicit review before scaling out.

9. **Blocked waiting on the port contract**: `release_lease` and a reconciled terminal transition
   are new port methods; they were specified here instead of added to `execution.py`, which is out of
   zone.

---

## 6. Verification

No tests, build, compile, lint, formatter, `pytest`, `alembic`, DDL or git checks were run. The
acceptance vectors above are reasoned from the code and contracts, per the migration verification
freeze. `__pycache__` directories under `apps/api` were removed once to free disk space after a
`ENOSPC` write failure; they are regenerated artefacts and contain no source.
