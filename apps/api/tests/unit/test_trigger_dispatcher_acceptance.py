"""Acceptance-focused unit coverage for trigger dispatch and resume fencing.

These tests intentionally use dispatcher collaborators as isolated fakes.  They
capture the public dictionary shapes returned by ``dispatch_batch`` as read
from the implementation and cover the reconcile retry boundary without a DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
from prodavan.application.projects.platform_event_service import PlatformEventService
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus


_PRINCIPAL = Principal(sub="employee-1", email="employee@example.test")


def _resume_project() -> MagicMock:
    """A project persisted mid-resume (the state reconcile must complete)."""
    return MagicMock(
        id="project-1",
        company_id="company-1",
        cabinet_id="cabinet-1",
        owner_employee_id="employee-1",
        status=ProjectStatus.PAUSED,
        launch_phase="resuming",
        resume_generation=4,
    )


def _paused_pod() -> MagicMock:
    return MagicMock(
        id="pod-1",
        status=PodStatus.PAUSED,
        desired_state=PodDesiredState.RUNNING.value,
        last_error=None,
    )


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


@pytest.mark.asyncio
async def test_reconcile_releases_marker_even_when_trigger_dispatch_fails() -> None:
    """The resume marker must not be held hostage by a later trigger drain.

    This replaces an earlier source-order assertion that required reconcile to
    clear ``launch_phase`` only *after* a successful ``dispatch_batch``. That
    expectation inverted the contract: ``launch_phase == "resuming"`` means
    "pod resume / bridge registration still running", and both are finished
    before the drain starts. Gating the clear on the drain left an ACTIVE
    project labelled "resuming" whenever the owner row was missing or the drain
    reported an inconclusive result, so the next reconcile pass re-ran bridge
    registration for a project that was not resuming.

    The check below is behavioural and stricter than the ordering scan it
    replaces: a dispatch that raises must still leave the project resumed,
    released, and committed, and must not escape into the reconcile pass.
    """
    from unittest.mock import patch

    from prodavan.application.pod_service.reconcile import PodReconcileService

    session = AsyncMock()
    project, pod = _resume_project(), _paused_pod()
    service = PodReconcileService(session, runtime=AsyncMock())
    service._pods.sync_desired = AsyncMock()

    with (
        patch(
            "prodavan.application.projects.runtime_side_effects.bootstrap_project_sessions",
            new=AsyncMock(return_value={"registered": 1}),
        ),
        patch(
            "prodavan.application.project_service.lifecycle_emitter.ProjectLifecycleEmitter.emit",
            new=AsyncMock(),
        ) as emit,
        patch(
            "prodavan.application.agent.trigger_dispatcher.AgentTriggerDispatcher.dispatch_batch",
            new=AsyncMock(side_effect=RuntimeError("drain unavailable")),
        ) as dispatch,
    ):
        await service._recover_interrupted_resume(project, pod)

    assert project.status == ProjectStatus.ACTIVE
    assert project.launch_phase is None
    assert emit.await_count == 1
    dispatch.assert_awaited_once()
    # The released marker was persisted before the drain was attempted.
    session.commit.assert_awaited()
