"""Reload-mid-turn transcript: the tail page keeps its user prompt."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentSessionStatus
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow


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
        status=AgentSessionStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_mid_turn_tail_prepends_the_current_turn_user_prompt() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    svc.get_session = AsyncMock(return_value=_session_row())  # type: ignore[method-assign]
    svc.turn_in_progress = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc._list_events_page = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            [
                {
                    "seq": 500,
                    "type": "tool_result",
                    "data": {"id": "t1", "name": "edit", "output": "ok"},
                }
            ],
            {"oldest_seq": 500, "newest_seq": 600, "has_more": True, "total_events": 600},
        )
    )
    user_row = AgentEventRow(
        id="aev_499",
        session_id="ags_1",
        seq=499,
        event_type=PLATFORM_EVENT_USER_MESSAGE,
        payload={"text": "исходный запрос"},
        at=datetime.now(tz=UTC),
    )
    user_result = MagicMock()
    user_result.scalars.return_value.first.return_value = user_row
    session.execute = AsyncMock(return_value=user_result)

    out = await svc.get_transcript(
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        session_id="ags_1",
        limit=500,
    )

    assert out["blocks"][0]["kind"] == "user"
    assert out["blocks"][0]["text"] == "исходный запрос"
    # Pagination cursor moves to the prepended event, so «load older» never
    # returns the same user message twice.
    assert out["oldest_seq"] == 499
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_tail_page_with_a_user_prompt_does_not_query_again() -> None:
    session = AsyncMock()
    svc = AgentSessionService(session)
    svc._projects.require_access = AsyncMock()  # type: ignore[method-assign]
    svc.get_session = AsyncMock(return_value=_session_row())  # type: ignore[method-assign]
    svc.turn_in_progress = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc._list_events_page = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            [
                {"seq": 1, "type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "привет"}},
                {"seq": 2, "type": "text_delta", "data": {"text": "ответ"}},
            ],
            {"oldest_seq": 1, "newest_seq": 2, "has_more": False, "total_events": 2},
        )
    )
    session.execute = AsyncMock()

    out = await svc.get_transcript(
        project_id="proj_1",
        principal=MagicMock(),
        employee=MagicMock(),
        session_id="ags_1",
    )

    assert [b["kind"] for b in out["blocks"]] == ["user", "assistant_markdown"]
    session.execute.assert_not_awaited()
