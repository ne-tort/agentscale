"""Unit tests for pod lifecycle events and relations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.project_service.command import ProjectCommand
from prodavan.domain.identity import Principal
from prodavan.domain.projects import PLATFORM_EVENT_TYPES, ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectRow


def test_platform_event_types_include_pod_events() -> None:
    for event in (
        "pod.provisioned",
        "pod.started",
        "pod.paused",
        "pod.terminated",
        "pod.failed",
        "project.failed",
        "project.recovered",
    ):
        assert event in PLATFORM_EVENT_TYPES


def test_platform_event_types_drop_runtime_unit_events() -> None:
    assert "project.runtime_unit.attached" not in PLATFORM_EVENT_TYPES
    assert "project.runtime_unit.detached" not in PLATFORM_EVENT_TYPES


@pytest.mark.asyncio
async def test_pause_emits_pod_before_project_event() -> None:
    session = AsyncMock()
    row = ProjectRow(
        id="prj_test1234567890",
        company_id="cmp_test1234567890",
        cabinet_id="cab_test1234567890",
        owner_employee_id="emp_test1234567890",
        name="Demo",
        slug="demo",
        status=ProjectStatus.ACTIVE,
        visibility_mode="cabinet_shared",
        workspace_key="wk_demo",
        container_ref="object-ws:wk_demo",
    )
    principal = Principal(sub="emp:test", roles=frozenset({"employee"}))

    with patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=MagicMock()):
        cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)
    cmd._stop_and_pause_runtime = AsyncMock()
    cmd._events.emit = AsyncMock()
    cmd._query._project_public = AsyncMock(return_value={"id": row.id})

    await cmd.pause(project_id=row.id, principal=principal, employee=None)

    cmd._stop_and_pause_runtime.assert_awaited_once()
    cmd._events.emit.assert_awaited_once()
    assert cmd._events.emit.await_args.kwargs["event_type"] == "project.paused"


@pytest.mark.asyncio
async def test_bind_pod_to_project_publishes_relation_granted() -> None:
    from prodavan.application.relations.commands import RelationsCommand

    session = AsyncMock()
    relations = RelationsCommand(session)
    relations._publish = AsyncMock()

    await relations.bind_pod_to_project(
        pod_id="pod_abc",
        project_id="prj_test1234567890",
        company_id="cmp_test1234567890",
    )

    relations._publish.assert_awaited_once()
    assert relations._publish.await_args.kwargs["event_type"] == "relation.granted"
