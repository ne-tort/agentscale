"""Unit tests for PodCommand."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
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


def _project() -> ProjectRow:
    return ProjectRow(
        id="prj_test1234567890",
        company_id="cmp_test1234567890",
        cabinet_id="cab_test1234567890",
        owner_employee_id="emp_test1234567890",
        name="Demo",
        slug="demo",
        status="active",
        visibility_mode="cabinet_shared",
        workspace_key="wk_demo",
        container_ref="object-ws:wk_demo",
    )


@pytest.mark.asyncio
async def test_sync_desired_normalizes_stale_stub_ref_in_sandbox_mode(monkeypatch) -> None:
    # Regression (launch 422): rows persisted in stub mode carry
    # ``object-ws:`` refs; the sandbox adapter treats runtime_ref as a
    # SandboxClaim name and k8s rejects it with 422 on create.
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_runtime_mode", "sandbox")
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.FAILED,
        desired_state=PodDesiredState.RUNNING,
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
    cmd = PodCommand(session, runtime=runtime, events=AsyncMock(spec=PodLifecycleEmitter))
    cmd._project_events = AsyncMock()

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="reconcile",
    )

    assert pod.runtime_ref == "sandbox-claim-wk-demo"
    assert project.container_ref == "sandbox-claim-wk-demo"
    runtime.ensure_running.assert_awaited_once()
    assert runtime.ensure_running.await_args.kwargs["runtime_ref"] == "sandbox-claim-wk-demo"


@pytest.mark.asyncio
async def test_sync_desired_keeps_legacy_ref_in_k8s_mode(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_runtime_mode", "k8s")
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
        runtime_ref="pod-wk-demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.get = AsyncMock(return_value=project)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = pod
    session.execute = AsyncMock(return_value=execute_result)

    runtime = AsyncMock()
    cmd = PodCommand(session, runtime=runtime, events=AsyncMock(spec=PodLifecycleEmitter))
    cmd._project_events = AsyncMock()

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="test",
    )

    assert pod.runtime_ref == "pod-wk-demo"


@pytest.mark.asyncio
async def test_sync_desired_running_creates_and_starts_pod() -> None:
    session = AsyncMock()
    project = _project()
    session.get = AsyncMock(return_value=project)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=execute_result)

    runtime = AsyncMock()
    hydrate = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    relations = AsyncMock()
    relations.bind_pod_to_project = AsyncMock()

    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=hydrate)
    cmd._relations = relations
    cmd._project_events = AsyncMock()

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="test",
    )

    runtime.ensure_running.assert_awaited_once()
    session.commit.assert_awaited()
    hydrate.hydrate.assert_awaited_once()
    assert events.emit.await_count >= 2
    event_types = [c.kwargs["event_type"] for c in events.emit.await_args_list]
    assert "pod.started" in event_types
    assert "pod.hydrated" in event_types


@pytest.mark.asyncio
async def test_sync_desired_absent_pauses_running_pod() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
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
    events = AsyncMock(spec=PodLifecycleEmitter)

    cmd = PodCommand(session, runtime=runtime, events=events)

    await cmd.sync_desired(
        project.id,
        PodDesiredState.ABSENT,
        principal=_principal(),
        reason="pause",
    )

    runtime.pause.assert_awaited_once_with(runtime_ref="object-ws:wk_demo")
    events.emit.assert_awaited()
    assert events.emit.await_args.kwargs["event_type"] == "pod.paused"


@pytest.mark.asyncio
async def test_sync_desired_idempotent_when_already_running() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
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
    events = AsyncMock(spec=PodLifecycleEmitter)

    cmd = PodCommand(session, runtime=runtime, events=events)

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
    )

    runtime.ensure_running.assert_not_awaited()
    events.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_desired_rematerialize_rehydrates_running_pod() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
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
    hydrate = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    env_loader = AsyncMock()
    env_loader.load_for_project = AsyncMock(return_value=())

    with patch(
        "prodavan.application.pod_service.command.ContainerEnvLoader",
        return_value=env_loader,
    ):
        cmd = PodCommand(session, runtime=runtime, events=events, hydrate=hydrate)

        await cmd.sync_desired(
            project.id,
            PodDesiredState.RUNNING,
            principal=_principal(),
            reason="rematerialize",
        )

    runtime.ensure_running.assert_awaited()
    hydrate.hydrate.assert_awaited()
    env_loader.load_for_project.assert_awaited()
    assert env_loader.load_for_project.await_args.kwargs["lifecycle"] == "project.sync"


@pytest.mark.asyncio
async def test_sync_desired_revives_failed_pod() -> None:
    session = AsyncMock()
    project = _project()
    failed = ProjectPodRow(
        id="pod_failed",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.FAILED,
        desired_state=PodDesiredState.ABSENT,
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
    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=AsyncMock())
    cmd._project_events = AsyncMock()

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="retry",
    )

    runtime.ensure_running.assert_awaited_once()
    assert failed.status == PodStatus.PROVISIONING
    assert failed.last_error is None


@pytest.mark.asyncio
async def test_sync_desired_delete_terminates_pod() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
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
    events = AsyncMock(spec=PodLifecycleEmitter)
    cmd = PodCommand(session, runtime=runtime, events=events)

    await cmd.sync_desired(
        project.id,
        PodDesiredState.ABSENT,
        principal=_principal(),
        reason="delete",
    )

    runtime.terminate.assert_awaited_once_with(runtime_ref="object-ws:wk_demo")
    assert pod.status == PodStatus.TERMINATED
    assert events.emit.await_args.kwargs["event_type"] == "pod.terminated"


@pytest.mark.asyncio
async def test_sync_desired_resume_emits_pod_resumed() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.PAUSED,
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
    events = AsyncMock(spec=PodLifecycleEmitter)
    cmd = PodCommand(session, runtime=runtime, events=events)

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="resume",
    )

    assert events.emit.await_args.kwargs["event_type"] == "pod.resumed"


@pytest.mark.asyncio
async def test_lazy_start_emits_project_started() -> None:
    session = AsyncMock()
    project = _project()
    session.get = AsyncMock(return_value=project)

    live_result = MagicMock()
    live_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=live_result)

    runtime = AsyncMock()
    hydrate = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    relations = AsyncMock()
    relations.bind_pod_to_project = AsyncMock()
    project_events = AsyncMock()

    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=hydrate)
    cmd._relations = relations
    cmd._project_events = project_events

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="lazy.start",
    )

    project_events.emit.assert_awaited_once()
    assert project_events.emit.await_args.kwargs["event_type"] == "project.started"


@pytest.mark.asyncio
async def test_sync_desired_reload_restarts_running_pod() -> None:
    session = AsyncMock()
    project = _project()
    pod = ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING.value,
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
    hydrate = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    store = AsyncMock()
    store.clear_project_latest = AsyncMock()

    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=hydrate)
    cmd._project_events = AsyncMock()

    with (
        patch(
            "prodavan.application.projects.workspace_checkpoint.checkpoint_project_workspace",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "prodavan.application.metrics.adapters.redis_metrics_store.build_metrics_store",
            return_value=store,
        ),
    ):
        await cmd.sync_desired(
            project.id,
            PodDesiredState.RUNNING,
            principal=_principal(),
            reason="reload",
        )

    runtime.terminate.assert_awaited_once_with(runtime_ref="object-ws:wk_demo")
    runtime.ensure_running.assert_awaited_once()
    store.clear_project_latest.assert_awaited_once_with(project.id)
    assert pod.status == PodStatus.PROVISIONING
    assert events.emit.await_args.kwargs["event_type"] == "pod.resumed"


@pytest.mark.asyncio
async def test_provision_reuses_failed_row() -> None:
    session = AsyncMock()
    project = _project()
    failed = ProjectPodRow(
        id="pod_failed",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.FAILED,
        desired_state=PodDesiredState.ABSENT.value,
        runtime_ref="object-ws:wk_demo",
        last_error="metrics-server unavailable",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.get = AsyncMock(return_value=project)

    events = AsyncMock(spec=PodLifecycleEmitter)
    cmd = PodCommand(session, runtime=AsyncMock(), events=events)
    cmd._get_live_row = AsyncMock(return_value=None)
    cmd._get_failed_row = AsyncMock(return_value=failed)

    out = await cmd.provision_for_project(project.id, principal=_principal(), start=False)

    assert out["id"] == failed.id
    assert failed.status == PodStatus.PENDING
    assert failed.last_error is None
    events.emit.assert_not_awaited()


def _running_pod(project) -> ProjectPodRow:
    return ProjectPodRow(
        id="pod_abc123",
        project_id=project.id,
        workspace_key=project.workspace_key,
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING.value,
        runtime_ref="object-ws:wk_demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _session_with(project, pod) -> AsyncMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=project)
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = pod
    session.execute = AsyncMock(return_value=execute_result)
    return session


@pytest.mark.asyncio
async def test_sync_desired_rematerialize_recreates_sandbox_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox: rematerialize обязан перевыпустить claim.

    Гидрация живого sandbox-пода заблокирована маркером `.hydrated` (bridge
    `hydrateOnBind` выходит сразу и ничего не перечитывает), а API-сторона в
    этом режиме гидрацию не делает вовсе (`build_hydrate` → StubHydrateAdapter).
    Без terminate+нового claim под продолжал ходить на ПРЕЖНИЙ provider
    endpoint и со старыми MCP-пакетами, хотя в БД всё уже новое — так после
    смены провайдера проекта валидный токен нового провайдера уезжал на чужой
    base_url и апстрим отвечал 401 «Invalid token».
    """
    monkeypatch.setattr("prodavan.config.settings.settings.pod_runtime_mode", "sandbox")
    project = _project()
    pod = _running_pod(project)
    session = _session_with(project, pod)

    runtime = AsyncMock()
    hydrate = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    store = AsyncMock()
    store.clear_project_latest = AsyncMock()
    env_loader = AsyncMock()
    env_loader.load_for_project = AsyncMock(return_value=())

    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=hydrate)
    cmd._project_events = AsyncMock()

    with (
        patch(
            "prodavan.application.pod_service.command.ContainerEnvLoader",
            return_value=env_loader,
        ),
        patch(
            "prodavan.application.projects.workspace_checkpoint.checkpoint_project_workspace",
            new=AsyncMock(return_value=None),
        ) as checkpoint,
        patch(
            "prodavan.application.metrics.adapters.redis_metrics_store.build_metrics_store",
            return_value=store,
        ),
    ):
        await cmd.sync_desired(
            project.id,
            PodDesiredState.RUNNING,
            principal=_principal(),
            reason="rematerialize",
        )

    # чекапоинт ДО terminate — файлы агента не теряются
    checkpoint.assert_awaited_once()
    # в sandbox-режиме runtime_ref нормализуется в claim-ссылку
    runtime.terminate.assert_awaited_once_with(runtime_ref="sandbox-claim-wk-demo")
    runtime.ensure_running.assert_awaited_once()
    hydrate.hydrate.assert_awaited()


