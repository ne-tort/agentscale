"""Pod runtime metrics sampler — publish to metrics Kafka topic + Redis hot store."""

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
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)


class PodMetricsSampler:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = build_metrics_store()

    async def sample_managed_pods(self) -> dict[str, int]:
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode not in ("k8s", "sandbox"):
            return {"sampled": 0, "skipped": 0}

        metrics_port = build_pod_metrics()
        runtime_port = build_pod_runtime()
        if metrics_port is None:
            return {"sampled": 0, "skipped": 0}

        sampled = 0
        skipped = 0

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
            else:
                skipped += 1

        return {"sampled": sampled, "skipped": skipped}

    async def sample_project(self, project_id: str) -> str:
        """Opportunistic sample for one project (e.g. runtime_view read path)."""
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode not in ("k8s", "sandbox"):
            return "noop"

        metrics_port = build_pod_metrics()
        runtime_port = build_pod_runtime()
        if metrics_port is None:
            return "noop"

        q = await self._session.execute(
            select(ProjectPodRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ProjectPodRow.project_id)
            .where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
                ProjectPodRow.runtime_ref.is_not(None),
            )
        )
        row = q.first()
        if row is None:
            return "absent"
        pod, project = row
        if project.status != ProjectStatus.ACTIVE:
            return "inactive"
        if pod.desired_state != PodDesiredState.RUNNING.value:
            return "not_running"
        return await self._sample_one(
            pod=pod,
            project=project,
            metrics_port=metrics_port,
            runtime_port=runtime_port,
        )

    async def cache_live_metrics(
        self,
        *,
        pod: ProjectPodRow,
        project: ProjectRow,
        metrics: dict[str, Any],
        status: dict[str, Any] | None = None,
    ) -> None:
        """Write-through when observation fetched metrics directly from k8s."""
        ts = EventEnvelope.now_iso()
        current = {
            "pod_id": pod.id,
            "cpu_millicores": metrics.get("cpu_millicores"),
            "memory_bytes": metrics.get("memory_bytes"),
            "phase": (status or {}).get("phase"),
            "restarts": (status or {}).get("restarts"),
            "ready": (status or {}).get("ready"),
            "timestamp": ts,
        }
        await self._ingest_hot(
            pod=pod,
            project=project,
            payload=current,
            event_type="pod.metrics.sample",
        )

    async def _sample_one(
        self,
        *,
        pod: ProjectPodRow,
        project: ProjectRow,
        metrics_port: Any,
        runtime_port: Any,
    ) -> str:
        runtime_ref = pod.runtime_ref or ""
        status: dict[str, Any] = {}
        metrics: dict[str, Any] | None = None
        try:
            status = await runtime_port.get_status(runtime_ref=runtime_ref) or {}
            metrics = await metrics_port.get_pod_metrics(
                runtime_ref=runtime_ref,
                # sandbox mode: the status just fetched carries the sandbox
                # (pod) name — pass it through to skip a duplicate claim GET.
                sandbox_name=str(status.get("sandbox_name") or "") or None,
            )
        except Exception:
            logger.debug("pod metrics sampler skipped runtime_ref=%s", runtime_ref, exc_info=True)
            return "skipped"

        if metrics is None:
            return "skipped"

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
            await self._heartbeat_sample(pod=pod, project=project, current=current)
            return "skipped"

        await self._ingest_hot(
            pod=pod,
            project=project,
            payload=current,
            event_type="pod.metrics.sample",
        )
        await self._store.put_pod_last_sample(pod.id, current)
        return "sampled"

    async def _heartbeat_sample(
        self,
        *,
        pod: ProjectPodRow,
        project: ProjectRow,
        current: dict[str, Any],
    ) -> None:
        """Refresh hot-store timestamp when values unchanged — prevents TTL expiry."""
        latest = await self._store.get_project_latest(project.id)
        if latest is None or latest.get("degraded"):
            return
        if latest.get("cpu_millicores") is None or latest.get("memory_bytes") is None:
            return
        ts = EventEnvelope.now_iso()
        refreshed = {
            **latest,
            "pod_id": pod.id,
            "timestamp": ts,
        }
        for key in ("phase", "restarts", "ready"):
            if current.get(key) is not None:
                refreshed[key] = current[key]
        await self._store.put_project_latest(project.id, refreshed)
        await self._store.put_pod_last_sample(pod.id, refreshed)

    async def _ingest_hot(
        self,
        *,
        pod: ProjectPodRow,
        project: ProjectRow,
        payload: dict[str, Any],
        event_type: str,
    ) -> None:
        from prodavan.core.events.envelope import metrics_envelope

        ts = payload.get("timestamp") or EventEnvelope.now_iso()
        body = dict(payload)
        body["timestamp"] = ts
        event_id = f"met_{uuid.uuid4().hex[:16]}"
        envelope = metrics_envelope(
            event_id=event_id,
            event_type=event_type,
            company_id=project.company_id,
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            payload=body,
            occurred_at=ts,
        )
        await MetricsCommand(self._session, store=self._store).ingest_envelope(envelope)
        if settings.kafka_enabled:
            from prodavan.core.events.deferred import schedule_metrics_event_publish

            schedule_metrics_event_publish(
                self._session,
                event_id=event_id,
                event_type=event_type,
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                payload=body,
                occurred_at=ts,
            )
