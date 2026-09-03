"""Unit tests — agent event pagination (tail / before_seq)."""

from __future__ import annotations

from datetime import UTC, datetime
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
