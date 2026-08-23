"""In-process trigger/idle worker as LifespanResource (transitional → Celery)."""

from __future__ import annotations

import logging

from prodavan.application.agent.trigger_worker import start_trigger_worker, stop_trigger_worker
from prodavan.core.infra.worker_manager import get_worker_manager
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)


class TriggerWorkerResource(LifespanResource):
    """Opt-in asyncio drain loop.

    Skipped when WorkerManager Celery executor is active (C-JOBS).
    """

    @property
    def name(self) -> str:
        return "trigger_worker"

    async def startup(self) -> None:
        mgr = get_worker_manager()
        if mgr is not None and mgr.uses_celery_executor:
            logger.info("trigger_worker: skipped — Celery executor active (run celery worker -B)")
            return
        start_trigger_worker()

    async def shutdown(self) -> None:
        await stop_trigger_worker()

    async def health(self) -> bool | None:
        return None
