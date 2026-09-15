"""Unit tests — chat sidebar selection, pins, title defaults."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.chat_sidebar_service import ChatSidebarService
from prodavan.application.agent.session_service import _default_chat_title, _touch_session_activity
from prodavan.domain.agent import AgentSessionStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow


def _principal() -> Principal:
    return Principal(sub="u1")


def _employee() -> MagicMock:
    emp = MagicMock()
    emp.id = "emp_1"
    return emp


def _project(*, cabinet_id: str = "cab_1", status: str = "active") -> MagicMock:
    p = MagicMock()
    p.id = "proj_1"
    p.cabinet_id = cabinet_id
    p.name = "Alpha"
    p.status = status
    return p


def _drafts_result(session_ids: list[str] | None = None) -> MagicMock:
    """Mock for ComposerDraftService.draft_session_ids_for_employee execute."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(session_ids or [])
    return result


@pytest.mark.asyncio
async def test_get_selection_empty() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.get_selection(
            cabinet_id="cab_1", principal=_principal(), employee=_employee()
        )
    assert out == {"cabinet_id": "cab_1", "project_id": None}


@pytest.mark.asyncio
async def test_set_selection_creates_row() -> None:
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[_project(), None])
    session.commit = AsyncMock()
    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.set_selection(
            cabinet_id="cab_1",
            project_id="proj_1",
            principal=_principal(),
            employee=_employee(),
        )
    assert out["project_id"] == "proj_1"
    session.add.assert_called_once()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_set_selection_rejects_foreign_project() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=_project(cabinet_id="other"))
    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        with pytest.raises(AppError) as ei:
            await svc.set_selection(
                cabinet_id="cab_1",
                project_id="proj_1",
                principal=_principal(),
                employee=_employee(),
            )
    assert ei.value.status == 404


@pytest.mark.asyncio
async def test_sidebar_sorts_pinned_and_project_chats() -> None:
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project()
    older = AgentSessionRow(
        id="ags_old",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_old",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
        title="Old",
        last_message_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = AgentSessionRow(
        id="ags_new",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_new",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
        title="New",
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    pin = MagicMock()
    pin.session_id = "ags_old"

    projects_result = MagicMock()
    projects_result.scalars.return_value.all.return_value = [proj]
    pins_result = MagicMock()
    pins_result.scalars.return_value.all.return_value = [pin]
    pinned_sess = MagicMock()
    pinned_sess.scalars.return_value.all.return_value = [older]
    project_sess = MagicMock()
    project_sess.scalars.return_value.all.return_value = [older, newer]
    session.get = AsyncMock(return_value=sel)
    # projects → pins → drafts → pinned sessions → project sessions
    session.execute = AsyncMock(
        side_effect=[
            projects_result,
            pins_result,
            _drafts_result(),
            pinned_sess,
            project_sess,
        ]
    )

    svc = ChatSidebarService(session)
    with (
        patch.object(svc._cabinets, "require_access", new=AsyncMock()),
        patch(
            "prodavan.application.agent.chat_sidebar_service.PodQuery"
        ) as pod_cls,
    ):
        pod_cls.return_value.runtime_view = AsyncMock(
            return_value={"observed_state": "running"}
        )
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["new_chat_enabled"] is True
    assert out["observed_state"] == "running"
    assert out["selected_project_id"] == "proj_1"
    assert [c["session_id"] for c in out["pinned"]] == ["ags_old"]
    assert [c["session_id"] for c in out["project_chats"]] == ["ags_new"]


@pytest.mark.asyncio
async def test_sidebar_new_chat_disabled_unless_container_running() -> None:
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project(status="active")
    projects_result = MagicMock()
    projects_result.scalars.return_value.all.return_value = [proj]
    pins_result = MagicMock()
    pins_result.scalars.return_value.all.return_value = []
    project_sess = MagicMock()
    project_sess.scalars.return_value.all.return_value = []
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[projects_result, pins_result, _drafts_result(), project_sess]
    )

    svc = ChatSidebarService(session)
    with (
        patch.object(svc._cabinets, "require_access", new=AsyncMock()),
        patch(
            "prodavan.application.agent.chat_sidebar_service.PodQuery"
        ) as pod_cls,
    ):
        pod_cls.return_value.runtime_view = AsyncMock(
            return_value={"observed_state": "starting"}
        )
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["new_chat_enabled"] is False
    assert out["observed_state"] == "starting"


@pytest.mark.asyncio
async def test_sidebar_new_chat_disabled_for_error_project() -> None:
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project(status="error")
    projects_result = MagicMock()
    projects_result.scalars.return_value.all.return_value = [proj]
    pins_result = MagicMock()
    pins_result.scalars.return_value.all.return_value = []
    project_sess = MagicMock()
    project_sess.scalars.return_value.all.return_value = []
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[projects_result, pins_result, _drafts_result(), project_sess]
    )

    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["new_chat_enabled"] is False
    assert out["observed_state"] is None


