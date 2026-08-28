"""Unit tests for PodCommand."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow


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
async def test_sync_desired_running_creates_and_starts_pod() -> None:
    session = AsyncMock()
    project = _project()
    session.get = AsyncMock(return_value=project)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=execute_result)

    runtime = AsyncMock()
    events = AsyncMock(spec=PodLifecycleEmitter)
    relations = AsyncMock()
    relations.bind_pod_to_project = AsyncMock()

    cmd = PodCommand(session, runtime=runtime, events=events)
    cmd._relations = relations

    await cmd.sync_desired(
        project.id,
        PodDesiredState.RUNNING,
        principal=_principal(),
        reason="test",
    )

    runtime.ensure_running.assert_awaited_once()
    events.emit.assert_awaited()
    assert events.emit.await_args.kwargs["event_type"] == "pod.started"


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
