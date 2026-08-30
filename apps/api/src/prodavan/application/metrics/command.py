"""Metrics BC write path — ingest samples from Kafka."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.ports.metrics_store import MetricsStorePort
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.metrics.types import MetricSample, MetricWindow


class MetricsCommand:
    def __init__(
        self,
        session: AsyncSession,
        *,
        store: MetricsStorePort | None = None,
    ) -> None:
        self._session = session
        self._store = store or build_metrics_store()

    async def ingest_envelope(self, envelope: EventEnvelope) -> None:
        if envelope.event_type == "pod.metrics.degraded":
            return
        project_id = (envelope.project_id or "").strip()
        if not project_id:
            return
        sample = MetricSample.from_payload(
            project_id=project_id,
            company_id=envelope.company_id,
            cabinet_id=envelope.cabinet_id,
            payload=dict(envelope.payload or {}),
            timestamp=envelope.occurred_at,
        )
        await self.ingest_sample(sample)

    async def ingest_sample(self, sample: MetricSample) -> None:
        body = sample.to_dict()
        await self._store.put_project_latest(sample.project_id, body)
        window = MetricWindow()
        await self._store.append_project_series(
            sample.project_id,
            window.name,
            body,
            max_points=window.max_points,
        )
        if sample.pod_id:
            await self._store.put_pod_last_sample(sample.pod_id, body)
