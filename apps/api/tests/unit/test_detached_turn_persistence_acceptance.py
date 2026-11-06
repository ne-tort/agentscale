"""Acceptance coverage for detached-turn idempotency across API restart."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.agent.detached_turn import DetachedTurnRegistry
from prodavan.application.agent.session_service import AgentSessionService


@pytest.mark.asyncio
async def test_persisted_turn_id_retry_after_restart_never_starts_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted ledger row outlives both the task registry and event pruning."""
    registry = DetachedTurnRegistry()
    monkeypatch.setattr("prodavan.application.agent.session_service.detached_turns", registry)
    persisted = SimpleNamespace(start_seq=17, status="completed")
    seq_query = SimpleNamespace(scalar_one=lambda: 17)
    ledger_query = SimpleNamespace(scalar_one_or_none=lambda: persisted)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[seq_query, ledger_query])
    service = AgentSessionService(session)

    turn = await service.start_detached_turn(
        project_id="project-1",
        session_id="session-1",
        text="same request",
        attachment_refs=None,
        principal=SimpleNamespace(sub="employee-1"),  # type: ignore[arg-type]
        employee=SimpleNamespace(id="employee-1"),  # type: ignore[arg-type]
        model=None,
        turn_id="client-stable-id",
    )

    assert turn.finished is True
    assert turn.start_seq == 17
    assert registry.get("client-stable-id", session_id="session-1") is turn
    assert turn.task is None


@pytest.mark.asyncio
async def test_remember_completed_is_idempotent_and_does_not_mark_session_active() -> None:
    registry = DetachedTurnRegistry()
    first = registry.remember_completed(
        turn_id="stable-id",
        session_id="session-1",
        project_id="project-1",
        start_seq=5,
    )
    second = registry.remember_completed(
        turn_id="stable-id",
        session_id="session-1",
        project_id="project-1",
        start_seq=9,
    )

    assert second is first
    assert second.start_seq == 5
    assert registry.active_for_session("session-1") is None


@pytest.mark.asyncio
async def test_restart_replay_closes_abandoned_started_ledger_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart replay makes the durable terminal status match INTERRUPTED."""
    registry = DetachedTurnRegistry()
    monkeypatch.setattr("prodavan.application.agent.session_service.detached_turns", registry)
    persisted = SimpleNamespace(start_seq=23, status="started", finished_at=None)
    seq_query = SimpleNamespace(scalar_one=lambda: 23)
    ledger_query = SimpleNamespace(scalar_one_or_none=lambda: persisted)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[seq_query, ledger_query])
    service = AgentSessionService(session)

    turn = await service.start_detached_turn(
        project_id="project-1",
        session_id="session-1",
        text="replayed request",
        attachment_refs=None,
        principal=SimpleNamespace(sub="employee-1"),  # type: ignore[arg-type]
        employee=SimpleNamespace(id="employee-1"),  # type: ignore[arg-type]
        model=None,
        turn_id="client-stable-id",
    )

    assert turn.finished is True
    assert turn.error_code == "INTERRUPTED"
    assert persisted.status == "failed"
    assert persisted.finished_at is not None
    session.commit.assert_awaited_once()
