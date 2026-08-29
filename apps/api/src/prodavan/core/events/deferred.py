"""Defer C-EVENT-BUS Kafka publish until PG transaction commits."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from prodavan.core.events.envelope import EventEnvelope

logger = logging.getLogger(__name__)

_PENDING_KEY = "_prodavan_event_bus_pending"
_listeners_registered = False


async def _publish_pending(items: list[EventEnvelope]) -> None:
    from prodavan.core.events.bus import publish_envelope

    for envelope in items:
        try:
            await publish_envelope(envelope)
        except Exception:
            logger.exception(
                "deferred event bus publish failed bus=%s id=%s",
                envelope.bus,
                envelope.event_id,
            )


def _flush_pending_sync(session: Session) -> None:
    items: list[EventEnvelope] = session.info.pop(_PENDING_KEY, [])
    if not items:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_publish_pending(items))
        return
    loop.create_task(_publish_pending(items))


def _clear_pending(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def _on_after_commit(session: Session) -> None:
    if _PENDING_KEY not in session.info:
        return
    _flush_pending_sync(session)


def _on_after_rollback(session: Session) -> None:
    if _PENDING_KEY not in session.info:
        return
    _clear_pending(session)


def _register_listeners_once() -> None:
    global _listeners_registered
    if _listeners_registered:
        return
    event.listen(Session, "after_commit", _on_after_commit)
    event.listen(Session, "after_rollback", _on_after_rollback)
    _listeners_registered = True


def schedule_envelope_publish(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Queue envelope for Kafka dual-write after successful commit."""
    _register_listeners_once()
    sync = session.sync_session
    pending: list[EventEnvelope] = sync.info.setdefault(_PENDING_KEY, [])
    pending.append(envelope)


def schedule_platform_event_publish(
    session: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> None:
    from prodavan.core.events.envelope import platform_envelope

    schedule_envelope_publish(
        session,
        platform_envelope(
            event_id=event_id,
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
            occurred_at=occurred_at,
        ),
    )


def schedule_project_trigger_publish(
    session: AsyncSession,
    *,
    event_id: str,
    kind: str,
    project_id: str,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> None:
    from prodavan.core.events.envelope import project_trigger_envelope

    schedule_envelope_publish(
        session,
        project_trigger_envelope(
            event_id=event_id,
            kind=kind,
            project_id=project_id,
            company_id=company_id,
            cabinet_id=cabinet_id,
            payload=payload,
            occurred_at=occurred_at,
        ),
    )


def schedule_metrics_event_publish(
    session: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> None:
    from prodavan.core.events.envelope import metrics_envelope

    schedule_envelope_publish(
        session,
        metrics_envelope(
            event_id=event_id,
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
            occurred_at=occurred_at,
        ),
    )
