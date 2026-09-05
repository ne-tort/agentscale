"""In-process periodic storage metrics sampler."""

from __future__ import annotations

import asyncio
import logging

from prodavan.config.settings import settings
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)

_task: asyncio.Task[None] | None = None
_stop: asyncio.Event | None = None


async def _loop(stop: asyncio.Event) -> None:
    interval = max(60.0, float(settings.metrics_storage_sample_interval_sec))
    factory = get_session_factory()
    while not stop.is_set():
        try:
            from prodavan.application.metrics.storage_sampler import StorageMetricsSampler

            async with factory() as session:
                stats = await StorageMetricsSampler(session).sample_all()
                await session.commit()
            if stats.get("published"):
                logger.debug("storage metrics sampler tick stats=%s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("storage metrics sampler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def start_storage_metrics_sampler() -> None:
    global _task, _stop
    if not settings.metrics_storage_sampler_enabled:
        return
    if _task is not None and not _task.done():
        return
    _stop = asyncio.Event()
    _task = asyncio.create_task(_loop(_stop), name="prodavan-storage-metrics-sampler")
    logger.info(
        "storage_metrics_sampler: started interval=%ss",
        settings.metrics_storage_sample_interval_sec,
    )


async def stop_storage_metrics_sampler() -> None:
    global _task, _stop
    if _stop is not None:
        _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            _task.cancel()
    _task = None
    _stop = None