@pytest.mark.asyncio
async def test_sync_desired_rematerialize_keeps_k8s_pod_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """k8s-адаптер пересоздаёт под сам по hydrate_generation — форс не нужен."""
    monkeypatch.setattr("prodavan.config.settings.settings.pod_runtime_mode", "k8s")
    project = _project()
    pod = _running_pod(project)
    session = _session_with(project, pod)

    runtime = AsyncMock()
    hydrate = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    env_loader = AsyncMock()
    env_loader.load_for_project = AsyncMock(return_value=())

    cmd = PodCommand(session, runtime=runtime, events=events, hydrate=hydrate)
    cmd._project_events = AsyncMock()

    with (
        patch(
            "prodavan.application.pod_service.command.ContainerEnvLoader",
            return_value=env_loader,
        ),
        patch(
            "prodavan.application.projects.workspace_checkpoint.checkpoint_project_workspace",
            new=AsyncMock(return_value=None),
        ) as checkpoint,
        patch(
            "prodavan.application.metrics.adapters.redis_metrics_store.build_metrics_store",
            return_value=AsyncMock(),
        ),
    ):
        await cmd.sync_desired(
            project.id,
            PodDesiredState.RUNNING,
            principal=_principal(),
            reason="rematerialize",
        )

    runtime.terminate.assert_not_awaited()
    checkpoint.assert_not_awaited()
    runtime.ensure_running.assert_awaited_once()
    hydrate.hydrate.assert_awaited()

