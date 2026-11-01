"""Acceptance-focused unit coverage for trigger dispatch and resume fencing.

These tests intentionally use dispatcher collaborators as isolated fakes.  They
capture the public dictionary shapes returned by ``dispatch_batch`` as read
from the implementation and cover the reconcile retry boundary without a DB.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
from prodavan.application.projects.platform_event_service import PlatformEventService
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus


_PRINCIPAL = Principal(sub="employee-1", email="employee@example.test")


def _dispatcher(*, status: ProjectStatus, dispatch_results: list[dict]) -> AgentTriggerDispatcher:
    dispatcher = AgentTriggerDispatcher.__new__(AgentTriggerDispatcher)
    dispatcher._projects = SimpleNamespace(
        require_access=AsyncMock(return_value=SimpleNamespace(status=status))
    )
    dispatcher._triggers = SimpleNamespace(
        has_non_exempt_queued=AsyncMock(return_value=False)
    )
    dispatcher._dispatch_one = AsyncMock(side_effect=dispatch_results)
    return dispatcher


@pytest.mark.asyncio
async def test_dispatch_batch_paused_has_stable_public_shape() -> None:
    dispatcher = _dispatcher(status=ProjectStatus.PAUSED, dispatch_results=[])

    result = await dispatcher.dispatch_batch(
        project_id="project-1", principal=_PRINCIPAL, employee=None
    )

    assert result == {"dispatched": False, "reason": "project_paused", "items": []}
    dispatcher._dispatch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_batch_success_uses_count_and_item_shapes() -> None:
    first = {"dispatched": True, "trigger_id": "trigger-1", "kind": "chat.message", "run": {"id": "run-1"}}
    second = {"dispatched": True, "trigger_id": "trigger-2", "kind": "webhook.http", "action": "run_webhook.http", "run": {"id": "run-2"}}
    dispatcher = _dispatcher(
        status=ProjectStatus.ACTIVE,
        dispatch_results=[first, second, {"dispatched": False, "reason": "no queued triggers"}],
    )

    result = await dispatcher.dispatch_batch(
        project_id="project-1", principal=_PRINCIPAL, employee=None, max_n=3
    )

    assert result == {"dispatched": True, "count": 2, "items": [first, second]}


@pytest.mark.asyncio
async def test_dispatch_batch_failed_item_is_returned_with_real_shape() -> None:
    failed = {
        "dispatched": False,
        "trigger_id": "trigger-1",
        "kind": "chat.message",
        "reason": "dispatch_error: temporary runtime failure",
        "retried": True,
        "attempts": 2,
    }
    dispatcher = _dispatcher(
        status=ProjectStatus.ACTIVE,
        dispatch_results=[failed, {"dispatched": False, "reason": "no queued triggers"}],
    )

    result = await dispatcher.dispatch_batch(
        project_id="project-1", principal=_PRINCIPAL, employee=None, max_n=2
    )

    assert result == {"dispatched": False, "count": 0, "items": [failed]}


@pytest.mark.asyncio
async def test_platform_event_replay_returns_existing_event_without_write() -> None:
    existing = SimpleNamespace(
        id="event-1",
        event_type="project.resumed",
        company_id="company-1",
        project_id="project-1",
        cabinet_id=None,
        actor_sub="system",
        payload={"resume_generation": 4},
        created_at=None,
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=existing), add=AsyncMock())

    result = await PlatformEventService(session).emit(
        event_type="project.resumed",
        project_id="project-1",
        idempotency_key="project-resumed:project-1:4",
    )

    assert result == {
        "id": "event-1",
        "event_type": "project.resumed",
        "company_id": "company-1",
        "project_id": "project-1",
        "cabinet_id": None,
        "actor_sub": "system",
        "payload": {"resume_generation": 4},
        "created_at": None,
    }
    session.add.assert_not_called()


def test_reconcile_does_not_clear_marker_after_stale_or_failed_dispatch() -> None:
    from prodavan.application.pod_service.reconcile import PodReconcileService

    source = inspect.getsource(PodReconcileService._recover_interrupted_resume)
    dispatch = source.index("dispatch = await AgentTriggerDispatcher")
    lock = source.index("select(ProjectRow).where(ProjectRow.id == project.id).with_for_update()", dispatch)
    failure_guard = source.index("dispatch_failed =", lock)
    generation_guard = source.index("current.resume_generation == project.resume_generation", failure_guard)
    clear = source.index("current.launch_phase = None", generation_guard)

    assert dispatch < lock < failure_guard < generation_guard < clear
    assert '"dispatch_error"' in source[failure_guard:clear]
    assert '"project_paused"' in source[failure_guard:clear]
