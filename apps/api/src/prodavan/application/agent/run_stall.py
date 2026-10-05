"""Сторож зависших прогонов агента (run-stall watchdog).

Провайдер может «подменить» модель посреди диалога или зависнуть, не отдавая
SSE — тогда прогон не получает ни done, ни error: в UI вечный спиннер, а чат
выглядит живым. Детект по СТРУКТУРЕ событий: после последнего терминального
события (done/error) есть активность рана (tool/текст/статусы), но тишина
дольше STALL_AFTER (больше встроенного 300-секундного таймаута провайдера).

На детект:
- пишем error{code=CHAT_RUN_STALLED} + done{reason=stalled} — UI закрывает
  спиннер и показывает понятную ошибку;
- один раз на ран: авто-ретрай последним сообщением пользователя (pod жив).
  Если ретрай тоже завис — только метка, без повторных ретраев (цикл off).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.identity import ROLE_PLATFORM_ADMIN, Principal
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow

logger = logging.getLogger(__name__)

# Дольше встроенного wall-clock таймаута провайдера (300s) + запас.
STALL_AFTER = timedelta(minutes=7)
# Окно кандидатов: сессии с событием за последние N часов.
CANDIDATE_WINDOW = timedelta(hours=2)

TERMINAL_TYPES = {"done", "error"}
STALL_ERROR_CODE = "CHAT_RUN_STALLED"


def _ts(row: AgentEventRow) -> datetime:
    raw = row.at or row.created_at
    if raw is None:
        return datetime.min.replace(tzinfo=UTC)
    if raw.tzinfo is None:
        return raw.replace(tzinfo=UTC)
    return raw


class AgentRunStallService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sweep_all(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(tz=UTC)
        stats: dict[str, Any] = {"checked": 0, "stalled": 0, "retried": 0, "marked_only": 0}

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

        # этот ран уже помечен как зависший (наш маркер) — не дублируем
        if any(
            ev.event_type == "error" and (ev.payload or {}).get("code") == STALL_ERROR_CODE
            for ev in post
        ):
            return None

        last_activity = max(_ts(ev) for ev in post)
        if now - last_activity < STALL_AFTER:
            return None  # ран жив (стрим/тулзы пишут события постоянно)

        prev_terminal = events[last_terminal_idx] if last_terminal_idx >= 0 else None
        prev_was_stall = (
            prev_terminal is not None
            and prev_terminal.event_type == "done"
            and (prev_terminal.payload or {}).get("reason") == "stalled"
        )

        # последнее сообщение пользователя в этом ране (для ретрая)
        last_user_text = ""
        for ev in reversed(post):
            if ev.event_type == "user_message":
                last_user_text = str((ev.payload or {}).get("text") or "").strip()
                break

        stalled = await self._mark_stalled(sess, events=events, auto_retry=not prev_was_stall)

        if prev_was_stall or not last_user_text:
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
            )
            return "stalled_retried" if stalled else None
        except Exception:
            logger.exception("run-stall auto retry failed session=%s", session_id)
            return "stalled_marked"

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
        auto_retry: bool,
    ) -> bool:
        next_seq = max(int(ev.seq) for ev in events) + 1
        self._session.add(
            AgentEventRow(
                session_id=str(sess.id),
                seq=next_seq,
                event_type="error",
                payload={
                    "code": STALL_ERROR_CODE,
                    "message": (
                        "Ответ агента прервался без завершения (таймаут/обрыв на стороне "
                        "провайдера). " + ("Повторяю запрос автоматически…" if auto_retry else
                        "Повторите запрос вручную.")
                    ),
                    "auto_retry": auto_retry,
                },
            )
        )
        self._session.add(
            AgentEventRow(
                session_id=str(sess.id),
                seq=next_seq + 1,
                event_type="done",
                payload={"reason": "stalled", "stalled": True, "auto_retry": auto_retry},
            )
        )
        await self._session.flush()
        return True
