"""Lifespan resource — storage metrics sampler."""

from __future__ import annotations

from prodavan.application.metrics.storage_sampler_worker import (
    start_storage_metrics_sampler,
    stop_storage_metrics_sampler,
)
from prodavan.core.lifespan.resource import LifespanResource


class StorageMetricsSamplerResource(LifespanResource):
    @property
    def name(self) -> str:
        return "storage_metrics_sampler"

    async def startup(self) -> None:
        start_storage_metrics_sampler()

    async def shutdown(self) -> None:
        await stop_storage_metrics_sampler()
