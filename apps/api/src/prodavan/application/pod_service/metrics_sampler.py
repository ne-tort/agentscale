"""Pod runtime metrics sampler — publish to metrics Kafka topic."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.command import MetricsCommand
from prodavan.application.metrics.delta import should_emit_sample
from prodavan.application.pod_service.factory import build_pod_metrics, build_pod_runtime
from prodavan.config.settings import settings
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.pods import PodStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)


class PodMetricsSampler:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = build_metrics_store()

    async def sample_managed_pods(self) -> dict[str, int]:
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode != "k8s":
            return {"sampled": 0, "skipped": 0, "degraded": 0}

        metrics_port = build_pod_metrics()
        runtime_port = build_pod_runtime()
        if metrics_port is None:
            return {"sampled": 0, "skipped": 0, "degraded": 0}

        sampled = 0
        skipped = 0
        degraded = 0

        q = await self._session.execute(
            select(ProjectPodRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ProjectPodRow.project_id)
            .where(
                ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
                ProjectPodRow.runtime_ref.is_not(None),
            )
        )
        for pod, project in q.all():
            if not pod.runtime_ref:
                continue
            emitted = await self._sample_one(
                pod=pod,
                project=project,
                metrics_port=metrics_port,
                runtime_port=runtime_port,
            )
            if emitted == "sampled":
                sampled += 1
            elif emitted == "degraded":
                degraded += 1
            else:
                skipped += 1

        return {"sampled": sampled, "skipped": skipped, "degraded": degraded}

    async def _sample_one(
        self,
        *,
        pod: ProjectPodRow,
        project: ProjectRow,
        metrics_port: Any,
        runtime_port: Any,
    ) -> str:
        from prodavan.core.events.deferred import schedule_metrics_event_publish

        runtime_ref = pod.runtime_ref or ""
        status: dict[str, Any] = {}
        metrics: dict[str, Any] | None = None
        try:
            status = await runtime_port.get_status(runtime_ref=runtime_ref) or {}
            metrics = await metrics_port.get_pod_metrics(runtime_ref=runtime_ref)
        except Exception:
            logger.exception("pod metrics sampler failed runtime_ref=%s", runtime_ref)
            await self._emit_degraded(
                pod=pod,
                project=project,
                reason="metrics-server unavailable",
            )
            return "degraded"

        if metrics is None:
            await self._emit_degraded(
                pod=pod,
                project=project,
                reason="metrics-server unavailable",
            )
            return "degraded"

        ts = EventEnvelope.now_iso()
        current = {
            "pod_id": pod.id,
            "cpu_millicores": metrics.get("cpu_millicores"),
            "memory_bytes": metrics.get("memory_bytes"),
            "phase": status.get("phase"),
            "restarts": status.get("restarts"),
            "ready": status.get("ready"),
            "timestamp": ts,
        }
        last = await self._store.get_pod_last_sample(pod.id)
        if not should_emit_sample(last=last, current=current):
            return "skipped"

        event_id = f"met_{uuid.uuid4().hex[:16]}"
        schedule_metrics_event_publish(
            self._session,
            event_id=event_id,
            event_type="pod.metrics.sample",
            company_id=project.company_id,
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            payload=current,
            occurred_at=ts,
        )
        await self._store.put_pod_last_sample(pod.id, current)

        if not settings.kafka_enabled:
            from prodavan.core.events.envelope import metrics_envelope

            envelope = metrics_envelope(
                event_id=event_id,
                event_type="pod.metrics.sample",
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                payload=current,
                occurred_at=ts,
            )
            await MetricsCommand(self._session, store=self._store).ingest_envelope(envelope)

        return "sampled"

    async def _emit_degraded(
        self,
        *,
        pod: ProjectPodRow,
        project: ProjectRow,
        reason: str,
    ) -> None:
        from prodavan.core.events.deferred import schedule_metrics_event_publish

        ts = EventEnvelope.now_iso()
        event_id = f"met_{uuid.uuid4().hex[:16]}"
        payload = {"pod_id": pod.id, "reason": reason, "degraded": True, "timestamp": ts}
        schedule_metrics_event_publish(
            self._session,
            event_id=event_id,
            event_type="pod.metrics.degraded",
            company_id=project.company_id,
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            payload=payload,
            occurred_at=ts,
        )
        if not settings.kafka_enabled:
            from prodavan.core.events.envelope import metrics_envelope

            envelope = metrics_envelope(
                event_id=event_id,
                event_type="pod.metrics.degraded",
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                payload=payload,
                occurred_at=ts,
            )
            await MetricsCommand(self._session, store=self._store).ingest_envelope(envelope)
