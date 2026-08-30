"""Unit tests — project.error propagation and pod reload limits."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.project_service.command import ProjectCommand
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow


@pytest.fixture(autouse=True)
def _noop_container_env_loader():
    with patch(
        "prodavan.application.pod_service.command.ContainerEnvLoader.load_for_project",
        new=AsyncMock(return_value=()),
    ):
        yield


def _principal() -> Principal:
    return Principal(sub="emp:test", roles=frozenset({"employee"}))


def _project(*, status: str = ProjectStatus.ACTIVE) -> ProjectRow:
    return ProjectRow(
        id="prj_test1234567890",
        company_id="cmp_test1234567890",
        cabinet_id="cab_test1234567890",
        owner_employee_id="emp_test1234567890",
        name="Demo",
        slug="demo",
        status=status,
        visibility_mode="cabinet_shared",
        workspace_key="wk_demo",
        container_ref="object-ws:wk_demo",
    )


@pytest.mark.asyncio
async def test_sync_desired_failure_sets_project_error() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.PENDING,
        desired_state=PodDesiredState.ABSENT,
        runtime_ref="object-ws:wk_demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.get = AsyncMock(return_value=project)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = pod
    session.execute = AsyncMock(return_value=execute_result)

    runtime = AsyncMock()
    runtime.ensure_running = AsyncMock(side_effect=RuntimeError("k8s boom"))
    events = AsyncMock(spec=PodLifecycleEmitter)
    project_events = AsyncMock()

    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=AsyncMock())
    cmd._project_events = project_events

    with pytest.raises(RuntimeError, match="k8s boom"):
        await cmd.sync_desired(
            project.id,
            PodDesiredState.RUNNING,
            principal=_principal(),
            reason="launch",
        )

    assert pod.status == PodStatus.FAILED
    assert pod.last_error == "k8s boom"
    assert project.status == ProjectStatus.ERROR
    project_events.emit.assert_awaited()
    assert project_events.emit.await_args.kwargs["event_type"] == "project.failed"


@pytest.mark.asyncio
async def test_sync_desired_reload_recovers_project_from_error() -> None:
    session = AsyncMock()
    project = _project(status=ProjectStatus.ERROR)
    failed = ProjectPodRow(
        id="pod_failed",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.FAILED,
        desired_state=PodDesiredState.RUNNING,
        runtime_ref="object-ws:wk_demo",
        last_error="boom",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.get = AsyncMock(return_value=project)

    live_result = MagicMock()
    live_result.scalar_one_or_none.return_value = None
    failed_result = MagicMock()
    failed_result.scalar_one_or_none.return_value = failed
    session.execute = AsyncMock(side_effect=[live_result, failed_result])

    runtime = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    project_events = AsyncMock()
    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=AsyncMock())
    cmd._project_events = project_events

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="reload",
    )

    assert project.status == ProjectStatus.ACTIVE
    assert failed.status == PodStatus.PROVISIONING
    recovered = [c.kwargs["event_type"] for c in project_events.emit.await_args_list]
    assert "project.recovered" in recovered


@pytest.mark.asyncio
async def test_reload_project_allowed_when_healthy() -> None:
    session = AsyncMock()
    row = _project(status=ProjectStatus.ACTIVE)
    with patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=MagicMock()):
        cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)
    cmd._pods.sync_desired = AsyncMock()
    cmd._project_public = AsyncMock(return_value={"id": row.id, "status": ProjectStatus.ACTIVE})
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("prodavan.application.pod_service.query.PodQuery") as pq_cls:
        pq = pq_cls.return_value
        pq.runtime_view = AsyncMock(return_value={})
        with patch(
            "prodavan.application.pod_service.runtime_observation.RuntimeObservationService"
        ) as obs_cls:
            obs_cls.return_value.wait_for_running = AsyncMock()
            with patch("prodavan.core.infra.cache.rate_limit_enforce", new_callable=AsyncMock):
                await cmd.reload_project(project_id=row.id, principal=_principal(), employee=None)

    cmd._pods.sync_desired.assert_awaited_once()
    assert cmd._pods.sync_desired.await_args.kwargs["reason"] == "reload"


@pytest.mark.asyncio
async def test_reload_project_allowed_when_active_container_degraded() -> None:
    session = AsyncMock()
    row = _project(status=ProjectStatus.ACTIVE)
    pod = ProjectPodRow(
        id="pod_live",
        project_id=row.id,
        workspace_key=row.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING.value,
        runtime_ref="pod-wk-demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=MagicMock()):
        cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)
    cmd._pods.sync_desired = AsyncMock()
    cmd._project_public = AsyncMock(return_value={"id": row.id, "status": ProjectStatus.ACTIVE})
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("prodavan.application.pod_service.query.PodQuery") as pq_cls:
        pq = pq_cls.return_value
        pq.runtime_view = AsyncMock(return_value={"observed_state": "degraded"})
        pq._get_failed_row = AsyncMock(return_value=None)
        with patch(
            "prodavan.application.pod_service.runtime_observation.RuntimeObservationService"
        ) as obs_cls:
            obs_cls.return_value._get_live_pod = AsyncMock(return_value=pod)
            obs_cls.return_value.observe = AsyncMock(
                return_value={"observed_state": "degraded", "last_error": "metrics missing"}
            )
            obs_cls.return_value.wait_for_running = AsyncMock()
            with patch("prodavan.core.infra.cache.rate_limit_enforce", new_callable=AsyncMock):
                await cmd.reload_project(project_id=row.id, principal=_principal(), employee=None)

    cmd._pods.sync_desired.assert_awaited()


@pytest.mark.asyncio
async def test_reload_project_rate_limit_redis_unavailable() -> None:
    session = AsyncMock()
    row = _project(status=ProjectStatus.ERROR)
    with patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=MagicMock()):
        cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)

    with patch("prodavan.application.pod_service.query.PodQuery") as pq_cls:
        pq = pq_cls.return_value
        pq.runtime_view = AsyncMock(return_value={})
        pq._get_failed_row = AsyncMock(return_value=None)
        with patch(
            "prodavan.application.pod_service.runtime_observation.RuntimeObservationService"
        ) as obs_cls:
            obs_cls.return_value._get_live_pod = AsyncMock(return_value=None)
            with patch("prodavan.core.infra.cache.rate_limit_enforce", new_callable=AsyncMock) as rl:
                rl.side_effect = AppError(
                    code="REDIS_UNAVAILABLE",
                    title="Service Unavailable",
                    status=503,
                    detail="redis unavailable",
                )
                with pytest.raises(AppError) as exc:
                    await cmd.reload_project(project_id=row.id, principal=_principal(), employee=None)

    assert exc.value.code == "REDIS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_rate_limit_enforce_fail_closed_when_redis_disabled() -> None:
    from prodavan.core.infra.cache import rate_limit_enforce

    with patch("prodavan.core.infra.redis_manager.get_redis_manager", return_value=None):
        with pytest.raises(AppError) as exc:
            await rate_limit_enforce("test:key", limit=1, window_sec=60)

    assert exc.value.code == "REDIS_UNAVAILABLE"
