"""Opt-in background drain of project triggers + optional idle pause (L07/L08/L09).

Enable via TRIGGER_WORKER_ENABLED and/or IDLE_PAUSE_WORKER_ENABLED.

Executors (P0):
- **Celery** (preferred): ``CELERY_ENABLED=true`` + broker → tasks in
  ``prodavan.core.jobs``; run ``celery -A prodavan.core.infra.worker_manager.celery_app worker -B``
- **In-process asyncio** (transitional): when Celery executor is not active

Prefer admin POST /admin/triggers/drain (and idle-pause/sweep) in CI.

Safety layers:
- process advisory lock (pg_try_advisory_lock) so one API process runs at a time
- row-level outbox lease + SKIP LOCKED on claim (crash → lease expires → re-claim)

Still not a separate durable broker (Kafka) — hole noted in L07 as outbox-lite.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
from prodavan.application.projects.idle_pause_service import IdlePauseService
from prodavan.config.settings import settings
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)

_LOCK_KEY_SQL = "hashtext('prodavan.trigger_worker')"

_stop: asyncio.Event | None = None
_task: asyncio.Task[None] | None = None


def _worker_wanted() -> bool:
    return bool(settings.trigger_worker_enabled or settings.idle_pause_worker_enabled)


async def drain_once() -> dict:
    factory = get_session_factory()
    async with factory() as session:
        locked = await session.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY_SQL})"))
        if not locked.scalar():
            return {"dispatched": False, "reason": "lock_held", "count": 0, "projects": []}
        try:
            out: dict = {
                "dispatched": False,
                "count": 0,
                "projects": [],
            }
            if settings.trigger_worker_enabled:
                out = await AgentTriggerDispatcher(session).drain_all(
                    max_projects=settings.trigger_worker_max_projects,
                    max_per_project=settings.trigger_worker_batch_max,
                )
            if settings.idle_pause_worker_enabled:
                idle = await IdlePauseService(session).sweep_all()
                out["idle_pause"] = idle
            return out
        finally:
            await session.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY_SQL})"))


async def _loop(stop: asyncio.Event) -> None:
    interval = max(1.0, float(settings.trigger_worker_interval_sec))
    logger.info(
        "trigger worker started interval=%ss drain=%s idle_pause=%s lease=%ss",
        interval,
        settings.trigger_worker_enabled,
        settings.idle_pause_worker_enabled,
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
                idle = result.get("idle_pause") or {}
                idle_count = int(idle.get("count") or 0)
                if idle_count:
                    logger.info("idle pause worker paused count=%s", idle_count)
        except Exception:
            logger.exception("trigger worker tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("trigger worker stopped")


def start_trigger_worker() -> None:
    global _stop, _task
    if not _worker_wanted():
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
