"""RuntimeObservationService — derive observed_state from live k8s + metrics."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.query import MetricsQuery
from prodavan.application.pod_service.factory import build_pod_metrics, build_pod_runtime
from prodavan.application.pod_service.metrics_sampler import PodMetricsSampler
from prodavan.config.settings import settings
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.pods.observed_state import ObservedState
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_PROVISIONING_PHASES = frozenset({"Pending", "ContainerCreating", "PodInitializing"})


class RuntimeObservationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._metrics_query = MetricsQuery(session)

    async def observe(
        self,
        *,
        project: ProjectRow | None,
        pod: ProjectPodRow | None,
    ) -> dict[str, Any]:
        if project is not None and project.launch_phase == "preparing":
            return self._summary(
                ObservedState.PREPARING,
                orchestrator_status=pod.status if pod else None,
                desired_state=pod.desired_state if pod else None,
            )

        if pod is None:
            if project is not None and project.status == ProjectStatus.PAUSED:
                return self._summary(ObservedState.PAUSED)
            return self._summary(ObservedState.ABSENT)

        if pod.desired_state == PodDesiredState.ABSENT.value:
            if pod.status in {PodStatus.PAUSED, PodStatus.PAUSING}:
                return self._summary(
                    ObservedState.PAUSED,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                )
            return self._summary(
                ObservedState.ABSENT,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
            )

        if pod.status == PodStatus.FAILED:
            return self._summary(
                ObservedState.FAILED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                last_error=pod.last_error,
            )

        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode != "k8s":
            return await self._observe_stub(project, pod)

        return await self._observe_k8s(project, pod)

    async def wait_for_running(
        self,
        *,
        project_id: str,
        timeout_sec: float | None = None,
        poll_sec: float = 2.0,
    ) -> dict[str, Any]:
        deadline = datetime.now(UTC).timestamp() + float(
            timeout_sec or settings.pod_provisioning_timeout_sec
        )
        last: dict[str, Any] | None = None
        while datetime.now(UTC).timestamp() < deadline:
            project = await self._session.get(ProjectRow, project_id)
            pod = await self._get_live_pod(project_id)
            if project is None or pod is None:
                raise RuntimeError("project or pod missing while waiting for running")
            await PodMetricsSampler(self._session).sample_managed_pods()
            await self._session.commit()
            last = await self.observe(project=project, pod=pod)
            state = last.get("observed_state")
            if state == ObservedState.RUNNING.value:
                pod.status = PodStatus.RUNNING
                await self._session.flush()
                return last
            if state in {ObservedState.FAILED.value, ObservedState.DEGRADED.value}:
                raise RuntimeError(
                    f"pod verification failed: observed_state={state} error={last.get('last_error')}"
                )
            await asyncio.sleep(poll_sec)
        raise RuntimeError(
            f"pod not verified running within {timeout_sec or settings.pod_provisioning_timeout_sec}s; "
            f"last={last}"
        )

    async def promote_or_demote(self, *, project: ProjectRow, pod: ProjectPodRow) -> str:
        """Sync DB orchestrator status from observation. Returns action taken."""
        obs = await self.observe(project=project, pod=pod)
        state = obs.get("observed_state")
        now = datetime.now(UTC)

        if pod.status == PodStatus.PROVISIONING:
            age = (now - self._as_utc(pod.updated_at)).total_seconds()
            if age > settings.pod_provisioning_timeout_sec:
                pod.status = PodStatus.FAILED
                pod.last_error = f"provisioning timeout after {int(age)}s"
                if project.status == ProjectStatus.ACTIVE:
                    project.status = ProjectStatus.ERROR
                return "failed_timeout"

        if state == ObservedState.RUNNING.value and pod.status == PodStatus.PROVISIONING:
            pod.status = PodStatus.RUNNING
            pod.last_error = None
            return "promoted"

        if state in {
            ObservedState.FAILED.value,
            ObservedState.DEGRADED.value,
            ObservedState.ABSENT.value,
        } and pod.status == PodStatus.RUNNING and pod.desired_state == PodDesiredState.RUNNING.value:
            pod.status = PodStatus.FAILED
            pod.last_error = obs.get("last_error") or f"observed {state}"
            if project.status == ProjectStatus.ACTIVE:
                project.status = ProjectStatus.ERROR
            return "demoted"

        return "noop"

    async def _observe_k8s(self, project: ProjectRow, pod: ProjectPodRow) -> dict[str, Any]:
        runtime_ref = pod.runtime_ref or ""
        k8s_status: dict[str, Any] = {}
        metrics_body: dict[str, Any] | None = None
        metrics_degraded = False
        degraded_reason: str | None = None

        cached = await self._metrics_query.get_project_runtime_metrics(project.id)
        if cached is not None:
            metrics_degraded = bool(cached.get("degraded"))
            degraded_reason = cached.get("degraded_reason")
            if not metrics_degraded:
                metrics_body = {
                    k: cached[k]
                    for k in ("cpu_millicores", "memory_bytes", "timestamp")
                    if k in cached and cached[k] is not None
                }
            if cached.get("phase") is not None:
                k8s_status["phase"] = cached.get("phase")
            if cached.get("ready") is not None:
                k8s_status["ready"] = cached.get("ready")
            if cached.get("restarts") is not None:
                k8s_status["restarts"] = cached.get("restarts")

        if not k8s_status and runtime_ref:
            try:
                runtime = build_pod_runtime()
                k8s_status = await runtime.get_status(runtime_ref=runtime_ref) or {}
            except Exception as exc:
                logger.exception("observe k8s status failed runtime_ref=%s", runtime_ref)
                return self._summary(
                    ObservedState.UNKNOWN,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    last_error=str(exc)[:500],
                )

        phase = str(k8s_status.get("phase") or "Unknown")
        ready = bool(k8s_status.get("ready"))
        hydrating = bool(k8s_status.get("hydrating"))
        hydrate_failed = bool(k8s_status.get("hydrate_failed"))
        timing: dict[str, Any] = {}
        if k8s_status.get("started_at"):
            timing["started_at"] = k8s_status["started_at"]
        if k8s_status.get("created_at"):
            timing["k8s_created_at"] = k8s_status["created_at"]

        def obs(state: ObservedState, **kw: Any) -> dict[str, Any]:
            return self._summary(state, **timing, **kw)

        if hydrate_failed:
            return obs(
                ObservedState.FAILED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=ready,
                last_error="hydrate initContainer failed",
                restarts=k8s_status.get("restarts"),
            )

        if phase == "NotFound":
            return obs(
                ObservedState.FAILED if pod.desired_state == PodDesiredState.RUNNING.value else ObservedState.ABSENT,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                last_error=pod.last_error or "pod not found in k8s",
            )

        if phase == "Failed":
            return obs(
                ObservedState.FAILED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=ready,
                last_error=pod.last_error or "k8s pod failed",
                restarts=k8s_status.get("restarts"),
            )

        if hydrating:
            return obs(
                ObservedState.HYDRATING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=False,
                restarts=k8s_status.get("restarts"),
            )

        if phase in _PROVISIONING_PHASES:
            age = (datetime.now(UTC) - self._as_utc(pod.updated_at)).total_seconds()
            if age > settings.pod_provisioning_timeout_sec:
                return obs(
                    ObservedState.FAILED,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    phase=phase,
                    ready=ready,
                    last_error=f"provisioning timeout after {int(age)}s",
                    restarts=k8s_status.get("restarts"),
                )
            return obs(
                ObservedState.PROVISIONING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=ready,
                restarts=k8s_status.get("restarts"),
            )

        if phase == "Running" and ready:
            if metrics_body is None and runtime_ref and not metrics_degraded:
                metrics_port = build_pod_metrics()
                if metrics_port is not None:
                    try:
                        live_metrics = await metrics_port.get_pod_metrics(runtime_ref=runtime_ref)
                        if live_metrics:
                            metrics_body = live_metrics
                    except Exception:
                        logger.exception("live metrics fetch failed runtime_ref=%s", runtime_ref)

            if self._metrics_verified(metrics_body, cached):
                return obs(
                    ObservedState.RUNNING,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    phase=phase,
                    ready=ready,
                    metrics=metrics_body,
                    metrics_fresh=True,
                    restarts=k8s_status.get("restarts"),
                )

            grace_age = (datetime.now(UTC) - self._as_utc(pod.updated_at)).total_seconds()
            if grace_age <= settings.pod_metrics_grace_sec:
                return obs(
                    ObservedState.STARTING,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    phase=phase,
                    ready=ready,
                    metrics_degraded=metrics_degraded,
                    metrics_degraded_reason=degraded_reason,
                    restarts=k8s_status.get("restarts"),
                )

            return obs(
                ObservedState.DEGRADED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=ready,
                metrics_degraded=True,
                metrics_degraded_reason=degraded_reason or "metrics-server unavailable",
                last_error=degraded_reason or "metrics not verified within grace period",
                restarts=k8s_status.get("restarts"),
            )

        return obs(
            ObservedState.UNKNOWN,
            orchestrator_status=pod.status,
            desired_state=pod.desired_state,
            phase=phase,
            ready=ready,
            restarts=k8s_status.get("restarts"),
        )

    async def _observe_stub(self, project: ProjectRow, pod: ProjectPodRow) -> dict[str, Any]:
        cached = await self._metrics_query.get_project_runtime_metrics(project.id)
        if self._metrics_verified(None, cached):
            metrics_body = {
                k: cached[k]  # type: ignore[index]
                for k in ("cpu_millicores", "memory_bytes", "timestamp")
                if cached.get(k) is not None  # type: ignore[union-attr]
            }
            return self._summary(
                ObservedState.RUNNING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase="Running",
                ready=True,
                metrics=metrics_body,
                metrics_fresh=True,
                restarts=cached.get("restarts") if cached else 0,
                started_at=cached.get("timestamp") if cached else None,
                stub=True,
            )

        if pod.status == PodStatus.PROVISIONING:
            age = (datetime.now(UTC) - self._as_utc(pod.updated_at)).total_seconds()
            if age > settings.pod_provisioning_timeout_sec:
                return self._summary(
                    ObservedState.FAILED,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    last_error=f"stub provisioning timeout after {int(age)}s",
                    stub=True,
                )
            return self._summary(
                ObservedState.STARTING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                stub=True,
            )

        if pod.status == PodStatus.RUNNING:
            return self._summary(
                ObservedState.UNKNOWN,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                last_error="stub pod without verified metrics",
                stub=True,
            )

        return self._summary(
            ObservedState.UNKNOWN,
            orchestrator_status=pod.status,
            desired_state=pod.desired_state,
            stub=True,
        )

    @staticmethod
    def _metrics_verified(
        metrics_body: dict[str, Any] | None,
        cached: dict[str, Any] | None,
    ) -> bool:
        if cached and cached.get("degraded"):
            return False
        source = metrics_body or cached
        if not source:
            return False
        cpu = source.get("cpu_millicores")
        mem = source.get("memory_bytes")
        if cpu is None or mem is None:
            return False
        ts = source.get("timestamp")
        if not ts:
            return False
        try:
            text = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - dt).total_seconds()
            return age <= max(60, int(settings.metrics_sample_ttl_sec)) * 2
        except (TypeError, ValueError):
            return False

    async def _get_live_pod(self, project_id: str) -> ProjectPodRow | None:
        from sqlalchemy import select

        from prodavan.domain.pods import POD_TERMINAL_STATUSES

        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        return q.scalar_one_or_none()

    @staticmethod
    def _as_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @staticmethod
    def _summary(
        observed_state: ObservedState,
        *,
        orchestrator_status: str | None = None,
        desired_state: str | None = None,
        phase: str | None = None,
        ready: bool | None = None,
        metrics: dict[str, Any] | None = None,
        metrics_fresh: bool = False,
        metrics_degraded: bool = False,
        metrics_degraded_reason: str | None = None,
        last_error: str | None = None,
        restarts: Any = None,
        stub: bool = False,
        started_at: str | None = None,
        k8s_created_at: str | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "observed_state": observed_state.value,
            "observed_at": EventEnvelope.now_iso(),
        }
        if orchestrator_status is not None:
            out["orchestrator_status"] = orchestrator_status
        if desired_state is not None:
            out["desired_state"] = desired_state
        if phase is not None:
            out["phase"] = phase
        if ready is not None:
            out["ready"] = ready
        if metrics:
            out["metrics"] = metrics
        out["metrics_fresh"] = metrics_fresh
        if metrics_degraded:
            out["metrics_degraded"] = True
            if metrics_degraded_reason:
                out["metrics_degraded_reason"] = metrics_degraded_reason
        if last_error:
            out["last_error"] = last_error
        if restarts is not None:
            out["restarts"] = restarts
        if started_at:
            out["started_at"] = started_at
        if k8s_created_at:
            out["k8s_created_at"] = k8s_created_at
        if stub:
            out["stub"] = True
        return out


async def emit_stub_metrics_heartbeat(
    session: AsyncSession,
    *,
    project: ProjectRow,
    pod: ProjectPodRow,
) -> None:
    """Write synthetic CPU/RAM sample so stub mode can reach verified running."""
    from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
    from prodavan.application.metrics.command import MetricsCommand
    from prodavan.core.events.envelope import metrics_envelope

    ts = EventEnvelope.now_iso()
    payload = {
        "pod_id": pod.id,
        "cpu_millicores": 1,
        "memory_bytes": 32 * 1024 * 1024,
        "phase": "Running",
        "restarts": 0,
        "ready": True,
        "timestamp": ts,
        "stub": True,
    }
    store = build_metrics_store()
    envelope = metrics_envelope(
        event_id=f"met_stub_{pod.id[:8]}",
        event_type="pod.metrics.sample",
        company_id=project.company_id,
        project_id=project.id,
        cabinet_id=project.cabinet_id,
        payload=payload,
        occurred_at=ts,
    )
    await MetricsCommand(session, store=store).ingest_envelope(envelope)
