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


def _project(
    *,
    pid: str = "proj_1",
    name: str = "Alpha",
    cabinet_id: str = "cab_1",
    status: str = "active",
) -> MagicMock:
    p = MagicMock()
    p.id = pid
    p.cabinet_id = cabinet_id
    p.name = name
    p.status = status
    return p


def _session_row(
    *,
    sid: str,
    project_id: str = "proj_1",
    title: str | None = None,
    last_message_at: datetime | None = None,
    created_at: datetime | None = None,
) -> AgentSessionRow:
    return AgentSessionRow(
        id=sid,
        project_id=project_id,
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id=sid,
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
        title=title,
        last_message_at=last_message_at,
        created_at=created_at,
    )


def _pod_row(
    *,
    project_id: str,
    status: str = "running",
    desired_state: str = "running",
) -> MagicMock:
    pod = MagicMock()
    pod.project_id = project_id
    pod.status = status
    pod.desired_state = desired_state
    return pod


def _rows_result(rows: list) -> MagicMock:
    """Mock for a scalars().all() execute result."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    return result


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
    assert out == {"cabinet_id": "cab_1", "project_id": None, "chat_session_id": None}


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

    projects_result = _rows_result([proj])
    pins_result = _rows_result([pin])
    pinned_sess = _rows_result([older])
    project_sess = _rows_result([older, newer])
    session.get = AsyncMock(return_value=sel)
    # projects → pins → drafts → pinned sessions → pods batch → project sessions
    session.execute = AsyncMock(
        side_effect=[
            projects_result,
            pins_result,
            _drafts_result(),
            pinned_sess,
            _rows_result([]),
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
    # Tree: pinned chat stays inside its project branch, pinned first.
    assert [g["project_id"] for g in out["projects"]] == ["proj_1"]
    branch = out["projects"][0]
    assert [c["session_id"] for c in branch["chats"]] == ["ags_old", "ags_new"]
    assert branch["chats"][0]["pinned"] is True
    assert branch["chats"][1]["pinned"] is False
    assert branch["new_chat_enabled"] is True


@pytest.mark.asyncio
async def test_sidebar_new_chat_disabled_unless_container_running() -> None:
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project(status="active")
    projects_result = _rows_result([proj])
    pins_result = _rows_result([])
    project_sess = _rows_result([])
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            projects_result,
            pins_result,
            _drafts_result(),
            _rows_result([]),
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
            return_value={"observed_state": "starting"}
        )
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["new_chat_enabled"] is False
    assert out["observed_state"] == "starting"
    assert out["projects"][0]["new_chat_enabled"] is False


@pytest.mark.asyncio
async def test_sidebar_new_chat_disabled_for_error_project() -> None:
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project(status="error")
    projects_result = _rows_result([proj])
    pins_result = _rows_result([])
    project_sess = _rows_result([])
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[projects_result, pins_result, _drafts_result(), project_sess]
    )

    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["new_chat_enabled"] is False
    assert out["observed_state"] is None
    # Error project is not running → no branch in the tree at all.
    assert out["projects"] == []
    assert out["project_chats"] == []


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
    projects_result = _rows_result([proj])
    pins_result = _rows_result([])
    project_sess = _rows_result([young, old])
    no_events = MagicMock()
    no_events.scalar_one_or_none.return_value = None
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            projects_result,
            pins_result,
            _drafts_result(),
            _rows_result([]),
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
    assert out["projects"][0]["chats"] == []
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


@pytest.mark.asyncio
async def test_sidebar_projects_tree_contains_only_running_projects() -> None:
    """Tree has one branch per RUNNING project — not only the selected one;
    draft/paused/error/completed/deleted projects stay out until launched."""
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj1 = _project(pid="proj_1", name="Alpha")
    proj2 = _project(pid="proj_2", name="Beta", status="paused")
    deleted = _project(pid="proj_del", name="Gone", status="deleted")

    chat1 = _session_row(
        sid="ags_1",
        project_id="proj_1",
        title="A1",
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    chat2 = _session_row(
        sid="ags_2",
        project_id="proj_2",
        title="B1",
        last_message_at=datetime(2026, 5, 1, tzinfo=UTC),
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )

    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([proj1, proj2, deleted]),  # projects
            _rows_result([]),  # pins
            _drafts_result(),  # drafts
            _rows_result([]),  # pods batch
            _rows_result([chat1, chat2]),  # sessions of all projects
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

    # Branches: RUNNING projects only — paused/draft/completed/deleted stay
    # out of the tree, together with their chats.
    assert [g["project_id"] for g in out["projects"]] == ["proj_1"]
    by_id = {g["project_id"]: g for g in out["projects"]}
    assert by_id["proj_1"]["project_name"] == "Alpha"
    assert by_id["proj_1"]["status"] == "active"
    assert [c["session_id"] for c in by_id["proj_1"]["chats"]] == ["ags_1"]
    # Legacy fields keep their old shape.
    assert out["project_ids_in_cabinet"] == ["proj_1", "proj_2", "proj_del"]
    assert [c["session_id"] for c in out["project_chats"]] == ["ags_1"]
    assert out["pinned"] == []


@pytest.mark.asyncio
async def test_sidebar_selected_paused_project_keeps_legacy_chats() -> None:
    """Paused selection: no tree branch, but the legacy project_chats field
    still lists its chats (and new chats stay disabled)."""
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project(pid="proj_1", name="Alpha", status="paused")
    chat = _session_row(
        sid="ags_1",
        project_id="proj_1",
        title="A1",
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([proj]),  # projects
            _rows_result([]),  # pins
            _drafts_result(),  # drafts
            _rows_result([chat]),  # sessions (selected project outside the tree)
        ]
    )

    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["projects"] == []  # paused → hidden from the tree
    assert out["new_chat_enabled"] is False
    assert out["observed_state"] is None
    assert [c["session_id"] for c in out["project_chats"]] == ["ags_1"]


@pytest.mark.asyncio
async def test_sidebar_branch_sorts_pinned_first_then_recency() -> None:
    """Within a branch: pinned chats first (recency), then the rest by recency."""
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_1"
    proj = _project()
    pinned_chat = _session_row(
        sid="ags_pin",
        project_id="proj_1",
        title="Pinned",
        last_message_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = _session_row(
        sid="ags_new",
        project_id="proj_1",
        title="New",
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    older = _session_row(
        sid="ags_mid",
        project_id="proj_1",
        title="Mid",
        last_message_at=datetime(2026, 3, 1, tzinfo=UTC),
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    pin = MagicMock()
    pin.session_id = "ags_pin"

    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([proj]),  # projects
            _rows_result([pin]),  # pins
            _drafts_result(),  # drafts
            _rows_result([pinned_chat]),  # pinned sessions
            _rows_result([]),  # pods batch
            _rows_result([pinned_chat, newer, older]),  # sessions
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

    branch = out["projects"][0]
    assert [c["session_id"] for c in branch["chats"]] == ["ags_pin", "ags_new", "ags_mid"]
    assert branch["chats"][0]["pinned"] is True
    # Pinned chat is NOT removed from its project branch…
    assert any(c["session_id"] == "ags_pin" for c in branch["chats"])
    # …while the legacy project_chats field still excludes pins.
    assert [c["session_id"] for c in out["project_chats"]] == ["ags_new", "ags_mid"]
    assert [c["session_id"] for c in out["pinned"]] == ["ags_pin"]


@pytest.mark.asyncio
async def test_sidebar_branch_new_chat_requires_active_and_running_pod() -> None:
    """Per-branch new_chat_enabled: running container only (the tree itself
    holds active projects only)."""
    session = AsyncMock()
    emp = _employee()
    sel = MagicMock()
    sel.project_id = "proj_sel"
    projects = [
        _project(pid="proj_sel", name="Sel"),
        _project(pid="proj_ok", name="Ok"),
        _project(pid="proj_paused", name="Paused", status="paused"),
        _project(pid="proj_prov", name="Prov"),
        _project(pid="proj_nopod", name="NoPod"),
    ]

    session.get = AsyncMock(return_value=sel)
    session.execute = AsyncMock(
        side_effect=[
            _rows_result(projects),  # projects
            _rows_result([]),  # pins
            _drafts_result(),  # drafts
            _rows_result(
                [
                    _pod_row(project_id="proj_sel"),
                    _pod_row(project_id="proj_ok"),
                    _pod_row(project_id="proj_prov", status="provisioning"),
                ]
            ),  # pods batch (no live pod for proj_nopod)
            _rows_result([]),  # sessions
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

    by_id = {g["project_id"]: g for g in out["projects"]}
    assert set(by_id) == {"proj_sel", "proj_ok", "proj_prov", "proj_nopod"}
    assert by_id["proj_sel"]["new_chat_enabled"] is True  # active + observed running
    assert by_id["proj_ok"]["new_chat_enabled"] is True  # active + live pod running
    assert by_id["proj_prov"]["new_chat_enabled"] is False  # pod not running yet
    assert by_id["proj_nopod"]["new_chat_enabled"] is False  # no live pod row
    # Paused project is not running → no branch at all.
    assert "proj_paused" not in by_id
    # Global gate mirrors the selected project.
    assert out["new_chat_enabled"] is True


@pytest.mark.asyncio
async def test_sidebar_selected_project_absent_keeps_tree() -> None:
    """No selection (or selection outside the cabinet) — tree still lists projects."""
    session = AsyncMock()
    emp = _employee()
    proj = _project(pid="proj_1", name="Alpha")
    chat = _session_row(
        sid="ags_1",
        project_id="proj_1",
        title="A1",
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([proj]),  # projects
            _rows_result([]),  # pins
            _drafts_result(),  # drafts
            _rows_result([]),  # pods batch
            _rows_result([chat]),  # sessions
        ]
    )

    svc = ChatSidebarService(session)
    with patch.object(svc._cabinets, "require_access", new=AsyncMock()):
        out = await svc.sidebar(cabinet_id="cab_1", principal=_principal(), employee=emp)

    assert out["selected_project_id"] is None
    assert out["new_chat_enabled"] is False
    assert [c["session_id"] for c in out["projects"][0]["chats"]] == ["ags_1"]
    assert out["project_chats"] == []
    assert out["projects"][0]["new_chat_enabled"] is False
