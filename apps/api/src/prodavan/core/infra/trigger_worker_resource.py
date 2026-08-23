"""In-process trigger/idle worker as LifespanResource (transitional → Celery)."""

from __future__ import annotations

from prodavan.application.agent.trigger_worker import start_trigger_worker, stop_trigger_worker
from prodavan.core.lifespan.resource import LifespanResource


class TriggerWorkerResource(LifespanResource):
    """Opt-in asyncio drain loop. P0 hole: replace with Celery (C-JOBS)."""

    @property
    def name(self) -> str:
        return "trigger_worker"

    async def startup(self) -> None:
        start_trigger_worker()

    async def shutdown(self) -> None:
        await stop_trigger_worker()

    async def health(self) -> bool | None:
        # Loop is opt-in; absence is not unhealthy.
        return None
