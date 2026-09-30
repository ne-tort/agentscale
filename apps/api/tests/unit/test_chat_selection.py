"""Chat-selection persistence regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.chat_sidebar_service import ChatSidebarService


def _service(row: Any = None) -> ChatSidebarService:
    svc = ChatSidebarService.__new__(ChatSidebarService)
    svc._session = MagicMock()
    svc._session.get = AsyncMock(return_value=row)
    svc._session.add = MagicMock()
    svc._session.commit = AsyncMock()
    svc._cabinets = MagicMock()
    svc._cabinets.require_access = AsyncMock()
    return svc


def _principal() -> Any:
    return MagicMock()


def _employee() -> Any:
    return SimpleNamespace(id="emp_1")


def _session_row(project_id: str = "proj_1", chat_id: str | None = "ags_1") -> Any:
    return SimpleNamespace(id="ags_1", project_id=project_id)


def _project_row(cabinet_id: str = "cab_1") -> Any:
    return SimpleNamespace(id="proj_1", cabinet_id=cabinet_id)


@pytest.mark.asyncio
async def test_get_selection_returns_chat() -> None:
    row = SimpleNamespace(project_id="proj_1", chat_session_id="ags_9")
    out = await _service(row).get_selection(
        cabinet_id="cab_1", principal=_principal(), employee=_employee()
    )
    assert out == {"cabinet_id": "cab_1", "project_id": "proj_1", "chat_session_id": "ags_9"}


@pytest.mark.asyncio
async def test_chat_only_update_keeps_project() -> None:
    svc = _service(SimpleNamespace(project_id="proj_1", chat_session_id=None))
    svc._session.get = AsyncMock(side_effect=[_session_row(), _project_row(), SimpleNamespace(project_id="proj_1", chat_session_id=None)])
    out = await svc.set_selection(
        cabinet_id="cab_1",
        project_id=None,
        chat_session_id="ags_1",
        update_chat=True,
        principal=_principal(),
        employee=_employee(),
    )
    assert out["project_id"] == "proj_1"  # untouched
    assert out["chat_session_id"] == "ags_1"


@pytest.mark.asyncio
async def test_project_switch_resets_chat() -> None:
    svc = _service(SimpleNamespace(project_id="proj_1", chat_session_id="ags_1"))
    svc._session.get = AsyncMock(side_effect=[_project_row(), SimpleNamespace(project_id="proj_1", chat_session_id="ags_1")])
    out = await svc.set_selection(
        cabinet_id="cab_1",
        project_id="proj_2",
        principal=_principal(),
        employee=_employee(),
    )
    assert out["project_id"] == "proj_2"
    assert out["chat_session_id"] is None  # old chat reset


@pytest.mark.asyncio
async def test_foreign_cabinet_chat_rejected() -> None:
    svc = _service(SimpleNamespace(project_id="proj_1", chat_session_id=None))
    svc._session.get = AsyncMock(side_effect=[_session_row(), _project_row("cab_OTHER")])
    with pytest.raises(Exception) as err:
        await svc.set_selection(
            cabinet_id="cab_1",
            project_id=None,
            chat_session_id="ags_1",
            update_chat=True,
            principal=_principal(),
            employee=_employee(),
        )
    assert "cabinet" in str(err.value).lower()
