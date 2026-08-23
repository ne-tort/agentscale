"""Opt-in background drain of project triggers (L07/L08).

Enable via TRIGGER_WORKER_ENABLED=true. Prefer admin POST /admin/triggers/drain in CI.

Safety layers:
- process advisory lock (pg_try_advisory_lock) so one API process drains at a time
- row-level outbox lease + SKIP LOCKED on claim (crash → lease expires → re-claim)

Still not a separate durable broker (Kafka/SQS) — hole noted in L07 as outbox-lite.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
from prodavan.config.settings import settings
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)

_LOCK_KEY_SQL = "hashtext('prodavan.trigger_worker')"

_stop: asyncio.Event | None = None
_task: asyncio.Task[None] | None = None


async def drain_once() -> dict:
    factory = get_session_factory()
    async with factory() as session:
        locked = await session.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY_SQL})"))
        if not locked.scalar():
            return {"dispatched": False, "reason": "lock_held", "count": 0, "projects": []}
        try:
            return await AgentTriggerDispatcher(session).drain_all(
                max_projects=settings.trigger_worker_max_projects,
                max_per_project=settings.trigger_worker_batch_max,
            )
        finally:
            await session.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY_SQL})"))


async def _loop(stop: asyncio.Event) -> None:
    interval = max(1.0, float(settings.trigger_worker_interval_sec))
    logger.info(
        "trigger worker started interval=%ss max_projects=%s batch=%s lease=%ss",
        interval,
        settings.trigger_worker_max_projects,
        settings.trigger_worker_batch_max,
        settings.trigger_outbox_lease_sec,
    )
    while not stop.is_set():
        try:
            result = await drain_once()
            if result.get("reason") == "lock_held":
                logger.debug("trigger worker skipped — advisory lock held")
            else:
                count = int(result.get("count") or 0)
                if count:
                    logger.info("trigger worker drained count=%s", count)
        except Exception:
            logger.exception("trigger worker tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("trigger worker stopped")


def start_trigger_worker() -> None:
    global _stop, _task
    if not settings.trigger_worker_enabled:
        return
    if _task is not None and not _task.done():
        return
    _stop = asyncio.Event()
    _task = asyncio.create_task(_loop(_stop), name="prodavan-trigger-worker")


async def stop_trigger_worker() -> None:
    global _stop, _task
    if _stop is not None:
        _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            _task.cancel()
        _task = None
    _stop = None
