"""In-process periodic pod metrics sampler (k8s mode)."""

from __future__ import annotations

import asyncio
import logging

from prodavan.config.settings import settings
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)

_task: asyncio.Task[None] | None = None
_stop: asyncio.Event | None = None


def _sampler_wanted() -> bool:
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    return mode == "k8s"


async def _loop(stop: asyncio.Event) -> None:
    interval = max(5.0, float(settings.metrics_sample_interval_sec))
    factory = get_session_factory()
    while not stop.is_set():
        try:
            from prodavan.application.pod_service.metrics_sampler import PodMetricsSampler

            async with factory() as session:
                stats = await PodMetricsSampler(session).sample_managed_pods()
                await session.commit()
            if stats.get("sampled") or stats.get("degraded"):
                logger.debug("pod metrics sampler tick stats=%s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pod metrics sampler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def start_pod_metrics_sampler() -> None:
    global _task, _stop
    if not _sampler_wanted():
        return
    if _task is not None and not _task.done():
        return
    _stop = asyncio.Event()
    _task = asyncio.create_task(_loop(_stop), name="prodavan-pod-metrics-sampler")
    logger.info(
        "pod_metrics_sampler: started interval=%ss",
        settings.metrics_sample_interval_sec,
    )


async def stop_pod_metrics_sampler() -> None:
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
