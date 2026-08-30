"""In-process pod metrics sampler — keeps Redis hot store fresh between UI polls."""

from __future__ import annotations

import logging

from prodavan.application.pod_service.pod_metrics_sampler_worker import (
    start_pod_metrics_sampler,
    stop_pod_metrics_sampler,
)
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)


class PodMetricsSamplerResource(LifespanResource):
    @property
    def name(self) -> str:
        return "pod_metrics_sampler"

    async def startup(self) -> None:
        start_pod_metrics_sampler()

    async def shutdown(self) -> None:
        await stop_pod_metrics_sampler()

    async def health(self) -> bool | None:
        return None
