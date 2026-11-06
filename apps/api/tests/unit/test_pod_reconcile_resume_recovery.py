"""Acceptance checks for recovering interrupted employee project resumes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.reconcile import PodReconcileService
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus


def _project(*, status: str = ProjectStatus.PAUSED) -> MagicMock:
    return MagicMock(
        id="prj_resume1234567890",
        company_id="cmp_resume1234567890",
        cabinet_id="cab_resume1234567890",
        owner_employee_id=None,
        status=status,
        launch_phase="resuming",
        resume_generation=7,
    )


def _pod(*, last_error: str | None = None) -> MagicMock:
    return MagicMock(
        id="pod_resume1234567890",
        status=PodStatus.PAUSED,
        desired_state=PodDesiredState.RUNNING.value,
        last_error=last_error,
    )


@pytest.mark.asyncio
async def test_reconcile_recovers_resume_after_api_process_restart() -> None:
    """Persisted resuming marker is completed and follows normal resume effects."""
    session = AsyncMock()
    project, pod = _project(), _pod()
    service = PodReconcileService(session, runtime=AsyncMock())
    service._pods.sync_desired = AsyncMock()
    session.execute = AsyncMock()
    service._reap_zombies = AsyncMock(return_value=0)

    with (
        patch("prodavan.application.pod_service.reconcile.PodMetricsSampler") as sampler_cls,
        patch(
            "prodavan.application.pod_service.reconcile.RuntimeObservationService"
        ) as observation_cls,
        patch(
            "prodavan.application.project_service.lifecycle_emitter.ProjectLifecycleEmitter.emit",
            new=AsyncMock(),
        ) as emit,
        patch(
            "prodavan.application.projects.runtime_side_effects.bootstrap_project_sessions",
            new=AsyncMock(return_value={"registered": 1}),
        ) as bootstrap,
    ):
        sampler_cls.return_value.sample_managed_pods = AsyncMock(return_value={})
        observation_cls.return_value.sync_runtime_health = AsyncMock(return_value="noop")
        bootstrap.return_value = {"registered": 1}
        stale_preparing_query = MagicMock()
        stale_preparing_query.scalars.return_value.all.return_value = []
        first_query = MagicMock()
        first_query.all.return_value = [(pod, project)]
        # The marker is released by a conditional UPDATE on the observed resume
        # generation, so this pass wins the CAS by definition here: rowcount 1.
        clear_marker = MagicMock()
        clear_marker.rowcount = 1
        active_query = MagicMock()
        active_query.scalars.return_value.all.return_value = []
        observation_query = MagicMock()
        observation_query.all.return_value = []
        session.execute.side_effect = [
            stale_preparing_query,
            first_query,
            clear_marker,
            active_query,
            observation_query,
        ]
        session.get = AsyncMock(return_value=None)

        result = await service._run_reconcile()

    assert result["fixed"] == 1
    service._pods.sync_desired.assert_awaited_once_with(
        project.id,
        PodDesiredState.RUNNING,
        principal=Principal(sub="system:pod-reconcile", roles=frozenset({"platform.admin"})),
        reason="resume",
    )
    assert project.status == ProjectStatus.ACTIVE
    assert project.launch_phase is None
    emit.assert_awaited_once()
    assert emit.await_args.kwargs["event_type"] == "project.resumed"
    assert emit.await_args.kwargs["idempotency_key"] == (
        f"project-resumed:{project.id}:{project.resume_generation}"
    )
    bootstrap.assert_called_once()


@pytest.mark.asyncio
async def test_resume_recovery_retry_reuses_durable_event_identity() -> None:
    """A crash after event flush leaves the retry on the same resume key."""
    session = AsyncMock()
    project, pod = _project(), _pod()
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
    ):
        await service._recover_interrupted_resume(project, pod)
        project.launch_phase = "resuming"
        await service._recover_interrupted_resume(project, pod)

    assert emit.await_count == 2
    first_key = emit.await_args_list[0].kwargs["idempotency_key"]
    second_key = emit.await_args_list[1].kwargs["idempotency_key"]
    assert first_key == second_key == f"project-resumed:{project.id}:7"


@pytest.mark.asyncio
async def test_resume_recovery_failure_marks_project_error_and_clears_marker() -> None:
    session = AsyncMock()
    project, pod = _project(status=ProjectStatus.COMPLETED), _pod()
    service = PodReconcileService(session, runtime=AsyncMock())
    service._pods.sync_desired = AsyncMock(side_effect=RuntimeError("runtime unavailable"))

    await service._recover_interrupted_resume(project, pod)

    service._pods.sync_desired.assert_awaited_once()
    assert project.status == ProjectStatus.ERROR
    assert project.launch_phase is None
    assert pod.last_error == "runtime unavailable"


@pytest.mark.asyncio
async def test_resume_recovery_clears_stale_pod_error_after_bootstrap() -> None:
    session = AsyncMock()
    project, pod = _project(), _pod(last_error="stale runtime failure")
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
        ),
    ):
        await service._recover_interrupted_resume(project, pod)

    assert project.status == ProjectStatus.ACTIVE
    assert project.launch_phase is None
    assert pod.last_error is None


@pytest.mark.asyncio
async def test_resume_bootstrap_failure_keeps_marker_for_reconcile_retry() -> None:
    session = AsyncMock()
    project, pod = _project(), _pod()
    service = PodReconcileService(session, runtime=AsyncMock())
    service._pods.sync_desired = AsyncMock()

    with patch(
        "prodavan.application.projects.runtime_side_effects.bootstrap_project_sessions",
        new=AsyncMock(side_effect=RuntimeError("bridge unavailable")),
    ) as bootstrap:
        await service._recover_interrupted_resume(project, pod)

    bootstrap.assert_awaited_once_with(session, project_id=project.id)
    assert project.status == ProjectStatus.ACTIVE
    assert project.launch_phase == "resuming"


@pytest.mark.asyncio
async def test_resume_recovery_retries_registration_after_crash() -> None:
    session = AsyncMock()
    project, pod = _project(), _pod()
    service = PodReconcileService(session, runtime=AsyncMock())
    service._pods.sync_desired = AsyncMock()

    with (
        patch(
            "prodavan.application.projects.runtime_side_effects.bootstrap_project_sessions",
            new=AsyncMock(side_effect=[RuntimeError("bridge crash"), {"registered": 1}]),
        ) as bootstrap,
        patch(
            "prodavan.application.project_service.lifecycle_emitter.ProjectLifecycleEmitter.emit",
            new=AsyncMock(),
        ) as emit,
    ):
        await service._recover_interrupted_resume(project, pod)
        assert project.launch_phase == "resuming"
        await service._recover_interrupted_resume(project, pod)

    assert bootstrap.await_count == 2
    assert project.status == ProjectStatus.ACTIVE
    assert project.launch_phase is None
    assert emit.await_count == 2
    assert [call.kwargs["idempotency_key"] for call in emit.await_args_list] == [
        f"project-resumed:{project.id}:{project.resume_generation}",
        f"project-resumed:{project.id}:{project.resume_generation}",
    ]


@pytest.mark.asyncio
async def test_resume_bootstrap_when_pod_is_not_running_keeps_marker_for_retry() -> None:
    session = AsyncMock()
    project, pod = _project(), _pod()
    service = PodReconcileService(session, runtime=AsyncMock())
    service._pods.sync_desired = AsyncMock()

    with patch(
        "prodavan.application.projects.runtime_side_effects.bootstrap_project_sessions",
        new=AsyncMock(return_value={"registered": 0, "skipped": "pod_not_running"}),
    ) as bootstrap:
        await service._recover_interrupted_resume(project, pod)

    bootstrap.assert_awaited_once_with(session, project_id=project.id)
    assert project.status == ProjectStatus.ACTIVE
    assert project.launch_phase == "resuming"
