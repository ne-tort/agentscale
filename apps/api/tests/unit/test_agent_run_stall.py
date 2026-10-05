"""Run-stall watchdog: детект зависшего рана по структуре событий.

Ран = события после последнего терминального (done/error). Тишина дольше
STALL_AFTER → ран мёртв: пишем error+done(stalled); один раз — авто-ретрай
последним сообщением пользователя (если под жив). Повторный stall после
ретрая — только метка (без цикла).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import prodavan.application.agent.session_service as session_service_mod
from prodavan.application.agent.run_stall import STALL_AFTER, AgentRunStallService
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
OLD = NOW - STALL_AFTER - timedelta(minutes=2)
FRESH = NOW - timedelta(seconds=30)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list:
        return list(self._rows)

    def scalar_one_or_none(self):  # noqa: ANN202
        return self._rows[0] if self._rows else None


class FakeSession:
    """Минимальный AsyncSession для run-stall: candidates/events/pods."""

    def __init__(
        self,
        *,
        sessions: dict[str, AgentSessionRow],
        events: dict[str, list[AgentEventRow]],
        pod_running: bool = True,
    ) -> None:
        self._sessions = sessions
        self._events = events
        self._pod_running = pod_running
        self.added: list[AgentEventRow] = []
        self.committed = 0

    async def get(self, model: Any, key: str) -> Any:
        if model is AgentSessionRow:
            return self._sessions.get(key)
        return None

    async def execute(self, stmt: Any) -> _Result:
        entity = stmt.column_descriptions[0].get("entity")
        col = stmt.column_descriptions[0].get("name")
        if entity is AgentEventRow and col == "session_id":
            # candidate sweep query
            return _Result([sid for sid, evs in self._events.items() if evs])
        if entity is AgentEventRow:
            # events of one session (stmt WHERE session_id)
            sid = self._stmt_session_id(stmt)
            rows = sorted(self._events.get(sid, []), key=lambda e: e.seq, reverse=True)
            return _Result(rows)
        if entity is ProjectPodRow:
            return _Result(["running"] if self._pod_running else ["stopped"])
        return _Result([])

    def _stmt_session_id(self, stmt: Any) -> str:
        # достаём значение параметра session_id из WHERE
        try:
            params = stmt.compile().params
            for k, v in params.items():
                if "session_id" in k:
                    return str(v)
        except Exception:
            pass
        return ""

    def add(self, row: AgentEventRow) -> None:
        self.added.append(row)
        self._events.setdefault(str(row.session_id), []).append(row)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed += 1


def _session(sid: str = "s1", status: str = "active", project_id: str = "p1") -> AgentSessionRow:
    return AgentSessionRow(
        id=sid,
        project_id=project_id,
        resolved_key_id=None,
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id=sid,
        model=None,
        cwd="/workspace",
        status=status,
    )


def _ev(sid: str, seq: int, etype: str, payload: dict | None = None, at: datetime = OLD) -> AgentEventRow:
    return AgentEventRow(session_id=sid, seq=seq, event_type=etype, payload=payload or {}, at=at, created_at=at)


@pytest.mark.asyncio
async def test_healthy_run_untouched() -> None:
    """Терминал есть, активности после — нет → не трогаем."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "привет"}),
            _ev("s1", 2, "done", {"reason": "completed"}),
        ]},
    )
    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_fresh_run_untouched() -> None:
    """Ран идёт прямо сейчас (свежая активность) → не трогаем."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "найди"}),
            _ev("s1", 2, "done", {"reason": "completed"}),
            _ev("s1", 3, "user_message", {"text": "ещё"}, at=FRESH),
            _ev("s1", 4, "text_delta", {"text": "..."}, at=FRESH),
        ]},
    )
    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_stalled_run_marked_and_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Зависший ран: error+done(stalled) + один авто-ретрай последним текстом."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери SSD"}),
            _ev("s1", 2, "tool_call", {"name": "equipment_catalog_search"}),
            _ev("s1", 3, "tool_result", {"ok": True}),
        ]},
        pod_running=True,
    )
    sent: list[dict] = []

    async def _send(self, **kwargs):  # noqa: ANN001, ANN003
        sent.append(kwargs)
        return {"session_id": kwargs["session_id"], "events": []}

    monkeypatch.setattr(session_service_mod.AgentSessionService, "send_message", _send)

    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["retried"] == 1
    # error + done записаны
    types = [r.event_type for r in db.added]
    assert types == ["error", "done"]
    assert db.added[0].payload["code"] == "CHAT_RUN_STALLED"
    assert db.added[0].payload["auto_retry"] is True
    assert db.added[1].payload["reason"] == "stalled"
    # ретрай последним сообщением пользователя
    assert sent and sent[0]["text"] == "подбери SSD"
    assert sent[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_second_consecutive_stall_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ран ПОСЛЕ stalled-done (т.е. это был наш ретрай) → только метка, без цикла."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "done", {"reason": "stalled", "stalled": True}),
            _ev("s1", 3, "user_message", {"text": "подбери"}),  # авто-ретрай
            _ev("s1", 4, "text_delta", {"text": "..."}),
        ]},
        pod_running=True,
    )
    sent: list[dict] = []

    async def _send(self, **kwargs):  # noqa: ANN001, ANN003
        sent.append(kwargs)
        return {"session_id": kwargs["session_id"], "events": []}

    monkeypatch.setattr(session_service_mod.AgentSessionService, "send_message", _send)

    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["retried"] == 0
    assert sent == []
    assert db.added[0].payload["auto_retry"] is False


@pytest.mark.asyncio
async def test_stalled_but_pod_down_marks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Под не жив — ретраить некуда: только метка ошибки."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "text_delta", {"text": "..."}),
        ]},
        pod_running=False,
    )
    sent: list[dict] = []

    async def _send(self, **kwargs):  # noqa: ANN001, ANN003
        sent.append(kwargs)
        return {}

    monkeypatch.setattr(session_service_mod.AgentSessionService, "send_message", _send)
    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["retried"] == 0
    assert sent == []


@pytest.mark.asyncio
async def test_stall_marker_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Второй прогон sweep по тому же зависшему рану ничего не пишет."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "tool_call", {"name": "x"}),
        ]},
        pod_running=False,  # без ретрая — просто метка
    )
    svc = AgentRunStallService(db)  # type: ignore[arg-type]
    stats1 = await svc.sweep_all(now=NOW)
    assert stats1["stalled"] == 1
    written = len(db.added)
    assert written == 2  # error + done

    # второй прогон: последний терминал — наш done(stalled), после него
    # активности нет → не stalled, ничего не пишется
    stats2 = await svc.sweep_all(now=NOW + timedelta(minutes=1))
    assert stats2["stalled"] == 0
    assert len(db.added) == written
