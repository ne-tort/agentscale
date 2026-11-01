"""Durable idempotency for detached turns across API restarts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.agent import session_service as session_service_module
from prodavan.application.agent.detached_turn import DetachedTurnRegistry
from prodavan.application.agent.session_service import AgentSessionService


def _scalar_result(value):
    return SimpleNamespace(scalar_one=lambda: value, scalar_one_or_none=lambda: value)


@pytest.mark.asyncio
async def test_persisted_turn_after_restart_is_rehydrated_without_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pruned process cache cannot turn a client retry into a second run."""
    registry = DetachedTurnRegistry()
    monkeypatch.setattr(session_service_module, "detached_turns", registry)
    ledger = SimpleNamespace(
        turn_id="client-turn-1", session_id="sess-1", start_seq=7, status="started"
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(12), _scalar_result(ledger)])
    service = AgentSessionService(db)

    turn = await service.start_detached_turn(
        project_id="proj-1",
        session_id="sess-1",
        text="retry payload",
        attachment_refs=None,
        principal=SimpleNamespace(sub="user-1"),  # type: ignore[arg-type]
        employee=SimpleNamespace(id="employee-1"),  # type: ignore[arg-type]
        model=None,
        turn_id="client-turn-1",
    )

    assert turn.finished is True
    assert turn.error_code == "INTERRUPTED"
    assert turn.start_seq == 7
    assert turn.turn_id == "client-turn-1"
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_turn_id_is_scoped_to_resolved_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = DetachedTurnRegistry()
    monkeypatch.setattr(session_service_module, "detached_turns", registry)
    db = AsyncMock()
    # A prior session may have reserved this client-generated id; only the
    # (session_id, turn_id) identity is looked up for this new chat.
    db.execute = AsyncMock(side_effect=[_scalar_result(0), _scalar_result(None)])
    started = SimpleNamespace(turn_id="shared-client-turn", session_id="session-2")
    registry.start = AsyncMock(return_value=started)  # type: ignore[method-assign]
    service = AgentSessionService(db)

    turn = await service.start_detached_turn(
        project_id="proj-1",
        session_id="session-2",
        text="independent session request",
        attachment_refs=None,
        principal=SimpleNamespace(sub="user-1"),  # type: ignore[arg-type]
        employee=SimpleNamespace(id="employee-1"),  # type: ignore[arg-type]
        model=None,
        turn_id="shared-client-turn",
    )

    assert turn is started
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    registry.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_session_turn_id_replays_persisted_turn_without_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = DetachedTurnRegistry()
    monkeypatch.setattr(session_service_module, "detached_turns", registry)
    persisted = SimpleNamespace(start_seq=3, status="completed")
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(0), _scalar_result(persisted)])
    service = AgentSessionService(db)

    turn = await service.start_detached_turn(
        project_id="proj-1",
        session_id="session-1",
        text="retry payload",
        attachment_refs=None,
        principal=SimpleNamespace(sub="user-1"),  # type: ignore[arg-type]
        employee=SimpleNamespace(id="employee-1"),  # type: ignore[arg-type]
        model=None,
        turn_id="client-turn-2",
    )

    assert turn.turn_id == "client-turn-2"
    assert turn.session_id == "session-1"
    assert turn.finished is True
    assert turn.start_seq == 3
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
