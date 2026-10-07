"""Unit tests — project.error propagation and pod reload limits."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.project_service.command import ProjectCommand
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow


@pytest.fixture(autouse=True)
def _noop_container_env_loader(monkeypatch):
    # Bridge revocation gen lives in Redis; unit tests run without it, so use
    # the in-process fallback (prod runs strict with Redis as SoT).
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_strict", False)
    with (
        patch(
            "prodavan.application.pod_service.command.ContainerEnvLoader.load_for_project",
            new=AsyncMock(return_value=()),
        ),
        patch(
            "prodavan.application.pod_service.command.PodCommand._mint_pod_bridge_token",
            new=AsyncMock(return_value=("test-pod-bridge-jwt", 0)),
        ),
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

    with patch(
        "prodavan.application.projects.workspace_checkpoint.checkpoint_project_workspace",
        new=AsyncMock(return_value=None),
    ):
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
    from prodavan.config.settings import settings
    from prodavan.core.infra.cache import rate_limit_enforce

    with (
        patch("prodavan.core.infra.redis_manager.get_redis_manager", return_value=None),
        patch.object(settings, "auth_mode", "oidc"),
    ):
        with pytest.raises(AppError) as exc:
            await rate_limit_enforce("test:key", limit=1, window_sec=60)

    assert exc.value.code == "REDIS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_rate_limit_enforce_allows_without_redis_in_test_mode() -> None:
    from prodavan.config.settings import settings
    from prodavan.core.infra.cache import rate_limit_enforce

    with (
        patch("prodavan.core.infra.redis_manager.get_redis_manager", return_value=None),
        patch.object(settings, "auth_mode", "test"),
    ):
        await rate_limit_enforce("test:key", limit=1, window_sec=60)


# ---------------------------------------------------- launch 502 (H6) + M12


def _launch_command(
    session: AsyncMock,
    row: ProjectRow,
    sync_desired: AsyncMock,
    request: pytest.FixtureRequest | None = None,
) -> ProjectCommand:
    # launch() resolves the policy via AdminCompanyService directly (not
    # cmd._companies); the patch must stay alive for the whole test, so it is
    # started here and stopped via a finalizer when a request fixture is given.
    policy = MagicMock(preferred_provider="openrouter", platform_fallback=True)
    policy_patcher = patch.object(
        AdminCompanyService, "get_agent_policy", AsyncMock(return_value=policy)
    )
    policy_patcher.start()
    if request is not None:
        request.addfinalizer(policy_patcher.stop)
    with patch(
        "prodavan.application.pod_service.command.build_pod_runtime",
        return_value=MagicMock(),
    ):
        cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)
    cmd._get_live_pod = AsyncMock(return_value=None)
    cmd._cabinets = MagicMock()
    cmd._cabinets.get_instance = AsyncMock(return_value=MagicMock(name="inst"))
    cmd._companies = MagicMock()
    mat = MagicMock(
        module_paths={},
        workspace_root="/ws/root",
        mcp_config_path="/ws/mcp.json",
        status="ok",
        package_names=[],
        sandbox_packages=[],
        agents_source="default",
    )
    cmd._materialize.materialize_project = AsyncMock(return_value=mat)
    cmd._resolve_enabled_module_ids = AsyncMock(return_value=[])
    cmd._pods.provision_for_project = AsyncMock(return_value={})
    cmd._pods.sync_desired = sync_desired
    cmd._events.emit = AsyncMock()
    cmd._project_public = AsyncMock(return_value={"id": row.id, "status": row.status})
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return cmd


@pytest.mark.asyncio
async def test_launch_failure_raises_502_and_persists_error(request: pytest.FixtureRequest) -> None:
    session = AsyncMock()
    row = _project()
    row.agent_provider = "openrouter"
    row.resolved_ai_key_id = "key_1"
    cmd = _launch_command(
        session,
        row,
        AsyncMock(side_effect=RuntimeError("sandbox create boom")),
        request=request,
    )

    with patch("prodavan.application.ai_keys.service.AiKeysService") as keys_cls:
        keys_cls.return_value.resolve_credentials_for_project = AsyncMock()
        with pytest.raises(AppError) as exc:
            await cmd.launch(project_id=row.id, principal=_principal(), employee=None)

    assert exc.value.code == "POD_LAUNCH_FAILED"
    assert exc.value.status == 502
    assert "sandbox create boom" in (exc.value.detail or "")
    assert exc.value.detail is not None and len(exc.value.detail) <= 300
    # PG state is committed BEFORE the client sees the 502.
    assert row.status == ProjectStatus.ERROR
    assert row.launch_phase is None
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_launch_success_schedules_session_bootstrap(request: pytest.FixtureRequest) -> None:
    from prodavan.domain.projects.runtime_ops import ProjectRuntimeOp

    session = AsyncMock()
    row = _project()
    row.agent_provider = "openrouter"
    row.resolved_ai_key_id = "key_1"
    cmd = _launch_command(session, row, AsyncMock(return_value=None), request=request)

    with patch("prodavan.application.ai_keys.service.AiKeysService") as keys_cls:
        keys_cls.return_value.resolve_credentials_for_project = AsyncMock()
        with patch(
            "prodavan.application.project_service.command.schedule_bootstrap_background"
        ) as sched:
            await cmd.launch(project_id=row.id, principal=_principal(), employee=None)

    assert row.status == ProjectStatus.ACTIVE
    sched.assert_called_once_with(
        project_id=row.id, op=ProjectRuntimeOp.LAUNCH
    )


# ---------------------------------------------- system pause + idle sweep (H4)


@pytest.mark.asyncio
async def test_pause_system_principal_without_pod_is_silent() -> None:
    session = AsyncMock()
    row = _project()
    with patch(
        "prodavan.application.pod_service.command.build_pod_runtime",
        return_value=MagicMock(),
    ):
        cmd = ProjectCommand(session)
    cmd._access.get_project = AsyncMock(return_value=row)
    cmd._get_live_pod = AsyncMock(return_value=None)
    cmd._stop_and_pause_runtime = AsyncMock()
    cmd._project_public = AsyncMock(return_value={"id": row.id, "status": row.status})

    out = await cmd.pause(
        project_id=row.id,
        principal=Principal(sub="system:jobs", roles=frozenset({"platform.admin"})),
        employee=None,
        skip_access=True,
    )

    cmd._stop_and_pause_runtime.assert_not_awaited()
    assert out["id"] == row.id


@pytest.mark.asyncio
async def test_pause_user_principal_without_pod_raises_422() -> None:
    session = AsyncMock()
    row = _project()
    with patch(
        "prodavan.application.pod_service.command.build_pod_runtime",
        return_value=MagicMock(),
    ):
        cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)
    cmd._get_live_pod = AsyncMock(return_value=None)

    with pytest.raises(AppError) as exc:
        await cmd.pause(project_id=row.id, principal=_principal(), employee=None)

    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_idle_pause_sweep_continues_when_one_project_fails() -> None:
    from datetime import timedelta

    from prodavan.application.project_service.idle_pause import ProjectIdlePauseService

    session = AsyncMock()
    with patch(
        "prodavan.application.pod_service.command.build_pod_runtime",
        return_value=MagicMock(),
    ):
        svc = ProjectIdlePauseService(session)

    svc._companies = MagicMock()
    svc._companies.get_agent_policy = AsyncMock(
        return_value=MagicMock(idle_pause_enabled=lambda: True, idle_pause_after_hours=1)
    )
    projects = [_project(), _project()]
    proj_q = MagicMock()
    proj_q.scalars.return_value.all.return_value = projects
    session.execute = AsyncMock(return_value=proj_q)
    svc._last_activity_at = AsyncMock(
        return_value=datetime.now(UTC) - timedelta(hours=48)
    )
    svc._commands = MagicMock()
    svc._commands.pause = AsyncMock(
        side_effect=[RuntimeError("pause boom"), {"id": projects[1].id}]
    )

    result = await svc.sweep_company("cmp_test1234567890")

    assert result["count"] == 1
    assert result["paused"][0]["project_id"] == projects[1].id
    assert svc._commands.pause.await_count == 2
