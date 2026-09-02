"""Unit tests — suspend/reactivate agent sessions on project pause/resume."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.domain.agent import AgentSessionStatus
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow


@pytest.mark.asyncio
async def test_suspend_active_for_project() -> None:
    session = AsyncMock()
    row = AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_1",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [row]
    session.execute = AsyncMock(return_value=result_mock)

    count = await AgentSessionService(session).suspend_active_for_project(project_id="proj_1")
    assert count == 1
    assert row.status == AgentSessionStatus.SUSPENDED


@pytest.mark.asyncio
async def test_reactivate_resumable_for_project() -> None:
    session = AsyncMock()
    row = AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_1",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.SUSPENDED,
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [row]
    session.execute = AsyncMock(return_value=result_mock)

    ids = await AgentSessionService(session).reactivate_resumable_for_project(project_id="proj_1")
    assert ids == ["ags_1"]
    assert row.status == AgentSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_cancel_resumable_for_project() -> None:
    session = AsyncMock()
    active = AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_1",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
    )
    suspended = AgentSessionRow(
        id="ags_2",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_2",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.SUSPENDED,
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [active, suspended]
    session.execute = AsyncMock(return_value=result_mock)

    with patch(
        "prodavan.application.agent.session_service.get_agent_adapter",
        return_value=AsyncMock(),
    ):
        count = await AgentSessionService(session).cancel_resumable_for_project(project_id="proj_1")

    assert count == 2
    assert active.status == AgentSessionStatus.CANCELLED
    assert suspended.status == AgentSessionStatus.CANCELLED


@pytest.mark.asyncio
async def test_resolve_sendable_reactivates_suspended_session() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    row = AgentSessionRow(
        id="ags_old",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_old",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.SUSPENDED,
    )
    svc.get_session = AsyncMock(return_value=row)  # type: ignore[method-assign]
    session.flush = AsyncMock()

    sid = await svc._resolve_sendable_session_id(
        project_id="proj_1",
        session_id="ags_old",
        principal=MagicMock(),
        employee=MagicMock(),
        model=None,
    )
    assert sid == "ags_old"
    assert row.status == AgentSessionStatus.ACTIVE
