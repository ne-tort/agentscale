"""Run-stall watchdog: детект зависшего рана + ретраи по политике чата.

Ран = события после последнего терминального (done/error). Тишина дольше
STALL_AFTER → ран мёртв. Обработка — в рамках штатной reconnect-системы:
status{phase:"reconnect", stall:true} (в транскрипте невидим) + done{stalled}
(без user-visible ошибки) + повтор последнего пользовательского сообщения
(флаг stall_retry). Число попыток и пауза — из chat_error_policy проекта
(max_attempts=0 → бесконечно); при исчерпании — штатный терминал
error{PROVIDER_TIMEOUT} + done{api_error}, как у рантайма.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import prodavan.application.agent.session_service as session_service_mod
from prodavan.application.agent.run_stall import STALL_AFTER, AgentRunStallService
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
OLD = NOW - STALL_AFTER - timedelta(minutes=2)
FRESH = NOW - timedelta(seconds=30)
# маркер предыдущей stall-попытки: тишина уже >= STALL_AFTER, но для
# интервального гейта он «недавний»
MARKER_TS = NOW - STALL_AFTER - timedelta(minutes=1)


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
    """Минимальный AsyncSession для run-stall: candidates/events/pods/project."""

    def __init__(
        self,
        *,
        sessions: dict[str, AgentSessionRow],
        events: dict[str, list[AgentEventRow]],
        pod_running: bool = True,
        chat_error_policy: dict | None = None,
    ) -> None:
        self._sessions = sessions
        self._events = events
        self._pod_running = pod_running
        self._project = ProjectRow(id="p1", chat_error_policy=chat_error_policy)
        self.added: list[AgentEventRow] = []
        self.committed = 0

    async def get(self, model: Any, key: str) -> Any:
        if model is AgentSessionRow:
            return self._sessions.get(key)
        if model is ProjectRow:
            return self._project
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


def _capture_sends(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    sent: list[dict] = []

    async def _send(self, **kwargs):  # noqa: ANN001, ANN003
        sent.append(kwargs)
        return {"session_id": kwargs["session_id"], "events": []}

    monkeypatch.setattr(session_service_mod.AgentSessionService, "send_message", _send)
    return sent


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
async def test_stalled_run_reconnect_status_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Первый stall: reconnect-кадр + done(stalled), БЕЗ user-visible ошибки;
    повтор сообщения с флагом stall_retry."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери SSD"}),
            _ev("s1", 2, "tool_call", {"name": "equipment_catalog_search"}),
            _ev("s1", 3, "tool_result", {"ok": True}),
        ]},
        pod_running=True,
    )
    sent = _capture_sends(monkeypatch)

    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["retried"] == 1
    # только status + done: никакого error-текста в транскрипт
    types = [r.event_type for r in db.added]
    assert types == ["status", "done"]
    status_payload = db.added[0].payload
    assert status_payload["phase"] == "reconnect"
    assert status_payload["stall"] is True
    assert status_payload["attempt"] == 1
    assert db.added[1].payload["reason"] == "stalled"
    # повтор последним сообщением пользователя, с флагом stall_retry
    assert sent and sent[0]["text"] == "подбери SSD"
    assert sent[0]["session_id"] == "s1"
    assert sent[0]["stall_retry"] is True


@pytest.mark.asyncio
async def test_default_policy_retries_without_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Политика по умолчанию (max_attempts=0) — бесконечные ретраи:
    второй stall в цепочке снова повторяет запрос (attempt=2)."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "status", {"phase": "reconnect", "stall": True, "attempt": 1}),
            _ev("s1", 3, "done", {"reason": "stalled", "stalled": True, "attempt": 1}, at=OLD),
            _ev("s1", 4, "user_message", {"text": "подбери", "stall_retry": True}, at=OLD),
            _ev("s1", 5, "text_delta", {"text": "..."}, at=OLD),
        ]},
        pod_running=True,
    )
    sent = _capture_sends(monkeypatch)

    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["retried"] == 1
    assert [r.event_type for r in db.added] == ["status", "done"]
    assert db.added[0].payload["attempt"] == 2
    assert db.added[0].payload["max_attempts"] == 0
    assert sent and sent[0]["stall_retry"] is True


@pytest.mark.asyncio
async def test_finite_policy_exhausted_final_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_attempts исчерпан → штатный терминал рантайма:
    error{PROVIDER_TIMEOUT} + done{api_error}, без нового повтора."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "done", {"reason": "stalled", "stalled": True, "attempt": 1}, at=OLD),
            _ev("s1", 3, "user_message", {"text": "подбери", "stall_retry": True}, at=OLD),
            _ev("s1", 4, "tool_call", {"name": "x"}, at=OLD),
        ]},
        pod_running=True,
        chat_error_policy={"interval_sec": 10, "max_attempts": 1, "fallback_models": []},
    )
    sent = _capture_sends(monkeypatch)

    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["exhausted"] == 1 and stats["retried"] == 0
    assert [r.event_type for r in db.added] == ["error", "done"]
    assert db.added[0].payload["code"] == "PROVIDER_TIMEOUT"
    assert db.added[0].payload["retryable"] is False
    assert db.added[1].payload["reason"] == "api_error"
    assert sent == []


@pytest.mark.asyncio
async def test_policy_interval_gates_next_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """interval_sec больше, чем прошло с прошлого маркера → ждём (ничего не пишем)."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "done", {"reason": "stalled", "stalled": True, "attempt": 1}, at=MARKER_TS),
            _ev("s1", 3, "user_message", {"text": "подбери", "stall_retry": True}, at=MARKER_TS),
            _ev("s1", 4, "text_delta", {"text": "..."}, at=MARKER_TS),
        ]},
        pod_running=True,
        chat_error_policy={"interval_sec": 3600, "max_attempts": 0, "fallback_models": []},
    )
    sent = _capture_sends(monkeypatch)

    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 0
    assert db.added == []
    assert sent == []

    # через час гейт открыт — попытка продолжается
    stats2 = await AgentRunStallService(db).sweep_all(now=NOW + timedelta(hours=1, minutes=1))
    assert stats2["retried"] == 1
    assert db.added[0].payload["attempt"] == 2


