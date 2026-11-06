# MIG-AR-HOST-01 — acceptance matrix (artifacts, not executed)

**Task:** host wiring for the durable Agent Run background loops in the API
lifespan. **Stage rule:** verification freeze — nothing below is run; the tests
are checked by reading them against the resource contract.

Implementation: `apps/api/src/prodavan/core/infra/agent_run_worker_resource.py`.
Registration: `apps/api/src/prodavan/core/wiring.py` (import + one
`manager.register(...)` after the database resource). Settings: six additive
fields in `apps/api/src/prodavan/config/settings.py`.

## What the resource owns

| Loop | One tick (bounded) | Advisory lock key |
|---|---|---|
| `intent_consumer` | `EnqueueIntentDispatcher.dispatch_batch(limit=AGENT_RUN_CONSUMER_BATCH_MAX, repair_expired=same)` | `prodavan.agent_run.intent_consumer` |
| `run_worker` | `run_worker_once(worker, max_ticks=AGENT_RUN_WORKER_BATCH_MAX)` — claim → execute → settle | `prodavan.agent_run.run_worker` |

Both loops wait on the interval through `asyncio.wait_for(stop.wait(), timeout=…)`,
exactly like `application/agent/trigger_worker.py`, so shutdown is immediate even
with a one-hour interval.

## Required coverage

| Requirement | Test file | Cases |
|---|---|---|
| disabled by default (startup without loops) | `test_disabled_by_default.py` | `test_settings_default_is_disabled`, `test_default_provider_assembles_nothing`, `test_disabled_startup_starts_no_loop_and_opens_no_session`, `test_disabled_resource_does_not_resolve_dependencies`, `test_shutdown_before_startup_is_a_no_op`, `test_default_construction_is_inert` |
| graceful shutdown | `test_graceful_shutdown.py` | `test_shutdown_returns_promptly_despite_a_long_interval`, `test_shutdown_drains_the_worker_lease`, `test_shutdown_without_any_loop_does_not_call_the_worker`, `test_startup_shutdown_startup_is_idempotent`, `test_shutdown_tolerates_a_loop_task_that_never_finishes` |
| tick-exception resilience | `test_tick_resilience.py` | `test_consumer_tick_exception_does_not_kill_the_loop`, `test_worker_tick_exception_does_not_kill_the_loop`, `test_empty_round_is_idle_not_an_error`, `test_tick_failure_reason_is_exposed_in_the_report`, `test_worker_round_counts_terminal_transitions`, `test_consumer_round_is_bounded_and_passes_the_owner`, `test_both_loops_use_their_own_advisory_lock_key` |
| second loop refused on a held advisory lock | `test_advisory_lock_refusal.py` | `test_consumer_refuses_when_another_pod_holds_the_lock`, `test_worker_refuses_when_another_pod_holds_the_lock`, `test_lock_is_released_after_a_successful_round`, `test_lock_held_loop_keeps_ticking_instead_of_stopping`, `test_paused_pod_takes_over_when_the_lock_opens`, `test_consumer_and_worker_have_distinct_lock_keys`, `test_missing_counters_are_zero_not_fabricated` |
| no secrets in logs | `test_no_secrets_in_logs.py` | `test_failing_tick_logs_the_class_and_withholds_the_message`, `test_report_and_health_never_carry_a_tick_payload`, `test_successful_tick_logs_counters_only`, `test_no_log_call_formats_an_exception_message`, `test_settings_surface_holds_no_endpoint_or_secret_field`, `test_reason_codes_are_constants_not_free_text`, `test_blocker_log_line_is_a_code_only` |
| fail-closed dependencies / composition | `test_disabled_by_default.py` | `test_enabled_flag_without_adapter_idles_and_stays_healthy`, `test_partial_dependencies_block_only_the_incomplete_loop`, `test_empty_bundle_counts_as_unwired`, `test_partial_worker_bundle_reports_the_missing_member`, `test_worker_constructor_refusal_is_absorbed`, `test_dependency_provider_failure_degrades_to_blocked`, `test_dispatcher_composition_registers_the_run_created_handler`, `test_dispatcher_is_not_composed_without_a_port`, `test_consumer_lease_covers_the_whole_claim_batch` |
| health / state visibility | `test_health_report.py` | `test_disabled_health_is_not_applicable`, `test_enabled_but_not_started_health_is_false`, `test_serving_loop_reports_last_tick_and_counters`, `test_owner_id_is_stable_per_loop_and_instance`, `test_blocked_loop_is_healthy_and_explains_itself`, `test_report_snapshot_is_a_copy_not_the_live_state`, `test_resource_declares_the_lifespan_contract`, `test_loop_names_are_distinct_and_used_by_the_report` |
| settings are additive and inert | `test_settings_and_registration.py` | field/default/alias parity, clamping of intervals and batch sizes, garbage values fall back, no URL/token/secret field, registration after `DatabaseEngineResource` and before the sampler/worker tail, import and registration appear once, no duplicate name |

