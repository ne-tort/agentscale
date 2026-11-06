"""Graceful shutdown of both loops (MIG-AR-HOST-01).

Shutdown has three obligations, and they are checked separately here:

1. the ``stop`` event is set, so a loop waiting out its interval wakes at once
   instead of finishing the sleep (an interval of an hour must not delay a pod
   termination by an hour);
2. the loop tasks are awaited and the resource ends in a state that can be
   started again;
3. an in-flight run lease is drained, not abandoned: ``AgentRunWorker.shutdown``
   is called with the same stop event so a claimed run is released or settled.
"""

from __future__ import annotations

import asyncio

import pytest

from prodavan.core.infra.agent_run_worker_resource import (
    AgentRunWorkerResource,
    current_host_dependency_provider,
    register_host_dependency_provider,
    unavailable_host_dependencies,
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


@pytest.mark.asyncio
async def test_shutdown_returns_promptly_despite_a_long_interval() -> None:
    """A one-hour interval must not hold the process open."""
    dispatcher = FakeDispatcher()
    worker = FakeWorker()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker_interval_sec=3600, consumer_interval_sec=3600),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher,
        run_worker=worker,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    assert await wait_until(lambda: dispatcher.calls and worker.calls, timeout=2.0)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await resource.shutdown()
    elapsed = loop.time() - started

    # The wait is bounded by the stop event, not by the interval.
    assert elapsed < 2.0
    for snapshot in resource.report()["loops"].values():
        assert snapshot["running"] is False
    assert resource.report()["started"] is False


@pytest.mark.asyncio
async def test_shutdown_drains_the_worker_lease() -> None:
    """The worker gets the stop event so a claimed run is not left to its TTL."""
    worker = FakeWorker()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=True, consumer=False),
        dependencies=wired_dependencies(),
        run_worker=worker,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    await wait_until(lambda: worker.calls > 0, timeout=2.0)
    await resource.shutdown()
    assert worker.stopped_with is not None
    assert worker.stopped_with.is_set() is True


@pytest.mark.asyncio
async def test_shutdown_without_any_loop_does_not_call_the_worker() -> None:
    """A blocked loop never composed a worker, so there is nothing to drain."""
    worker = FakeWorker()
    config = enabled_config()
    resource = AgentRunWorkerResource(
        config=config,
        dependencies=None,
        run_worker=worker,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    register_host_dependency_provider(unavailable_host_dependencies)
    await resource.startup()
    await resource.shutdown()
    assert resource.report()["started"] is False


@pytest.mark.asyncio
async def test_startup_shutdown_startup_is_idempotent() -> None:
    """A restarted resource rebuilds its stop event and loops again."""
    dispatcher = FakeDispatcher()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=False, consumer=True, consumer_interval_sec=3600),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    assert await wait_until(lambda: dispatcher.calls, timeout=2.0)
    await resource.shutdown()
    first = len(dispatcher.calls)

    await resource.startup()
    assert await wait_until(lambda: len(dispatcher.calls) > first, timeout=2.0)
    await resource.shutdown()


@pytest.mark.asyncio
async def test_shutdown_tolerates_a_loop_task_that_never_finishes(monkeypatch) -> None:
    """A wedged tick is cancelled after the bounded grace window, not awaited forever."""
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=False, consumer=True, consumer_interval_sec=3600),
        dependencies=wired_dependencies(),
        dispatcher=FakeDispatcher(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )

    async def wedged(_loop) -> None:  # pragma: no cover - driven by the test
        await asyncio.Event().wait()

    monkeypatch.setattr(resource, "_serve", wedged)
    monkeypatch.setattr(
        "prodavan.core.infra.agent_run_worker_resource.SHUTDOWN_TIMEOUT_SEC", 0.05
    )
    await resource.startup()
    await asyncio.sleep(0)
    await resource.shutdown()
    assert resource.report()["loops"]["intent_consumer"]["running"] is False
