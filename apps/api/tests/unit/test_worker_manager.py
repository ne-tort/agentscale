"""Unit tests — Celery WorkerManager (eager) + task registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prodavan.core.infra.worker_manager import WorkerManager, get_worker_manager, set_worker_manager
from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.enqueue import enqueue_trigger_drain


@pytest.mark.asyncio
async def test_worker_manager_disabled() -> None:
    set_worker_manager(None)
    mgr = WorkerManager(enabled=False, broker_url="memory://")
    await mgr.startup()
    assert mgr.enabled is False
    assert mgr.uses_celery_executor is False
    assert await mgr.health() is None
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_eager_registers_tasks() -> None:
    set_worker_manager(None)
    mgr = WorkerManager(
        enabled=True,
        broker_url="memory://",
        task_always_eager=True,
        schedule_trigger_drain=True,
        schedule_idle_pause=True,
    )
    await mgr.startup()
    assert mgr.enabled is True
    # Eager mode keeps in-process loop available as fallback for API process.
    assert mgr.uses_celery_executor is False
    assert job_names.TRIGGER_DRAIN in mgr._app.tasks
    assert job_names.DISPATCH_TRIGGER in mgr._app.tasks
    assert job_names.IDLE_PAUSE_SWEEP in mgr._app.tasks
    assert job_names.REMATERIALIZE_PROJECT in mgr._app.tasks
    assert "prodavan-trigger-drain" in mgr._app.conf.beat_schedule
    assert "prodavan-idle-pause-sweep" in mgr._app.conf.beat_schedule

    with patch(
        "prodavan.application.agent.trigger_worker.drain_once",
        new_callable=AsyncMock,
        return_value={"dispatched": True, "count": 0, "projects": []},
    ):
        result = mgr._app.tasks[job_names.TRIGGER_DRAIN].apply().get()
        assert result["count"] == 0
        enq = enqueue_trigger_drain()
        assert enq["enqueued"] is True
    await mgr.shutdown()
    assert get_worker_manager() is None


def test_cli_bootstrap_celery_app_not_none() -> None:
    from prodavan.core.infra import worker_manager as wm

    app = wm.bootstrap_celery_app_from_settings()
    assert app is not None
    assert wm.celery_app is not None


@pytest.mark.asyncio
async def test_uses_celery_executor_when_not_eager() -> None:
    set_worker_manager(None)
    mgr = WorkerManager(enabled=True, broker_url="redis://localhost:6379/1", task_always_eager=False)
    await mgr.startup()
    assert mgr.uses_celery_executor is True
    await mgr.shutdown()