@pytest.mark.asyncio
async def test_set_pin_adds_and_removes() -> None:
    session = AsyncMock()
    emp = _employee()
    sess_row = AgentSessionRow(
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
    session.get = AsyncMock(side_effect=[sess_row, _project(), None])
    session.commit = AsyncMock()
    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.set_pin(
            session_id="ags_1", pinned=True, principal=_principal(), employee=emp
        )
    assert out == {"session_id": "ags_1", "pinned": True}
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_get_transcript_requires_session_id() -> None:
    from prodavan.application.agent.session_service import AgentSessionService

    session = AsyncMock()
    svc = AgentSessionService(session)
    with patch.object(svc._projects, "require_access", new=AsyncMock()):
        with pytest.raises(AppError) as ei:
            await svc.get_transcript(
                project_id="proj_1",
                principal=_principal(),
                employee=_employee(),
                session_id="",
            )
    assert ei.value.status == 400
    assert "session_id" in (ei.value.detail or "")


@pytest.mark.asyncio
async def test_sidebar_keeps_young_empty_shells_from_gc() -> None:
    """Empty shells mid-send/settings must not be deleted on sidebar reload."""
    from prodavan.application.agent.chat_sidebar_service import _EMPTY_SHELL_GC_GRACE

    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project()
    young = AgentSessionRow(
        id="ags_young",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_young",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
        title="Young",
        last_message_at=None,
        created_at=datetime.now(tz=UTC) - (_EMPTY_SHELL_GC_GRACE / 2),
    )
    old = AgentSessionRow(
        id="ags_old_empty",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_old_empty",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
        title="Old empty",
        last_message_at=None,
        created_at=datetime.now(tz=UTC) - (_EMPTY_SHELL_GC_GRACE * 2),
    )
    projects_result = MagicMock()
    projects_result.scalars.return_value.all.return_value = [proj]
    pins_result = MagicMock()
    pins_result.scalars.return_value.all.return_value = []
    project_sess = MagicMock()
    project_sess.scalars.return_value.all.return_value = [young, old]
    no_events = MagicMock()
    no_events.scalar_one_or_none.return_value = None
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            projects_result,
            pins_result,
            _drafts_result(),
            project_sess,
            no_events,  # event check for old empty shell
        ]
    )
    session.commit = AsyncMock()

    svc = ChatSidebarService(session)
    with (
        patch.object(svc._cabinets, "require_access", new=AsyncMock()),
        patch(
            "prodavan.application.agent.chat_sidebar_service.PodQuery"
        ) as pod_cls,
    ):
        pod_cls.return_value.runtime_view = AsyncMock(
            return_value={"observed_state": "running"}
        )
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["project_chats"] == []
    deleted_ids = [c.args[0].id for c in session.delete.await_args_list]
    assert "ags_old_empty" in deleted_ids
    assert "ags_young" not in deleted_ids


def test_default_chat_title_truncates() -> None:
    assert _default_chat_title("hi") == "hi"
    long = "x" * 100
    assert len(_default_chat_title(long)) <= 60


def test_touch_session_sets_title_once() -> None:
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
    _touch_session_activity(row, text="Hello world")
    assert row.title == "Hello world"
    assert row.last_message_at is not None
    first_at = row.last_message_at
    _touch_session_activity(row, text="Second message should not rename")
    assert row.title == "Hello world"
    assert row.last_message_at >= first_at
