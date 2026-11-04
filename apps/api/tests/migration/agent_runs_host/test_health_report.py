"""Health report and state visibility (MIG-AR-HOST-01).

``LifespanResource.health`` returns ``bool | None``, which cannot answer the only
question an operator has when nothing happens: *is it disabled, is it blocked on
a missing adapter, or is it running and finding no work?*  The task therefore
requires a meaningful state when a loop is enabled, and this file pins the four
distinguishable states:

======================  =========================  =============
state                   ``health()``               ``report()``
======================  =========================  =============
disabled (default)      ``None`` (not applicable)  ``enabled=False``
starting / stopped      ``False``                  ``started=False``
enabled, blocked        ``True``                   ``blocked=True`` + blocker
enabled, serving        ``True``                   ``running=True``, counters
======================  =========================  =============
"""

from __future__ import annotations

import pytest

from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_DEPENDENCIES_UNWIRED,
    LOOP_CONSUMER,
    LOOP_WORKER,
    AgentRunHostSettings,
    AgentRunWorkerResource,
    current_host_dependency_provider,
    register_host_dependency_provider,
    unavailable_host_dependencies,
)

from .acceptance_fixtures import (
    NOW,
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


def _clock():
    return NOW


def test_disabled_health_is_not_applicable() -> None:
    resource = AgentRunWorkerResource(config=AgentRunHostSettings())
    report = resource.report()
    assert report["enabled"] is False
    assert report["started"] is False
    assert report["queue_name"] == "agent-control-plane"
    for snapshot in report["loops"].values():
        assert snapshot["enabled"] is False
        assert snapshot["ticks"] == 0
        assert snapshot["last_tick_at"] is None


@pytest.mark.asyncio
async def test_enabled_but_not_started_health_is_false() -> None:
    """Between construction and lifespan startup the resource is not serving."""
    resource = AgentRunWorkerResource(
        config=enabled_config(),
        dependencies=wired_dependencies(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    assert await resource.health() is False


@pytest.mark.asyncio
async def test_serving_loop_reports_last_tick_and_counters() -> None:
    dispatcher = FakeDispatcher()
    worker = FakeWorker()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker_interval_sec=3600, consumer_interval_sec=3600),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher,
        run_worker=worker,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
        clock=_clock,
        owner_id="pod-a",
    )
    await resource.startup()
    assert await wait_until(
        lambda: resource.report()["loops"][LOOP_CONSUMER]["ticks"] >= 1, timeout=3.0
    )
    assert await resource.health() is True

    consumer = resource.report()["loops"][LOOP_CONSUMER]
    assert consumer["running"] is True
    assert consumer["blocked"] is False
    assert consumer["lock_key"] == "prodavan.agent_run.intent_consumer"
    assert consumer["last_tick_at"] == NOW.isoformat()
    assert consumer["started_at"] == NOW.isoformat()
    assert consumer["last_result"]["claimed"] == 2
    assert consumer["last_result"]["repaired"] == 3
    assert consumer["interval_sec"] == 3600.0
    assert consumer["batch_max"] == 4

    await resource.shutdown()
    assert await resource.health() is False
    for snapshot in resource.report()["loops"].values():
        assert snapshot["running"] is False


@pytest.mark.asyncio
async def test_owner_id_is_stable_per_loop_and_instance() -> None:
    """The lease owner must be readable in an audit and stable across ticks."""
    dispatcher = FakeDispatcher()
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=False, consumer=True),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
        owner_id="pod-a",
    )
    await resource.drain_consumer_once()
    await resource.drain_consumer_once()
    owners = {call["owner_id"] for call in dispatcher.calls}
    assert owners == {"agent-run-host:pod-a:intent_consumer"}
    assert all(len(owner) <= 128 for owner in owners)


@pytest.mark.asyncio
async def test_blocked_loop_is_healthy_and_explains_itself() -> None:
    """Readiness stays green; the blocker is data, not an error state."""
    register_host_dependency_provider(unavailable_host_dependencies)
    resource = AgentRunWorkerResource(
        config=enabled_config(worker_interval_sec=3600, consumer_interval_sec=3600),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.startup()
    assert await resource.health() is True
    for snapshot in resource.report()["loops"].values():
        assert snapshot["blocked"] is True
        assert snapshot["blockers"] == [BLOCKER_DEPENDENCIES_UNWIRED]
        assert snapshot["running"] is True
    await resource.shutdown()


@pytest.mark.asyncio
async def test_report_snapshot_is_a_copy_not_the_live_state() -> None:
    """Callers may serialise the report; mutating it cannot corrupt the loop."""
    resource = AgentRunWorkerResource(
        config=enabled_config(),
        dependencies=wired_dependencies(),
        dispatcher=FakeDispatcher(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    await resource.drain_consumer_once()
    first = resource.report()
    first["loops"][LOOP_CONSUMER]["last_result"]["claimed"] = 999
    first["loops"].clear()
    second = resource.report()
    assert set(second["loops"]) == {LOOP_CONSUMER, LOOP_WORKER}
    assert second["loops"][LOOP_CONSUMER]["last_result"]["claimed"] == 2
