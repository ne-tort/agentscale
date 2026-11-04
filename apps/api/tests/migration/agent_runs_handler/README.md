# MIG-AR-HANDLER-01 — acceptance artifacts (authored, not executed)

Verification freeze is in force: every module here is written to be runnable and
is **not executed** by this task. Correctness of the production code is argued
from the contracts in `application/agent_runs/handler.py`,
`dispatcher.py`, `execution_adapter.py`, `worker.py` and the persistence
consumer; the cases below pin each argument to an assertion.

## Files

| File | Covers |
|---|---|
| `acceptance_fixtures.py` | The composed harness: one `MemoryPlatform` (run row + lease row + aggregate cursor), the real `FakeAtomicTransaction` aggregate CAS, `MemoryLeaseStore` (SQL-shaped fenced CAS), a read port that satisfies both `AgentControlPlaneReadPort` and `RunInputSource`, the real `ProviderAttemptJournal` over a doubled port, a scripted turn runner, and a `MemoryIntentStore` that reproduces the durable consumer's reservation answers. |
| `test_intent_delivery.py` | Delivery happens at all: one intent → one claim → one turn → one terminal transition → `delivered`. Addressed claim (a neighbouring queued run is untouched), host composition seam, terminal refusal of a non-executed kind, scope/supersession refusals, and redaction of logs + audit. |
| `test_idempotency.py` | A re-delivered intent never executes twice: consumer-level replay (`ALREADY_DELIVERED`) and in-flight (`IN_FLIGHT_UNKNOWN`), handler-level replay against a terminal run, a run owned by another claim, and one-settlement-per-claim. |
| `test_fail_closed_paths.py` | Every refusal: named blockers per missing dependency, factory failure/no-port, bounded retry backoff, attempts-exhausted terminal refusal, invisible run, projection read failure, stale-fence discard (quarantine without corruption), the terminal-refusal handler, and the full worker-tick → dispatch-outcome decision table. |

## Acceptance matrix (task requirements → assertions)

1. **Intent executes and is marked delivered** —
   `test_created_intent_executes_one_turn_and_is_delivered`: `agent_run.start` +
   `agent_run.succeed` committed, prompt executed once through
   `AgentRunWorker`/`AgentTurnRunner`, intent row `delivered` with
   `action=deliver reason=intent_delivered`.
2. **Repeat delivery does not execute again** —
   `test_duplicate_delivery_of_a_delivered_intent_does_not_execute`,
   `test_replay_against_a_finished_run_is_delivered_without_executing`,
   `test_duplicate_delivery_of_an_in_flight_intent_is_quarantined`,
   `test_run_already_owned_by_another_claim_is_quarantined`.
3. **Unknown kind → terminal refusal** —
   `test_unknown_intent_kind_is_refused_terminally_by_the_dispatcher` (dispatcher
   classification) and `test_handler_refuses_a_kind_it_does_not_execute` (handler
   defence in depth): `action=reject reason=intent_kind_unknown`, never a retry.
4. **Unavailable dependency → named blocker, not delivered** —
   the five parametrized blockers, factory failure, and
   `test_dispatcher_records_the_blocked_retry_durably`: `action=retry`, row stays
   `pending`, run untouched, `delivered_at` NULL.
5. **Stale lease generation → refusal without corruption** —
   `test_stale_generation_mid_turn_is_quarantined_without_corruption`: the turn's
   result is journalled `discarded_stale`, no success is written, the run keeps
   the other generation's state, intent is quarantined (never blind-retried).
6. **Audit without secrets** —
   `test_delivery_logs_contain_no_secret_and_no_prompt`: captures everything the
   `prodavan.application.agent_runs` package logs on both the success and the
   blocked path, and asserts the secret marker is absent from logs, handler
   reports and every audit record; the audit vocabulary is restricted to
   `action/reason/kind/attempts`.

## What the doubles do *not* stand in for

The lease store, control-plane transaction, attempt journal storage, turn runner
and intent row are doubled. The dispatcher, handler, execution adapter, worker,
attempt journal object and control-plane service are the real modules. No
double is importable from `prodavan`; the handler cannot be constructed without
its ports.
