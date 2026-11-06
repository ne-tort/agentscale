"""Tick resilience (MIG-AR-HOST-01).

A background loop that dies on its first exception is worse than no loop: the
queue silently stops being consumed and nothing in the process knows.  These
checks pin the ``trigger_worker.py`` discipline — an exception inside a tick is
counted, logged by class name, and the loop keeps going; the next tick runs on
the normal cadence.
"""

from __future__ import annotations

import pytest

from prodavan.application.agent_runs.dispatcher import DispatchBatchReport
from prodavan.core.infra.agent_run_worker_resource import (
    CONSUMER_LOCK_KEY,
    LOOP_CONSUMER,
    LOOP_WORKER,
    REASON_TICK_FAILED,
    WORKER_LOCK_KEY,
    AgentRunWorkerResource,
    current_host_dependency_provider,
    register_host_dependency_provider,
)

from .acceptance_fixtures import (
    SECRET_MARKER,
    FakeDispatcher,
    FakeLock,
    FakeSessionFactory,
    FakeWorker,
    enabled_config,
    succeeded_tick,
    wait_until,
    wired_dependencies,
)


@pytest.fixture(autouse=True)
def _restore_provider() -> None:
    previous = current_host_dependency_provider()
    yield
    register_host_dependency_provider(previous)


def _resource(*, dispatcher, worker, lock=None, interval=0.05) -> AgentRunWorkerResource:
    return AgentRunWorkerResource(
        config=enabled_config(
            worker_interval_sec=interval,
            consumer_interval_sec=interval,
            worker_batch_max=2,
            consumer_batch_max=7,
        ),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher,
        run_worker=worker,
        session_factory=FakeSessionFactory(),
        lock=lock or FakeLock(),
    )


@pytest.mark.asyncio
async def test_consumer_tick_exception_does_not_kill_the_loop() -> None:
    """Three failing ticks, then a good one — all inside the same loop task."""
    dispatcher = FakeDispatcher(
        report=DispatchBatchReport(claimed=1, delivered=1),
        error=RuntimeError(f"ledger unavailable {SECRET_MARKER}"),
        error_times=3,
    )
    resource = _resource(dispatcher=dispatcher, worker=FakeWorker())

    await resource.startup()
    # Wait for the *recovered* state, not just for the fourth dispatch call: the
    # dispatcher records its call before it raises, while the loop publishes
    # ``last_reason``/``last_result`` after the tick returns.  Waiting only on the
    # call count sampled the snapshot between the two and observed the third
    # tick's failure as if it were the last one.
    #
    # The budget is wall-clock, not a claim about speed: four ticks at a short
    # interval are instant on an idle machine but a full-suite run shares the CPU
    # with every other test, and three seconds was tight enough to flake only when
    # the whole suite ran. The state being waited for is unchanged, and the
    # assertions below still require exactly the recovered shape.
    recovered = await wait_until(
        lambda: len(dispatcher.calls) >= 4
        and resource.report()["loops"][LOOP_CONSUMER]["last_reason"] == "ok",
        timeout=20.0,
    )
    assert recovered
    snapshot = resource.report()["loops"][LOOP_CONSUMER]

    assert snapshot["running"] is True
    assert snapshot["ticks"] >= 4
    assert snapshot["errors"] == 3
    assert snapshot["last_error_class"] == "RuntimeError"
    # After the good tick the loop reports real counters again.
    assert snapshot["last_reason"] == "ok"
    assert snapshot["last_result"]["delivered"] == 1

    await resource.shutdown()


@pytest.mark.asyncio
async def test_worker_tick_exception_does_not_kill_the_loop() -> None:
    worker = FakeWorker(error=LookupError("claim refused"), error_times=2)
    resource = _resource(dispatcher=FakeDispatcher(), worker=worker)

    await resource.startup()
    assert await wait_until(lambda: worker.calls >= 3, timeout=20.0)
    snapshot = resource.report()["loops"][LOOP_WORKER]

    assert snapshot["running"] is True
    assert snapshot["errors"] == 2
    assert snapshot["last_error_class"] == "LookupError"
    await resource.shutdown()


