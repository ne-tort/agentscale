"""Metrics BC write path — pod samples + overview facts."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.accumulator import MetricsAccumulator
from prodavan.application.metrics.adapters.redis_counter_store import build_counter_store
from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.ports.metrics_store import MetricsStorePort
from prodavan.application.metrics.presence_store import clear_presence, set_presence
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.metrics.types import METRICS_EVENT_TYPES, MetricSample, MetricWindow


class MetricsCommand:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        store: MetricsStorePort | None = None,
        accumulator: MetricsAccumulator | None = None,
    ) -> None:
        self._session = session
        self._store = store or build_metrics_store()
        self._acc = accumulator or MetricsAccumulator(build_counter_store())

    async def ingest_envelope(self, envelope: EventEnvelope) -> None:
        et = envelope.event_type
        if et not in METRICS_EVENT_TYPES and envelope.bus == "metrics":
            return
        if et == "pod.metrics.degraded":
            return
        if et == "pod.metrics.sample":
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
            return
        if et == "metrics.presence.heartbeat":
            await self._presence_set(dict(envelope.payload or {}))
            return
        if et == "metrics.presence.clear":
            await self._presence_clear(dict(envelope.payload or {}))
            return
        if et == "metrics.counter.delta":
            p = dict(envelope.payload or {})
            await self._acc.apply_counter_delta(
                metric=str(p.get("metric") or ""),
                entity_type=str(p.get("entity_type") or ""),
                entity_id=str(p.get("entity_id") or ""),
                delta=int(p.get("delta") or 0),
                company_id=envelope.company_id or p.get("company_id"),
                cabinet_id=envelope.cabinet_id or p.get("cabinet_id"),
                project_id=envelope.project_id or p.get("project_id"),
                at=envelope.occurred_at,
            )
            return
        if et == "metrics.usage.turn":
            p = dict(envelope.payload or {})
            if envelope.project_id and not p.get("project_id"):
                p["project_id"] = envelope.project_id
            if envelope.cabinet_id and not p.get("cabinet_id"):
                p["cabinet_id"] = envelope.cabinet_id
            if envelope.company_id and not p.get("company_id"):
                p["company_id"] = envelope.company_id
            await self._acc.apply_usage_turn(p, at=envelope.occurred_at)
            return
        if et == "metrics.storage.snapshot":
            p = dict(envelope.payload or {})
            await self._acc.apply_storage_snapshot(
                entity_type=str(p.get("entity_type") or ""),
                entity_id=str(p.get("entity_id") or ""),
                bytes_value=int(p.get("bytes") or 0),
                company_id=envelope.company_id or p.get("company_id"),
                cabinet_id=envelope.cabinet_id or p.get("cabinet_id"),
                at=envelope.occurred_at,
            )

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

    async def _presence_set(self, payload: dict[str, Any]) -> None:
        emp = str(payload.get("employee_id") or "").strip()
        if emp:
            await set_presence("employee", emp)
            return
        company_login = str(payload.get("company_login") or "").strip()
        if company_login:
            await set_presence("company", company_login)

    async def _presence_clear(self, payload: dict[str, Any]) -> None:
        emp = str(payload.get("employee_id") or "").strip()
        if emp:
            await clear_presence("employee", emp)
            return
        company_login = str(payload.get("company_login") or "").strip()
        if company_login:
            await clear_presence("company", company_login)
