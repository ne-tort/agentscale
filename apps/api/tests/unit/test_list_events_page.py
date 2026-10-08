"""Unit tests — agent event pagination (tail / before_seq / after_seq)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow


def _event_row(session_id: str, seq: int) -> AgentEventRow:
    return AgentEventRow(
        id=f"aev_{seq}",
        session_id=session_id,
        seq=seq,
        event_type="text_delta",
        payload={"text": f"chunk-{seq}"},
        at=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_list_events_page_tail_returns_last_n_in_order() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    svc.get_session = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentSessionRow(
            id="ags_1",
            project_id="proj_1",
            resolved_key_id=None,
            provider="cursor",
            api_kind="cursor_sdk",
            vendor_agent_id="ags_1",
            model=None,
            cwd="/workspace",
            status="active",
        )
    )
    rows = [_event_row("ags_1", seq) for seq in (7, 6, 5, 4)]
    page_mock = MagicMock()
    page_mock.scalars.return_value.all.return_value = rows
    count_mock = MagicMock()
    count_mock.scalar_one.return_value = 42
    session.execute = AsyncMock(side_effect=[page_mock, count_mock])

    events, meta = await svc._list_events_page(
        session_id="ags_1",
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        limit=3,
        tail=True,
    )

    assert [e["seq"] for e in events] == [5, 6, 7]
    assert meta["oldest_seq"] == 5
    assert meta["newest_seq"] == 7
    assert meta["has_more"] is True
    assert meta["total_events"] == 42


@pytest.mark.asyncio
async def test_list_events_page_before_seq_returns_older_window() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    svc.get_session = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentSessionRow(
            id="ags_1",
            project_id="proj_1",
            resolved_key_id=None,
            provider="cursor",
            api_kind="cursor_sdk",
            vendor_agent_id="ags_1",
            model=None,
            cwd="/workspace",
            status="active",
        )
    )
    rows = [_event_row("ags_1", seq) for seq in (4, 3, 2)]
    page_mock = MagicMock()
    page_mock.scalars.return_value.all.return_value = rows
    count_mock = MagicMock()
    count_mock.scalar_one.return_value = 10
    session.execute = AsyncMock(side_effect=[page_mock, count_mock])

    events, meta = await svc._list_events_page(
        session_id="ags_1",
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        limit=2,
        before_seq=5,
    )

    assert [e["seq"] for e in events] == [3, 4]
    assert meta["oldest_seq"] == 3
    assert meta["newest_seq"] == 4
    assert meta["has_more"] is True
    assert meta["total_events"] == 10


@pytest.mark.asyncio
async def test_list_events_page_skips_total_count_when_disabled() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    svc.get_session = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentSessionRow(
            id="ags_1",
            project_id="proj_1",
            resolved_key_id=None,
            provider="cursor",
            api_kind="cursor_sdk",
            vendor_agent_id="ags_1",
            model=None,
            cwd="/workspace",
            status="active",
        )
    )
    rows = [_event_row("ags_1", seq) for seq in (4, 3)]
    page_mock = MagicMock()
    page_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=page_mock)

    _events, meta = await svc._list_events_page(
        session_id="ags_1",
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        limit=2,
        before_seq=5,
        include_total_count=False,
    )

    assert meta["oldest_seq"] == 3
    assert meta["newest_seq"] == 4
    assert "total_events" not in meta
    assert session.execute.await_count == 1


def _session_row() -> AgentSessionRow:
    return AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_1",
        model=None,
        cwd="/workspace",
        status="active",
    )


@pytest.mark.asyncio
async def test_list_events_page_after_seq_returns_newer_ascending() -> None:
    """Incremental tail: only events strictly newer than the cursor, ascending."""
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    svc.get_session = AsyncMock(return_value=_session_row())  # type: ignore[method-assign]
    rows = [_event_row("ags_1", seq) for seq in (5, 6, 7)]
    page_mock = MagicMock()
    page_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=page_mock)

    events, meta = await svc._list_events_page(
        session_id="ags_1",
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        limit=10,
        after_seq=4,
        include_total_count=False,
    )

    assert [e["seq"] for e in events] == [5, 6, 7]
    assert meta["newest_seq"] == 7
    assert meta["has_more"] is False
    assert "total_events" not in meta


@pytest.mark.asyncio
async def test_turn_in_progress_true_for_non_terminal_tail() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    result_mock = MagicMock()
    result_mock.first.return_value = ("text_delta", datetime.now(tz=UTC))
    session.execute = AsyncMock(return_value=result_mock)

    assert await svc.turn_in_progress("ags_1") is True


@pytest.mark.asyncio
async def test_turn_in_progress_false_for_terminal_tail() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    for event_type in ("done", "error"):
        result_mock = MagicMock()
        result_mock.first.return_value = (event_type, datetime.now(tz=UTC))
        session.execute = AsyncMock(return_value=result_mock)
        assert await svc.turn_in_progress("ags_1") is False


@pytest.mark.asyncio
async def test_turn_in_progress_false_when_empty_or_stale() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    empty = MagicMock()
    empty.first.return_value = None
    session.execute = AsyncMock(return_value=empty)
    assert await svc.turn_in_progress("ags_1") is False

    stale = MagicMock()
    stale.first.return_value = ("text_delta", datetime.now(tz=UTC) - timedelta(minutes=30))
    session.execute = AsyncMock(return_value=stale)
    assert await svc.turn_in_progress("ags_1") is False


@pytest.mark.asyncio
async def test_get_transcript_turn_in_progress_false_when_session_cancelled() -> None:
    """A stopped (CANCELLED) session must not report a turn in progress.

    Otherwise a reloaded client re-arms the Cancel button and polls forever
    even though the run was stopped.
    """
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    cancelled = _session_row()
    cancelled.status = "cancelled"
    svc.get_session = AsyncMock(return_value=cancelled)  # type: ignore[method-assign]
    # Non-terminal tail would normally read as "working".
    svc.turn_in_progress = AsyncMock(return_value=True)  # type: ignore[method-assign]
    page_mock = MagicMock()
    page_mock.scalars.return_value.all.return_value = []
    count_mock = MagicMock()
    count_mock.scalar_one.return_value = 0
    session.execute = AsyncMock(side_effect=[page_mock, count_mock])

    out = await svc.get_transcript(
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        session_id="ags_1",
    )

    assert out["turn_in_progress"] is False
