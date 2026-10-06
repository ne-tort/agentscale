"""Сторож зависших прогонов агента (run-stall watchdog).

Провайдер может «подменить» модель посреди диалога или зависнуть, не отдавая
SSE — тогда прогон не получает ни done, ни error: в UI вечный спиннер, а чат
выглядит живым. Детект по СТРУКТУРЕ событий: после последнего терминального
события (done/error) есть активность рана (tool/текст/статусы), но тишина
дольше STALL_AFTER (больше встроенного 300-секундного таймаута провайдера).

Обработка stall — ЧАСТЬ штатной системы ошибок провайдера (chat_error_policy,
та же, что reconnect-движок рантайма):

- в транскрипт пишется ``status{phase:"reconnect", stall:true}`` — тот же
  кадр, который рантайм эмитит при ожидании реконнекта: живой UI показывает
  «Попытка реконнекта…», а история статус-кадры не рендерит (никаких
  самодельных сообщений в чате);
- ``done{reason:"stalled"}`` закрывает зависший ран (UI не синтезирует
  ошибку: reason не входит в failure-набор);
- ретрай = повтор последнего пользовательского сообщения (payload-флаг
  ``stall_retry`` — UI не дублирует пузырь; send уносит SendRetryPolicy
  проекта, так что новый ран обслуживается reconnect-движком рантайма);
- число попыток и пауза между ними — из политики чата: ``max_attempts``
  (0 = бесконечно, серверный дефолт) и ``interval_sec``;
- когда попытки политики исчерпаны — штатная терминальная пара как у
  рантайма: ``error{PROVIDER_TIMEOUT}`` + ``done{api_error}``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.identity import ROLE_PLATFORM_ADMIN, Principal
from prodavan.domain.projects.chat_error_policy import normalize_chat_error_policy
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

# Дольше встроенного wall-clock таймаута провайдера (300s) + запас.
STALL_AFTER = timedelta(minutes=7)
# Окно кандидатов: сессии с событием за последние N часов.
CANDIDATE_WINDOW = timedelta(hours=2)

TERMINAL_TYPES = {"done", "error"}
# Легаси-маркер предыдущей версии сторожа (user-visible error) — учитываем
# при дедупе, но больше не пишем.
STALL_ERROR_CODE = "CHAT_RUN_STALLED"
# Терминал исчерпанной политики — тот же код, что эмитит рантайм.
PROVIDER_TIMEOUT_CODE = "PROVIDER_TIMEOUT"
EXHAUSTED_MESSAGE = "provider request timed out: agent run stalled"


def _ts(row: AgentEventRow) -> datetime:
    raw = row.at or row.created_at
    if raw is None:
        return datetime.min.replace(tzinfo=UTC)
    if raw.tzinfo is None:
        return raw.replace(tzinfo=UTC)
    return raw


def _payload(row: AgentEventRow) -> dict[str, Any]:
    payload = row.payload
    return payload if isinstance(payload, dict) else {}


class AgentRunStallService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sweep_all(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(tz=UTC)
        stats: dict[str, Any] = {
            "checked": 0,
            "stalled": 0,
            "retried": 0,
            "marked_only": 0,
            "exhausted": 0,
        }

        cutoff = now - CANDIDATE_WINDOW
        q = await self._session.execute(
            select(AgentEventRow.session_id)
            .where(AgentEventRow.created_at >= cutoff)
            .group_by(AgentEventRow.session_id)
        )
        candidate_ids = [str(s) for s in q.scalars().all()]
        for session_id in candidate_ids:
            try:
                result = await self._check_session(session_id, now=now)
            except Exception:
                logger.exception("run-stall sweep failed for session %s", session_id)
                continue
            stats["checked"] += 1
            if result == "stalled_retried":
                stats["stalled"] += 1
                stats["retried"] += 1
            elif result == "stalled_marked":
                stats["stalled"] += 1
                stats["marked_only"] += 1
            elif result == "stalled_exhausted":
                stats["stalled"] += 1
                stats["exhausted"] += 1
        await self._session.commit()
        return stats

    async def _check_session(self, session_id: str, *, now: datetime) -> str | None:
        sess = await self._session.get(AgentSessionRow, session_id)
        if sess is None or str(sess.status) != "active":
            return None
        q = await self._session.execute(
            select(AgentEventRow)
            .where(AgentEventRow.session_id == session_id)
            .order_by(AgentEventRow.seq.desc())
            .limit(400)
        )
        events = list(q.scalars().all())
        if not events:
            return None
        events.reverse()  # по возрастанию seq

        last_terminal_idx = -1
        for i, ev in enumerate(events):
            if ev.event_type in TERMINAL_TYPES:
                last_terminal_idx = i
        post = events[last_terminal_idx + 1 :]
        if not post:
            return None  # ран завершён / ранов не было

        # этот ран уже обработан сторожем (наш status-маркер или легаси-error)
        if any(self._is_own_marker(ev) for ev in post):
            return None

        last_activity = max(_ts(ev) for ev in post)
        if now - last_activity < STALL_AFTER:
            return None  # ран жив (стрим/тулзы пишут события постоянно)

        attempts, last_marker_ts, last_user_text = self._scan_chain(events)
        policy = await self._project_policy(str(sess.project_id))
        max_attempts = int(policy["max_attempts"])

        # пауза политики между повторами (дефолтные 10s < STALL_AFTER —
        # фактически не тормозит; защищает длинные interval_sec)
        if last_marker_ts is not None and now - last_marker_ts < timedelta(
            seconds=int(policy["interval_sec"])
        ):
            return None

        # попытки политики исчерпаны — штатный терминал (как у рантайма)
        if max_attempts > 0 and attempts >= max_attempts:
            await self._mark_exhausted(sess, events=events)
            return "stalled_exhausted"

        attempt = attempts + 1
        await self._mark_stalled(sess, events=events, attempt=attempt, policy=policy)

        if not last_user_text:
            return "stalled_marked"

        # ретрай — только если под проекта жив
        if not await self._pod_running(str(sess.project_id)):
            return "stalled_marked"

        try:
            from prodavan.application.agent.session_service import AgentSessionService

            await AgentSessionService(self._session).send_message(
                project_id=str(sess.project_id),
                session_id=session_id,
                text=last_user_text,
                attachment_refs=None,
                principal=Principal(
                    sub="system:run-stall-watchdog",
                    roles=frozenset({ROLE_PLATFORM_ADMIN}),
                ),
                employee=None,
                stall_retry=True,
            )
            return "stalled_retried"
        except Exception:
            logger.exception("run-stall auto retry failed session=%s", session_id)
            return "stalled_marked"

    @staticmethod
    def _is_own_marker(ev: AgentEventRow) -> bool:
        payload = _payload(ev)
        if ev.event_type == "status" and payload.get("stall") is True:
            return True
        return ev.event_type == "error" and payload.get("code") == STALL_ERROR_CODE

    @staticmethod
    def _scan_chain(events: list[AgentEventRow]) -> tuple[int, datetime | None, str]:
        """(attempts, ts последнего stalled-маркера, текст для повтора).

        Цепочка stall-ретраев: от хвоста событий назад до ОРГАНИЧЕСКОГО
        user_message (без флага stall_retry) или предыдущего терминала.
        Считаем done{stalled} маркеры (попытки уже были) и запоминаем
        последний пользовательский текст.
        """
        attempts = 0
        last_marker_ts: datetime | None = None
        last_user_text = ""
        for ev in reversed(events):
            payload = _payload(ev)
            if ev.event_type == "user_message":
                text = str(payload.get("text") or "").strip()
                if text and not last_user_text:
                    last_user_text = text
                if payload.get("stall_retry") is not True:
                    break  # начало цепочки — органическое сообщение
                continue
            if ev.event_type == "done" and payload.get("stalled") is True:
                attempts += 1
                if last_marker_ts is None:
                    last_marker_ts = _ts(ev)
                continue
            if ev.event_type in TERMINAL_TYPES:
                break  # предыдущий ран завершён штатно — цепочка не наша
        return attempts, last_marker_ts, last_user_text

    async def _project_policy(self, project_id: str) -> dict[str, Any]:
        project = await self._session.get(ProjectRow, project_id)
        raw = getattr(project, "chat_error_policy", None) if project is not None else None
        try:
            return normalize_chat_error_policy(raw if isinstance(raw, dict) else None)
        except Exception:
            return normalize_chat_error_policy(None)

    async def _pod_running(self, project_id: str) -> bool:
        q = await self._session.execute(
            select(ProjectPodRow.status)
            .where(ProjectPodRow.project_id == project_id)
            .order_by(ProjectPodRow.updated_at.desc())
            .limit(1)
        )
        status = q.scalar_one_or_none()
        return str(status or "").lower() == "running"

    async def _mark_stalled(
        self,
        sess: AgentSessionRow,
        *,
        events: list[AgentEventRow],
        attempt: int,
        policy: dict[str, Any],
    ) -> None:
        next_seq = max(int(ev.seq) for ev in events) + 1
        # кадр реконнекта (UI-индикатор «Попытка реконнекта…», в истории
        # невидим) + терминал зависшего рана без user-visible ошибки
        self._session.add(
            AgentEventRow(
                session_id=str(sess.id),
                seq=next_seq,
                event_type="status",
                payload={
                    "phase": "reconnect",
                    "attempt": attempt,
                    "max_attempts": int(policy["max_attempts"]),
                    "retry_in_ms": int(policy["interval_sec"]) * 1000,
                    "stall": True,
                    "detail": "run stall watchdog",
                },
            )
        )
        self._session.add(
            AgentEventRow(
                session_id=str(sess.id),
                seq=next_seq + 1,
                event_type="done",
                payload={"reason": "stalled", "stalled": True, "attempt": attempt},
            )
        )
        await self._session.flush()

    async def _mark_exhausted(
        self,
        sess: AgentSessionRow,
        *,
        events: list[AgentEventRow],
    ) -> None:
        next_seq = max(int(ev.seq) for ev in events) + 1
        self._session.add(
            AgentEventRow(
                session_id=str(sess.id),
                seq=next_seq,
                event_type="error",
                payload={
                    "code": PROVIDER_TIMEOUT_CODE,
                    "message": EXHAUSTED_MESSAGE,
                    "retryable": False,
                },
            )
        )
        self._session.add(
            AgentEventRow(
                session_id=str(sess.id),
                seq=next_seq + 1,
                event_type="done",
                payload={"reason": "api_error", "error_message": EXHAUSTED_MESSAGE},
            )
        )
        await self._session.flush()