@pytest.mark.asyncio
async def test_stalled_but_pod_down_marks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Под не жив — ретраить некуда: только reconnect-маркеры, без ошибки."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "text_delta", {"text": "..."}),
        ]},
        pod_running=False,
    )
    sent = _capture_sends(monkeypatch)
    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 1 and stats["retried"] == 0 and stats["marked_only"] == 1
    assert [r.event_type for r in db.added] == ["status", "done"]
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
    _capture_sends(monkeypatch)
    svc = AgentRunStallService(db)  # type: ignore[arg-type]
    stats1 = await svc.sweep_all(now=NOW)
    assert stats1["stalled"] == 1
    written = len(db.added)
    assert written == 2  # status + done

    # второй прогон: последний терминал — наш done(stalled), после него
    # активности нет → не stalled, ничего не пишется
    stats2 = await svc.sweep_all(now=NOW + timedelta(minutes=1))
    assert stats2["stalled"] == 0
    assert len(db.added) == written


@pytest.mark.asyncio
async def test_legacy_stall_error_not_reprocessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Легаси error{CHAT_RUN_STALLED} старой версии сторожа — терминал:
    ран закрыт, новый sweep его не трогает."""
    db = FakeSession(
        sessions={"s1": _session()},
        events={"s1": [
            _ev("s1", 1, "user_message", {"text": "подбери"}),
            _ev("s1", 2, "error", {"code": "CHAT_RUN_STALLED", "message": "…"}),
            _ev("s1", 3, "done", {"reason": "stalled", "stalled": True}),
        ]},
        pod_running=True,
    )
    sent = _capture_sends(monkeypatch)
    stats = await AgentRunStallService(db).sweep_all(now=NOW)  # type: ignore[arg-type]
    assert stats["stalled"] == 0
    assert db.added == []
    assert sent == []
