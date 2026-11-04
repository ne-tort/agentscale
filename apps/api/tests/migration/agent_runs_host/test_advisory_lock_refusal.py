"""Advisory-lock refusal (MIG-AR-HOST-01).

Two pods run the same lifespan.  The durable fences (``SKIP LOCKED`` claim,
per-run ``lease_generation``) already make a wrong winner harmless, but they do
not stop two pods from *trying*: double repair rounds, double sweeps and two
workers interleaving claim/execute cycles.  The process advisory lock is what
makes the second pod stand down, and this file pins that it stands down
completely — no claim, no write, not even a call into the dispatcher.
"""

from __future__ import annotations

import pytest

from prodavan.core.infra.agent_run_worker_resource import (
    CONSUMER_LOCK_KEY,
    LOOP_CONSUMER,
    REASON_LOCK_HELD,
    WORKER_LOCK_KEY,
    AgentRunWorkerResource,
    current_host_dependency_provider,
    register_host_dependency_provider,
)

from .acceptance_fixtures import (
    FakeDispatcher,
    FakeLock,
    FakeSessionFactory,
    FakeWorker,
    enabled_config,
    wait_until,
    wired_dependencies,
)


@pytest.fixture(autouse=True)
def _restore_provider() -> None:
    previous = current_host_dependency_provider()
    yield
    register_host_dependency_provider(previous)


def _resource(*, lock: FakeLock, dispatcher=None, worker=None, interval: float = 0.05):
    return AgentRunWorkerResource(
        config=enabled_config(
            worker_interval_sec=interval, consumer_interval_sec=interval
        ),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher or FakeDispatcher(),
        run_worker=worker or FakeWorker(),
        session_factory=FakeSessionFactory(),
        lock=lock,
    )


@pytest.mark.asyncio
async def test_consumer_refuses_when_another_pod_holds_the_lock() -> None:
    lock = FakeLock(held=False)
    dispatcher = FakeDispatcher()
    resource = _resource(lock=lock, dispatcher=dispatcher)

    result = await resource.drain_consumer_once()

    assert result["skipped"] is True
    assert result["reason"] == REASON_LOCK_HELD
    assert result["claimed"] == 0
    # The deciding point: the dispatcher was never entered, so no intent was
    # claimed and no delivery receipt could be reserved.
    assert dispatcher.calls == []
    assert lock.keys == ["prodavan.agent_run.intent_consumer"]


@pytest.mark.asyncio
async def test_worker_refuses_when_another_pod_holds_the_lock() -> None:
    lock = FakeLock(held=False)
    worker = FakeWorker()
    resource = _resource(lock=lock, worker=worker)

    result = await resource.run_worker_round_once()

    assert result["skipped"] is True
    assert result["reason"] == REASON_LOCK_HELD
    assert result["ticks"] == 0
    # No tick ran, so no run was claimed and no lease was taken.
    assert worker.calls == 0
    assert lock.keys == ["prodavan.agent_run.run_worker"]


@pytest.mark.asyncio
async def test_lock_is_released_after_a_successful_round() -> None:
    """The lock lives inside the session context, so it cannot leak past the tick."""
    lock = FakeLock(held=True)
    dispatcher = FakeDispatcher()
    resource = _resource(lock=lock, dispatcher=dispatcher)

    first = await resource.drain_consumer_once()
    second = await resource.drain_consumer_once()

    assert first["skipped"] is False
    assert second["skipped"] is False
    assert len(dispatcher.calls) == 2
    assert lock.keys == ["prodavan.agent_run.intent_consumer"] * 2


@pytest.mark.asyncio
async def test_lock_held_loop_keeps_ticking_instead_of_stopping() -> None:
    """Losing the race every tick is a normal steady state, not an error."""
    lock = FakeLock(held=False)
    dispatcher = FakeDispatcher()
    resource = _resource(lock=lock, dispatcher=dispatcher)

    await resource.startup()
    assert await wait_until(lambda: len(lock.keys) >= 3, timeout=3.0)
    snapshot = resource.report()["loops"][LOOP_CONSUMER]

    assert snapshot["running"] is True
    assert snapshot["errors"] == 0
    assert snapshot["last_reason"] == REASON_LOCK_HELD
    assert dispatcher.calls == []
    await resource.shutdown()


@pytest.mark.asyncio
async def test_paused_pod_takes_over_when_the_lock_opens() -> None:
    """The refusal is per-tick: the moment the other pod finishes, work resumes."""
    lock = FakeLock(held=False)
    dispatcher = FakeDispatcher()
    worker = FakeWorker()
    resource = _resource(lock=lock, dispatcher=dispatcher, worker=worker)

    assert (await resource.drain_consumer_once())["reason"] == REASON_LOCK_HELD
    lock.held = True
    assert (await resource.drain_consumer_once())["skipped"] is False
    assert len(dispatcher.calls) == 1


def test_consumer_and_worker_have_distinct_lock_keys() -> None:
    assert CONSUMER_LOCK_KEY == "prodavan.agent_run.intent_consumer"
    assert WORKER_LOCK_KEY == "prodavan.agent_run.run_worker"
    assert CONSUMER_LOCK_KEY != WORKER_LOCK_KEY
    resource = _resource(lock=FakeLock())
    assert resource.name == "agent_run_host"


@pytest.mark.asyncio
async def test_missing_counters_are_zero_not_fabricated() -> None:
    """A skipped round reports zeros; nothing is invented for an unrun tick."""
    lock = FakeLock(held=False)
    resource = _resource(lock=lock)
    result = await resource.run_worker_round_once()
    assert result["claimed"] == 0
    assert result["ticks"] == 0
    assert "statuses" not in result
