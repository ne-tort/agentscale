"""Unit tests — chat deletion cascade (chat-scoped module rows die with the chat)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.domain.agent import AgentSessionStatus
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow


def _row(status: AgentSessionStatus = AgentSessionStatus.SUSPENDED) -> AgentSessionRow:
    return AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        resolved_key_id=None,
        provider="openclaw",
        api_kind="openclaw_sdk",
        vendor_agent_id="ags_1",
        model=None,
        cwd="/workspace",
        status=status,
    )


class _FakeAdapter:
    async def close(self, handle):  # noqa: ANN001
        return None

    async def cancel(self, handle):  # noqa: ANN001
        return None


@pytest.mark.asyncio
async def test_delete_session_cascades_chat_scoped_module_rows() -> None:
    session = AsyncMock()
    executed: list[object] = []

    async def _execute(stmt, *args, **kwargs):  # noqa: ANN003
        executed.append(stmt)
        res = MagicMock()
        res.rowcount = 3
        res.scalars.return_value.first.return_value = None
        return res

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()

    service = AgentSessionService(session)

    async def _get_session(_self, *, session_id):  # noqa: ANN003
        assert session_id == "ags_1"
        return _row()

    with patch.object(
        AgentSessionService, "get_session", _get_session
    ), patch(
        "prodavan.application.agent.session_service.get_agent_adapter",
        return_value=_FakeAdapter(),
    ):
        out = await service.delete_session(
            project_id="proj_1",
            session_id="ags_1",
            principal=MagicMock(),
            employee=None,
        )

    assert out["ok"] is True
    assert out["module_rows_deleted"] == 3

    tables = []
    module_stmts = []
    for stmt in executed:
        name = getattr(getattr(stmt, "table", None), "name", None)
        tables.append(name)
        if name == "module_instance_data_rows":
            module_stmts.append(stmt)
    # chat-scoped module rows deleted in the same transaction
    assert "module_instance_data_rows" in tables
    assert "agent_events" in tables
    usage_tables = [t for t in tables if t and "usage" in t]
    assert usage_tables, tables
    assert "agent_sessions" in tables
    # module delete filters strictly by session_id (other chats untouched)
    assert len(module_stmts) == 1
    compiled = str(module_stmts[0])
    assert "session_id" in compiled


@pytest.mark.asyncio
async def test_delete_session_zero_module_rows_ok() -> None:
    session = AsyncMock()

    async def _execute(stmt, *args, **kwargs):  # noqa: ANN003
        res = MagicMock()
        res.rowcount = 0
        res.scalars.return_value.first.return_value = None
        return res

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()

    async def _get_session(_self, *, session_id):  # noqa: ANN003
        return _row(status=AgentSessionStatus.ACTIVE)

    with patch.object(
        AgentSessionService, "get_session", _get_session
    ), patch(
        "prodavan.application.agent.session_service.get_agent_adapter",
        return_value=_FakeAdapter(),
    ):
        out = await AgentSessionService(session).delete_session(
            project_id="proj_1",
            session_id="ags_1",
            principal=MagicMock(),
            employee=None,
        )
    assert out["module_rows_deleted"] == 0
    assert out["ok"] is True
