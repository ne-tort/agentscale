"""Disabled-by-default and fail-closed dependency behaviour (MIG-AR-HOST-01).

The single most important property of this task: the resource is registered in
the production lifespan of every deployment, so "registered" must not mean "does
something".  These tests pin that down three ways — no task, no session, no
provider lookup — and then pin the opposite: an enabled flag without an assembled
adapter idles with a blocker instead of breaking startup.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from prodavan.core.infra import agent_run_worker_resource as host
from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_DEPENDENCIES_UNWIRED,
    LOOP_CONSUMER,
    LOOP_WORKER,
    AgentRunHostDependencies,
    AgentRunHostSettings,
    AgentRunWorkerResource,
    register_host_dependency_provider,
    unavailable_host_dependencies,
)

from .acceptance_fixtures import (
    FakeLock,
    FakeSessionFactory,
    enabled_config,
    wired_dependencies,
)


@pytest.fixture(autouse=True)
def _restore_provider() -> None:
    """The provider is process-global; every test must leave the default behind."""
    previous = host.current_host_dependency_provider()
    yield
    register_host_dependency_provider(previous)


# ---------------------------------------------------------------------------
# Default settings
# ---------------------------------------------------------------------------


def test_settings_default_is_disabled() -> None:
    config = AgentRunHostSettings()
    assert config.worker_enabled is False
    assert config.consumer_enabled is False
    assert config.any_enabled is False


def test_default_provider_assembles_nothing() -> None:
    """Fail-closed default: no adapter, no in-memory stand-in."""
    deps = unavailable_host_dependencies()
    assert deps.execution_port is None
    assert deps.runs is None
    assert deps.journal is None
    assert deps.runner is None
    assert deps.consumer_blockers == (host.BLOCKER_EXECUTION_PORT_UNWIRED,)


# ---------------------------------------------------------------------------
# Disabled startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_startup_starts_no_loop_and_opens_no_session() -> None:
    factory = FakeSessionFactory()
    lock = FakeLock()
    resource = AgentRunWorkerResource(
        config=AgentRunHostSettings(),
        session_factory=factory,
        lock=lock,
    )
    await resource.startup()

    assert resource.enabled is False
    assert resource.report()["started"] is False
    for snapshot in resource.report()["loops"].values():
        assert snapshot["running"] is False
        assert snapshot["ticks"] == 0
    assert factory.opened == 0
    assert lock.keys == []
    # health() is "not applicable", not "unhealthy": a disabled resource must not
    # drag the process readiness probe down.
    assert await resource.health() is None

    await resource.shutdown()
    assert factory.opened == 0


@pytest.mark.asyncio
async def test_disabled_resource_does_not_resolve_dependencies() -> None:
    """A provider is never called while the feature is off."""
    calls: list[int] = []

    def provider() -> AgentRunHostDependencies:
        calls.append(1)
        return wired_dependencies()

    register_host_dependency_provider(provider)
    resource = AgentRunWorkerResource(
        config=AgentRunHostSettings(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    await resource.shutdown()
    assert calls == []


@pytest.mark.asyncio
async def test_shutdown_before_startup_is_a_no_op() -> None:
    """Lifespan shutdown runs even for resources that never started."""
    resource = AgentRunWorkerResource(
        config=enabled_config(),
        dependencies=wired_dependencies(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.shutdown()  # must not raise, must not create a loop
    assert resource.report()["started"] is False


# ---------------------------------------------------------------------------
# Enabled without dependencies → idle, never crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enabled_flag_without_adapter_idles_and_stays_healthy() -> None:
    """The core fail-closed rule of the task.

    ``AGENT_RUN_WORKER_ENABLED=true`` on a deployment that has no execution
    adapter must: start the process, keep readiness green, consume nothing, and
    publish the blocker so an operator can see why nothing happens.
    """
    register_host_dependency_provider(None)  # default: nothing assembled
    factory = FakeSessionFactory()
    lock = FakeLock()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker_interval_sec=3600, consumer_interval_sec=3600),
        session_factory=factory,
        lock=lock,
    )
    await resource.startup()

    # Let both loop tasks reach their first tick (each tick is a skip).
    for _ in range(3):
        await asyncio.sleep(0)
    for snapshot in resource.report()["loops"].values():
        assert snapshot["blocked"] is True
        assert snapshot["blockers"] == [BLOCKER_DEPENDENCIES_UNWIRED]

    assert await resource.health() is True
    # Idling is not "doing nothing quietly at the database": no session was taken.
    assert factory.opened == 0
    assert lock.keys == []

    consumer = await resource.drain_consumer_once()
    worker = await resource.run_worker_round_once()
    assert consumer == {"skipped": True, "reason": BLOCKER_DEPENDENCIES_UNWIRED, "claimed": 0}
    assert worker == {
        "skipped": True,
        "reason": BLOCKER_DEPENDENCIES_UNWIRED,
        "claimed": 0,
        "ticks": 0,
    }
    assert factory.opened == 0

    await resource.shutdown()
    assert resource.report()["started"] is False


@pytest.mark.asyncio
async def test_partial_dependencies_block_only_the_incomplete_loop() -> None:
    """A consumer needs the port; a worker needs the whole bundle.

    The run worker also needs a run projection source, a durable attempt journal
    and a turn runner.  A missing one must block the *worker* and leave the
    consumer usable, instead of silently running a half-composed worker.
    """
    deps = AgentRunHostDependencies(execution_port=object())
    assert deps.consumer_blockers == ()
    assert deps.worker_blockers == (
        host.BLOCKER_RUN_SOURCE_UNWIRED,
        host.BLOCKER_JOURNAL_UNWIRED,
        host.BLOCKER_TURN_RUNNER_UNWIRED,
    )


@pytest.mark.asyncio
async def test_partial_worker_bundle_reports_the_missing_member() -> None:
    """The worker round names the first missing dependency instead of guessing."""
    partial = AgentRunHostDependencies(execution_port=object(), runs=object())
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=True, consumer=False),
        dependencies=partial,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    result = await resource.run_worker_round_once()
    assert result["skipped"] is True
    assert result["reason"] == host.BLOCKER_JOURNAL_UNWIRED
    await resource.shutdown()


@pytest.mark.asyncio
async def test_worker_constructor_refusal_is_absorbed() -> None:
    """``AgentRunWorker`` rejects an unusable port; the host must not raise.

    ``object()`` satisfies "not None" but has none of the fenced port methods, so
    the worker refuses to be built.  That refusal is a composition error the host
    reports and idles on — never an exception that escapes the tick and kills the
    loop.
    """
    unusable = AgentRunHostDependencies(
        execution_port=object(),
        runs=object(),
        journal=object(),
        runner=object(),
    )
    assert unusable.worker_blockers == ()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=True, consumer=False),
        dependencies=unusable,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    result = await resource.run_worker_round_once()
    assert result["skipped"] is True
    assert result["reason"] == host.BLOCKER_COMPOSITION_FAILED
    # The refusal is remembered: a broken composition is not retried per tick.
    assert (await resource.run_worker_round_once())["reason"] == host.BLOCKER_COMPOSITION_FAILED
    await resource.shutdown()


@pytest.mark.asyncio
async def test_dependency_provider_failure_degrades_to_blocked() -> None:
    """A raising composition root must not take the process down at startup."""

    def provider() -> AgentRunHostDependencies:
        raise RuntimeError("composition exploded")

    register_host_dependency_provider(provider)
    resource = AgentRunWorkerResource(
        config=enabled_config(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    assert await resource.health() is True
    result = await resource.drain_consumer_once()
    assert result["reason"] == BLOCKER_DEPENDENCIES_UNWIRED
    await resource.shutdown()


# ---------------------------------------------------------------------------
# Structure guard
# ---------------------------------------------------------------------------


def test_resource_declares_the_lifespan_contract() -> None:
    """``name`` must be stable: it keys the lifespan health report."""
    resource = AgentRunWorkerResource(config=AgentRunHostSettings())
    assert resource.name == "agent_run_host"
    for method in ("startup", "shutdown", "health"):
        assert inspect.iscoroutinefunction(getattr(resource, method))


def test_loop_names_are_distinct_and_used_by_the_report() -> None:
    resource = AgentRunWorkerResource(config=AgentRunHostSettings())
    report = resource.report()
    assert set(report["loops"]) == {LOOP_CONSUMER, LOOP_WORKER}