## Health semantics

`LifespanResource.health()` keeps its declared `bool | None` contract so
`LifespanManager.health_report()` stays correctly typed; the "meaningful state"
requirement (last tick, counters) is served by `AgentRunWorkerResource.report()`.

| Situation | `health()` | `report()` |
|---|---|---|
| both flags off (default) | `None` (not applicable) | `enabled=False`, `started=False` |
| enabled, startup not run / already stopped | `False` | `started=False` |
| enabled, loop blocked on a missing dependency | `True` (**by design**) | `blocked=True`, `blockers=[…]`, `running=True` |
| enabled, loop serving | `True` | `running=True`, `last_tick_at`, `ticks`, `errors`, `last_result` counters |

A blocked loop is reported healthy on purpose: failing readiness for every
deployment that enables a flag before its adapter lands is the exact failure this
task forbids. The blocker code is data, and it is what an operator needs.

## Reasons and blockers (closed vocabulary)

Reasons: `ok`, `idle`, `skipped`, `advisory_lock_held`, `tick_failed`.
Blockers: `agent_run_host_dependencies_unwired`,
`agent_run_execution_port_unwired`, `agent_run_run_input_source_unwired`,
`agent_run_provider_attempt_journal_unwired`, `agent_run_turn_runner_unwired`,
`agent_run_host_composition_failed`.

## Design decisions a reviewer should judge

1. **Two advisory-lock keys, not one.** The consumer and the run worker are
   different pipelines; serialising them on one key would make a slow provider
   turn block intent consumption. `prodavan.agent_run.intent_consumer` and
   `prodavan.agent_run.run_worker` are static module constants (never user input —
   `advisory_lock` interpolates the key into SQL).
2. **Idle is not "claim and retry".** Claiming intents without an execution
   adapter would advance real handoff rows through `intent_handler_not_wired`
   retries into the poison-pill refusal. The host therefore refuses to open a
   session at all while a dependency is missing.
3. **Lease sized from the batch.** `SqlAlchemyIntentConsumer.claim` stamps one
   `lease_until` on every row of a batch, and an expired `dispatching` row is
   quarantined rather than retried. `_consumer_lease_seconds()` therefore gives
   `min(900, 30 + 3 × batch)` seconds so a full batch can finish inside its own
   lease (25 → 105 s). The dispatcher's retry budget matches the existing API
   composition (`DISPATCH_MAX_ATTEMPTS = 288`).
4. **Drain before cancel.** Shutdown sets the stop event, lets
   `AgentRunWorker.shutdown` give the in-flight turn its grace window (20 s), and
   only then awaits/cancels the loop task (30 s ceiling). Cancelling first would
   cut a turn off mid-flight and leave the run to its lease TTL.
5. **`health()` stays a bool.** The base contract and
   `LifespanManager.health_report()` are typed `bool | None`; the "last tick /
   counters" requirement is met by `report()`, which is what an admin or a support
   transcript actually reads.

## Deliberately out of scope (blockers for the parent)

1. **No execution adapter exists yet.** `AgentRunExecutionPort` has no
   implementation in `apps/api/src`, so the default dependency provider returns
   an empty bundle, both loops idle, and no intent is claimed. This is the
   fail-closed state the migration wave plan already names ("Execution adapter +
   lease store — in progress"). The seam is
   `register_host_dependency_provider()`, not a fallback: claiming intents
   without a handler would walk real handoff rows to the poison-pill refusal and
   lose work silently.
2. **The duplicate-queue-name note.** `INTENT_QUEUE_NAME` is repeated in this
   module instead of imported from
   `prodavan.api.migration_agent_runs.dependencies` because `core` must not
   depend on the API layer. Both must keep the value `agent-control-plane`; the
   writer (`DurableAgentControlPlane`) defaults to it too.
3. **`agent_run_provider_attempt_journal_unwired`.** Even with a port, the run
   worker refuses to start without a durable `ProviderAttemptJournal`; that is
   the journal slice's blocker, not this task's.