@pytest.mark.asyncio
async def test_empty_round_is_idle_not_an_error() -> None:
    """``AgentRunWorker`` returns an ``idle`` tick when nothing is claimable."""
    resource = _resource(dispatcher=FakeDispatcher(), worker=FakeWorker(ticks=[]))
    await resource.startup()
    assert await wait_until(
        lambda: resource.report()["loops"][LOOP_WORKER]["ticks"] >= 1, timeout=20.0
    )
    snapshot = resource.report()["loops"][LOOP_WORKER]
    assert snapshot["errors"] == 0
    assert snapshot["last_result"]["ticks"] == 1
    assert snapshot["last_result"]["statuses"] == {"idle": 1}
    assert snapshot["last_result"]["claimed"] == 0
    await resource.shutdown()


@pytest.mark.asyncio
async def test_worker_round_counts_terminal_transitions() -> None:
    worker = FakeWorker(
        ticks=[
            succeeded_tick(run_id="run-a"),
            succeeded_tick(run_id="run-b"),
            succeeded_tick(run_id="run-c"),
        ]
    )
    resource = _resource(dispatcher=FakeDispatcher(), worker=worker)
    result = await resource.run_worker_round_once()

    assert result["skipped"] is False
    assert result["ticks"] == 2  # bounded by AGENT_RUN_WORKER_BATCH_MAX
    assert result["claimed"] == 2
    assert result["wrote_run_state"] == 2
    assert result["statuses"] == {"succeeded": 2}


@pytest.mark.asyncio
async def test_consumer_round_is_bounded_and_passes_the_owner() -> None:
    dispatcher = FakeDispatcher(report=DispatchBatchReport(claimed=7, delivered=7))
    resource = _resource(dispatcher=dispatcher, worker=FakeWorker())
    result = await resource.drain_consumer_once()

    assert result == {
        "skipped": False,
        "reason": "ok",
        "claimed": 7,
        "delivered": 7,
        "retried": 0,
        "rejected": 0,
        "quarantined": 0,
        "lease_lost": 0,
        "repaired": 0,
    }
    call = dispatcher.calls[0]
    assert call["limit"] == 7  # AGENT_RUN_CONSUMER_BATCH_MAX
    assert call["repair_expired"] == 7
    assert call["owner_id"].startswith("agent-run-host:")
    assert call["owner_id"].endswith(f":{LOOP_CONSUMER}")


@pytest.mark.asyncio
async def test_both_loops_use_their_own_advisory_lock_key() -> None:
    """Two different pipelines must not serialise on one key."""
    lock = FakeLock()
    dispatcher = FakeDispatcher()
    worker = FakeWorker()
    resource = _resource(dispatcher=dispatcher, worker=worker, lock=lock)

    await resource.drain_consumer_once()
    await resource.run_worker_round_once()

    assert lock.keys == [CONSUMER_LOCK_KEY, WORKER_LOCK_KEY]
    assert CONSUMER_LOCK_KEY != WORKER_LOCK_KEY


@pytest.mark.asyncio
async def test_tick_failure_reason_is_exposed_in_the_report() -> None:
    dispatcher = FakeDispatcher(error=TimeoutError("tik"), error_times=1)
    resource = _resource(dispatcher=dispatcher, worker=FakeWorker())
    await resource.startup()
    assert await wait_until(
        lambda: resource.report()["loops"][LOOP_CONSUMER]["errors"] >= 1, timeout=20.0
    )
    snapshot = resource.report()["loops"][LOOP_CONSUMER]
    assert snapshot["last_reason"] == REASON_TICK_FAILED
    assert snapshot["last_result"] == {}
    await resource.shutdown()
