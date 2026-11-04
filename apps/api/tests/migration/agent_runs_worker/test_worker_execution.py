"""Durable Agent Run worker acceptance: claim → execute → terminal transition.

Every test here is an *acceptance vector* from ``acceptance.md``.  Authored
during the migration verification freeze — not executed.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from prodavan.application.agent_runs.attempts import AttemptState, ProviderAttemptJournal
from prodavan.application.agent_runs.execution import ProviderExecutionResult, ProviderOutcome
from prodavan.application.agent_runs.lease import LeasePolicy
from prodavan.application.agent_runs.worker import (
    AgentRunWorker,
    RunInputInvalid,
    SettlementKind,
    WorkerConfig,
    WorkerConfigError,
    WorkerRetryPolicy,
    WorkerTick,
    WorkerTickStatus,
    WorkerUnavailable,
    plan_settlement,
    parse_run_input,
)
from prodavan.domain.agent.control_plane import AgentRunStatus

from .fakes import (
    LEASE_TTL,
    NOW,
    RUN_ID,
    AdvancingClock,
    ExpiringPort,
    FakeAttemptPort,
    FakeClock,
    FakeExecutionPort,
    FakePortWithoutRelease,
    FakeRunStore,
    ScriptedRunner,
    failed,
    make_lease,
    retryable,
    success,
    unknown,
)


def build_worker(
    *,
    port=None,
    runner=None,
    attempts=None,
    config=None,
    port_cls=None,
    clock=None,
):
    clock = clock or FakeClock()
    port = port or (port_cls or FakeExecutionPort)(clock=clock)
    store = FakeRunStore(port.row)
    attempts = attempts or FakeAttemptPort()
    worker = AgentRunWorker(
        port=port,
        runs=store,
        journal=ProviderAttemptJournal(port=attempts),
        runner=runner or ScriptedRunner(),
        config=config
        or WorkerConfig(
            owner_id="worker-a",
            lease_policy=LeasePolicy(ttl=LEASE_TTL),
            heartbeat_interval=timedelta(seconds=10),
            poll_interval=timedelta(seconds=1),
            shutdown_grace=timedelta(seconds=1),
        ),
        clock=clock,
    )
    return worker, port, clock, attempts, store


# ---------------------------------------------------------------------------
# Happy path and eligibility
# ---------------------------------------------------------------------------


async def test_successful_tick_writes_exactly_one_terminal_transition() -> None:
    worker, port, _clock, attempts, _store = build_worker()
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.SUCCEEDED
    assert tick.wrote_run_state
    assert port.row.status == AgentRunStatus.SUCCEEDED.value
    assert port.row.generation == 1
    assert len(port.finish_calls) == 1
    assert port.unknown_calls == []
    assert port.retry_calls == []
    assert attempts.start_states == [AttemptState.KNOWN_SUCCEEDED]


async def test_idle_when_no_run_is_eligible() -> None:
    port = FakeExecutionPort(clock=FakeClock())
    port.row.status = AgentRunStatus.SUCCEEDED.value
    worker, _port, _clock, attempts, _store = build_worker(port=port)
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.IDLE
    assert not tick.claimed
    assert port.finish_calls == []
    assert attempts.records == {}


async def test_worker_refuses_to_start_without_a_durable_port() -> None:
    with pytest.raises(WorkerUnavailable) as unavailable:

        class _Missing:
            async def claim_next(self, **kwargs):
                return None

        AgentRunWorker(
            port=_Missing(),
            runs=FakeRunStore(FakeExecutionPort().row),
            journal=ProviderAttemptJournal(port=FakeAttemptPort()),
            runner=ScriptedRunner(),
            config=WorkerConfig(owner_id="worker-a"),
        )
    assert unavailable.value.blocker == "agent_run_durable_claim_and_lease_unwired"


def test_config_requires_lease_ttl_beyond_two_heartbeats() -> None:
    with pytest.raises(WorkerConfigError):
        WorkerConfig(
            owner_id="worker-a",
            lease_policy=LeasePolicy(ttl=timedelta(seconds=10)),
            heartbeat_interval=timedelta(seconds=5),
        )


# ---------------------------------------------------------------------------
# Duplicate claim / restart
# ---------------------------------------------------------------------------


async def test_a_second_claim_of_a_leased_run_is_refused() -> None:
    worker, port, _clock, _attempts, _store = build_worker()
    first = await worker.tick()
    assert first.status is WorkerTickStatus.SUCCEEDED
    # Put the run back in the queue while a lease is still held: the port must
    # refuse to hand it out twice.
    port.row.status = AgentRunStatus.QUEUED.value
    port.row.lease_owner = "worker-a"
    port.row.lease_token = "token-generation-1"
    port.row.lease_until = NOW + LEASE_TTL
    port.row.available_at = None
    second = await worker.tick()
    assert second.status is WorkerTickStatus.IDLE


async def test_restart_does_not_reset_the_generation() -> None:
    worker, port, clock, _attempts, _store = build_worker()
    await worker.tick()
    assert port.row.generation == 1

    # Simulate a process restart: a brand new worker instance over the same
    # durable row re-claims a requeued run.
    port.row.status = AgentRunStatus.QUEUED.value
    port.row.available_at = None
    restarted, _port, _clock2, _attempts2, _store2 = build_worker(
        port=port, config=WorkerConfig(owner_id="worker-b", lease_policy=LeasePolicy(ttl=LEASE_TTL))
    )
    tick = await restarted.tick()
    assert tick.status is WorkerTickStatus.SUCCEEDED
    assert tick.generation == 2
    assert port.row.generation == 2


# ---------------------------------------------------------------------------
# Stale generation -> 0 rows CAS and refusal
# ---------------------------------------------------------------------------


async def test_stale_generation_cannot_write_and_is_recorded_as_discarded() -> None:
    """A cancel during execution bumps the generation; the old worker loses."""

    async def cancel_midflight(request, cancel):
        port.cancel()  # another actor cancels: generation += 1
        cancel.cancel("cancelled_by_operator")

    port = FakeExecutionPort(clock=FakeClock())
    worker, port, _clock, attempts, _store = build_worker(
        port=port, runner=ScriptedRunner(on_execute=cancel_midflight)
    )
    tick = await worker.tick()

    assert tick.status is WorkerTickStatus.DISCARDED_STALE
    assert not tick.wrote_run_state
    assert port.row.status == AgentRunStatus.CANCELLED.value
    assert port.finish_calls == []
    assert port.unknown_calls == []
    assert port.retry_calls == []
    # The attempt journal keeps the evidence, including why it was discarded.
    stored = next(iter(attempts.records.values()))
    assert stored.state is AttemptState.DISCARDED_STALE
    assert stored.failure_code == "agent_run.lease_lost"


async def test_superseded_worker_does_not_record_unknown_either() -> None:
    async def steal_midflight(request, cancel):
        port.steal()
        cancel.cancel("lease_taken")

    port = FakeExecutionPort(clock=FakeClock())
    worker, port, _clock, attempts, _store = build_worker(
        port=port,
        runner=ScriptedRunner(unknown(), on_execute=steal_midflight),
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.DISCARDED_STALE
    assert port.unknown_calls == []
    assert port.row.status == AgentRunStatus.RUNNING.value  # worker-b owns it


# ---------------------------------------------------------------------------
# Heartbeat / expiry
# ---------------------------------------------------------------------------


async def test_heartbeat_after_expiry_fences_the_worker_out() -> None:
    clock = FakeClock()
    port = ExpiringPort(clock=clock)
    worker, port, clock, attempts, _store = build_worker(port=port)

    # ``ScriptedRunner`` *awaits* its ``on_execute`` hook, so the hook must be a
    # coroutine function.  A plain lambda returning ``clock.advance(...)``'s
    # datetime raised ``TypeError`` inside the runner, and the worker (correctly)
    # turned that into an unknown outcome — the lease-expiry path under test was
    # never reached.
    async def expire_the_lease_during_the_turn(request, cancel) -> None:
        clock.advance(LEASE_TTL + timedelta(seconds=1))

    # The turn runs long enough for the lease to expire while it works.
    worker_runner = ScriptedRunner(success(), on_execute=expire_the_lease_during_the_turn)
    worker._runner = worker_runner

    tick = await worker.tick()

    # The settlement itself has to ask the lease store: the claim predates the
    # turn, and by now the deadline is gone.
    assert port.heartbeat_results == [False]
    assert tick.status is WorkerTickStatus.DISCARDED_STALE
    assert not tick.wrote_run_state
    assert port.finish_calls == []


async def test_heartbeat_that_raises_fences_the_worker_out() -> None:
    class RaisingPort(FakeExecutionPort):
        async def heartbeat(self, *, lease, now, lease_until):
            raise RuntimeError("database connection lost")

    port = RaisingPort(clock=FakeClock())

    async def slow(request, cancel):
        await asyncio.sleep(0)  # let the heartbeat task start
        await asyncio.sleep(0.15)

    worker, port, _clock, _attempts, _store = build_worker(
        port=port, runner=ScriptedRunner(success(), on_execute=slow)
    )
    worker._config = WorkerConfig(
        owner_id="worker-a",
        lease_policy=LeasePolicy(ttl=LEASE_TTL),
        heartbeat_interval=timedelta(seconds=0.05),
        poll_interval=timedelta(seconds=1),
        shutdown_grace=timedelta(seconds=1),
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.DISCARDED_STALE
    assert port.finish_calls == []


async def test_confirmed_heartbeats_keep_a_long_turn_live() -> None:
    clock = FakeClock()
    port = FakeExecutionPort(clock=clock)

    async def slow_but_beating(request, cancel):
        await asyncio.sleep(0.12)

    worker, port, clock, _attempts, _store = build_worker(
        port=port, runner=ScriptedRunner(success(), on_execute=slow_but_beating)
    )
    worker._config = WorkerConfig(
        owner_id="worker-a",
        lease_policy=LeasePolicy(ttl=LEASE_TTL),
        heartbeat_interval=timedelta(seconds=5),
        poll_interval=timedelta(seconds=1),
        shutdown_grace=timedelta(seconds=1),
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.SUCCEEDED
    assert port.row.status == AgentRunStatus.SUCCEEDED.value


# ---------------------------------------------------------------------------
# Cancellation during execution
# ---------------------------------------------------------------------------


async def test_cancellation_during_execution_cancels_the_turn_and_discards_result() -> None:
    port = FakeExecutionPort(clock=FakeClock())
    seen: dict[str, object] = {}

    async def cancel_then_finish(request, cancel):
        seen["cancelled_initially"] = cancel.cancelled

    runner = ScriptedRunner(wait_for_cancel=True, on_execute=cancel_then_finish)
    worker, port, _clock, attempts, _store = build_worker(port=port, runner=runner)
    worker._config = WorkerConfig(
        owner_id="worker-a",
        lease_policy=LeasePolicy(ttl=LEASE_TTL),
        heartbeat_interval=timedelta(seconds=0.05),
        poll_interval=timedelta(seconds=1),
        shutdown_grace=timedelta(seconds=1),
    )

    async def cancel_soon() -> None:
        await asyncio.sleep(0.02)
        port.cancel()  # generation += 1, run is cancelled
        await asyncio.sleep(0.2)  # let the next heartbeat observe the new generation

    tick_task = asyncio.create_task(worker.tick())
    await cancel_soon()
    tick = await tick_task

    assert seen["cancelled_initially"] is False
    assert tick.status is WorkerTickStatus.DISCARDED_STALE
    assert port.finish_calls == []
    assert port.row.status == AgentRunStatus.CANCELLED.value


async def test_cancelled_turn_reports_unknown_not_success() -> None:
    """If the lease is still ours, a cancelled provider call is parked, not succeeded."""
    # The worker measures its shutdown grace window on the injected clock, so the
    # clock has to move with wall time here; a frozen FakeClock keeps the drain
    # loop inside `while ... self._now() < deadline` for ever and the test hangs
    # instead of settling the turn.
    clock = AdvancingClock()
    # "Still ours" is only true when the drain cannot hand the lease back: the
    # production adapter has no ``release_lease`` extension, so the lease stays
    # with this worker and the cancelled turn is parked. A port that *does*
    # release would return the run to the queue before the terminal write and the
    # worker would (correctly) report a lost CAS instead — that path is covered by
    # ``test_shutdown_with_release_support_returns_the_run_to_the_queue``.
    port = FakePortWithoutRelease(clock=clock)
    runner = ScriptedRunner(wait_for_cancel=True)
    worker, port, _clock, attempts, _store = build_worker(port=port, runner=runner, clock=clock)
    worker._config = WorkerConfig(
        owner_id="worker-a",
        lease_policy=LeasePolicy(ttl=LEASE_TTL),
        # Production-shaped cadence: the 60 s lease outlives the 1 s shutdown
        # grace, which is the situation under test — a cancelled turn whose lease
        # the worker still holds. A sub-second heartbeat interval would instead
        # measure fence freshness against a beat that the drain has already
        # stopped, turning the assertion into a race on scheduler jitter.
        heartbeat_interval=timedelta(seconds=10),
        poll_interval=timedelta(seconds=1),
        shutdown_grace=timedelta(seconds=1),
    )

    async def shutdown() -> None:
        await asyncio.sleep(0.02)
        stop = asyncio.Event()
        await worker.shutdown(stop=stop)

    tick_task = asyncio.create_task(worker.tick())
    await shutdown()
    tick = await tick_task
    assert tick.status is WorkerTickStatus.UNKNOWN_RECORDED
    assert port.row.status == AgentRunStatus.FAILED.value
    assert port.finish_calls == []
    stored = next(iter(attempts.records.values()))
    assert stored.state is AttemptState.UNKNOWN


# ---------------------------------------------------------------------------
# Unknown outcome
# ---------------------------------------------------------------------------


async def test_unknown_outcome_is_never_marked_success() -> None:
    worker, port, _clock, attempts, _store = build_worker(
        runner=ScriptedRunner(unknown())
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.UNKNOWN_RECORDED
    assert tick.failure_code == "agent_run.provider_outcome_unknown"
    assert port.row.status != AgentRunStatus.SUCCEEDED.value
    assert port.finish_calls == []
    assert port.retry_calls == []  # never a blind retry
    assert port.unknown_calls == ["req-unknown"]
    stored = next(iter(attempts.records.values()))
    assert stored.state is AttemptState.UNKNOWN


async def test_runner_exception_after_dispatch_is_unknown() -> None:
    worker, port, _clock, attempts, _store = build_worker(
        runner=ScriptedRunner(raise_exc=RuntimeError("bridge died mid-stream"))
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.UNKNOWN_RECORDED
    assert port.row.status == AgentRunStatus.FAILED.value
    assert port.finish_calls == []


async def test_unresolved_prior_attempt_blocks_a_new_provider_call() -> None:
    attempts = FakeAttemptPort()
    attempts.seed_unresolved(generation=1, attempt=1, state=AttemptState.STARTED)
    worker, port, _clock, attempts, _store = build_worker(attempts=attempts)
    runner = ScriptedRunner(success())
    worker._runner = runner

    tick = await worker.tick()

    assert tick.status is WorkerTickStatus.UNKNOWN_RECORDED
    assert runner.calls == 0  # the provider was never called again
    assert port.unknown_calls == ["req-prior"]


# ---------------------------------------------------------------------------
# Retry scheduling
# ---------------------------------------------------------------------------


async def test_known_retryable_failure_schedules_a_durable_retry() -> None:
    retry_at = NOW + timedelta(seconds=10)
    worker, port, _clock, attempts, _store = build_worker(
        runner=ScriptedRunner(retryable(retry_at=retry_at))
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.RETRY_SCHEDULED
    assert port.retry_calls == [retry_at]
    assert port.row.status == "retry_due"
    assert port.finish_calls == []
    stored = next(iter(attempts.records.values()))
    assert stored.state is AttemptState.KNOWN_RETRYABLE


async def test_retry_is_refused_once_the_policy_is_exhausted() -> None:
    retry_at = NOW + timedelta(minutes=30)
    worker, port, _clock, attempts, _store = build_worker(
        runner=ScriptedRunner(retryable(retry_at=retry_at)),
        config=WorkerConfig(
            owner_id="worker-a",
            lease_policy=LeasePolicy(ttl=LEASE_TTL),
            retry_policy=WorkerRetryPolicy(max_attempts=1),
        ),
    )
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.FAILED
    assert tick.failure_code == "agent_run.retry_exhausted"
    assert port.retry_calls == []


def test_unknown_result_cannot_carry_a_retry_time() -> None:
    with pytest.raises(ValueError):
        ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN, retry_at=NOW + timedelta(seconds=5))


# ---------------------------------------------------------------------------
# Success requires output
# ---------------------------------------------------------------------------


async def test_success_without_output_is_a_failure_not_a_success() -> None:
    empty = ProviderExecutionResult(outcome=ProviderOutcome.SUCCEEDED, output_payload={})
    worker, port, _clock, _attempts, _store = build_worker(runner=ScriptedRunner(empty))
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.FAILED
    assert tick.failure_code == "agent_run.empty_result"
    assert port.row.status == AgentRunStatus.FAILED.value


async def test_known_failure_uses_the_provider_failure_code() -> None:
    worker, port, _clock, _attempts, _store = build_worker(runner=ScriptedRunner(failed()))
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.FAILED
    assert tick.failure_code == "agent_error.AGENT_INVALID_MODEL"
    assert port.finish_calls[0].outcome is ProviderOutcome.FAILED


# ---------------------------------------------------------------------------
# Settlement planning (pure)
# ---------------------------------------------------------------------------


def test_settlement_planner_never_turns_unknown_into_success() -> None:
    settlement = plan_settlement(
        unknown(), now=NOW, attempt=1, retry_policy=WorkerRetryPolicy(), fence_ok=True
    )
    assert settlement.kind is SettlementKind.UNKNOWN
    assert settlement.output_payload is None


def test_settlement_planner_discards_after_a_lost_fence() -> None:
    settlement = plan_settlement(
        success(), now=NOW, attempt=1, retry_policy=WorkerRetryPolicy(), fence_ok=False
    )
    assert settlement.kind is SettlementKind.DISCARD
    assert not settlement.writes_run_state


# ---------------------------------------------------------------------------
# Invalid input / missing run
# ---------------------------------------------------------------------------


def test_run_input_requires_prompt_text() -> None:
    with pytest.raises(RunInputInvalid):
        parse_run_input({})
    with pytest.raises(RunInputInvalid):
        parse_run_input(None)
    with pytest.raises(RunInputInvalid):
        parse_run_input({"text": "   "})
    with pytest.raises(RunInputInvalid):
        parse_run_input({"text": "ok", "attachment_refs": [1, 2]})
    parsed = parse_run_input({"text": " hello ", "attachment_refs": ["a"], "model": "m"})
    assert parsed.text == "hello"
    assert parsed.attachment_refs == ("a",)


async def test_missing_prompt_fails_the_run_without_calling_the_provider() -> None:
    port = FakeExecutionPort(clock=FakeClock())
    port.row.input_payload = {"attachment_refs": []}
    worker, port, _clock, attempts, _store = build_worker(port=port)
    runner = ScriptedRunner(success())
    worker._runner = runner
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.FAILED
    assert tick.failure_code == "agent_run.input_invalid"
    assert runner.calls == 0
    assert attempts.records == {}  # no provider attempt was ever journaled


async def test_run_invisible_in_tenant_scope_writes_nothing() -> None:
    worker, port, _clock, attempts, store = build_worker()
    store.hidden = True
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.REFUSED
    assert not tick.wrote_run_state
    assert port.finish_calls == []
    assert port.unknown_calls == []
    assert attempts.records == {}


async def test_run_without_session_writes_nothing_to_the_provider() -> None:
    port = FakeExecutionPort(clock=FakeClock())
    port.row.session_id = None
    worker, port, _clock, attempts, _store = build_worker(port=port)
    runner = ScriptedRunner(success())
    worker._runner = runner
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.FAILED
    assert tick.failure_code == "agent_run.input_invalid"
    assert runner.calls == 0


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


async def test_shutdown_without_release_support_keeps_the_lease() -> None:
    port = FakePortWithoutRelease(clock=FakeClock())
    worker, port, _clock, _attempts, _store = build_worker(port=port)
    # "Already stopping" has to be said *to the worker*: a local event the worker
    # never receives does not stop it, and the previous version of this test set
    # exactly such a detached event while still asserting the drain behaviour.
    # ``shutdown`` records the signal, so the next claim is handed straight back.
    stop = asyncio.Event()
    await worker.shutdown(stop=stop)
    assert stop.is_set()
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.RELEASED_ON_SHUTDOWN
    assert not tick.wrote_run_state
    assert port.release_calls == []
    # The lease is intact, so no other worker can double-execute immediately.
    assert port.row.lease_owner == "worker-a"


async def test_shutdown_with_release_support_returns_the_run_to_the_queue() -> None:
    port = FakeExecutionPort(clock=FakeClock())
    worker, port, _clock, _attempts, _store = build_worker(port=port)
    stop = asyncio.Event()
    await worker.shutdown(stop=stop)
    tick = await worker.tick()
    assert tick.status is WorkerTickStatus.RELEASED_ON_SHUTDOWN
    assert port.release_calls == [RUN_ID]
    assert port.row.status == AgentRunStatus.QUEUED.value
    assert port.row.generation == 1  # release never resets the generation


async def test_run_forever_drains_and_stops() -> None:
    worker, port, _clock, _attempts, _store = build_worker()
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(worker.run_forever(stop=stop), stopper())
    assert port.row.status == AgentRunStatus.SUCCEEDED.value


def test_tick_is_immutable_and_writes_nothing_on_idle() -> None:
    idle = WorkerTick(WorkerTickStatus.IDLE)
    assert not idle.claimed and not idle.wrote_run_state
