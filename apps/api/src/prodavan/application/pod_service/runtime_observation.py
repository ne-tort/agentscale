"""RuntimeObservationService — derive observed_state from live k8s; metrics are display-only."""

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
_UNHEALTHY_OBSERVED = frozenset(
    {
        ObservedState.FAILED.value,
        ObservedState.ABSENT.value,
    }
)
_TRANSITIONAL_OBSERVED = frozenset(
    {
        ObservedState.PREPARING.value,
        ObservedState.PROVISIONING.value,
        ObservedState.PULLING.value,
        ObservedState.HYDRATING.value,
        ObservedState.STARTING.value,
    }
)


def project_is_recoverable(
    project: ProjectRow,
    pod: ProjectPodRow | None,
    obs: dict[str, Any] | None,
) -> bool:
    """True when reload/recovery is meaningful (project or container unhealthy)."""
    if project.status == ProjectStatus.ERROR:
        return True
    if project.status != ProjectStatus.ACTIVE or pod is None:
        return False
    if pod.status == PodStatus.FAILED and bool((pod.last_error or "").strip()):
        return True
    state = (obs or {}).get("observed_state")
    if state in _UNHEALTHY_OBSERVED:
        return True
    return state == ObservedState.UNKNOWN.value and bool((obs or {}).get("last_error"))


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
            # Sticky FAILED short-circuit only when we are not trying to run —
            # otherwise re-observe k8s so a recovered node can promote again.
            if pod.desired_state != PodDesiredState.RUNNING.value:
                return self._summary(
                    ObservedState.FAILED,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    last_error=pod.last_error,
                )

        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode == "sandbox":
            return await self._observe_sandbox(project, pod)
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
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode != "k8s" and mode != "sandbox":
            # Stub only: no cluster to wait for — promote instantly.
            project = await self._session.get(ProjectRow, project_id)
            pod = await self._get_live_pod(project_id)
            if project is None or pod is None:
                raise RuntimeError("project or pod missing while waiting for running")
            obs = await self.observe(project=project, pod=pod)
            if (
                pod.status == PodStatus.PROVISIONING
                and pod.desired_state == PodDesiredState.RUNNING.value
            ):
                pod.status = PodStatus.RUNNING
                pod.last_error = None
                pod.last_started_at = datetime.now(UTC)
                await self._session.flush()
            return obs

        # k8s AND sandbox poll the real runtime: in sandbox mode an
        # instant PROVISIONING→RUNNING promotion would register sessions
        # against a sandbox that is not Ready yet (bind/register race).
        if timeout_sec is not None:
            budget = float(timeout_sec)
        elif mode == "sandbox":
            # No image pull in sandbox mode (warm-pool adoption) — the
            # budget is provisioning + readiness of the adopted pod.
            budget = float(
                settings.pod_provisioning_timeout_sec + settings.pod_ready_timeout_sec
            )
        else:
            budget = float(
                settings.pod_image_pull_timeout_sec + settings.pod_ready_timeout_sec
            )
        deadline = datetime.now(UTC).timestamp() + budget
        last: dict[str, Any] | None = None
        while datetime.now(UTC).timestamp() < deadline:
            project = await self._session.get(ProjectRow, project_id)
            pod = await self._get_live_pod(project_id)
            if project is None or pod is None:
                raise RuntimeError("project or pod missing while waiting for running")
            last = await self.observe(project=project, pod=pod)
            state = last.get("observed_state")
            if state == ObservedState.RUNNING.value:
                pod.status = PodStatus.RUNNING
                pod.last_started_at = datetime.now(UTC)
                await self._session.flush()
                return last
            if state == ObservedState.FAILED.value:
                raise RuntimeError(
                    f"pod verification failed: observed_state={state} error={last.get('last_error')}"
                )
            await asyncio.sleep(poll_sec)
        raise RuntimeError(
            f"pod not verified running within {budget}s; last={last}"
        )

    async def sync_runtime_health(self, *, project: ProjectRow, pod: ProjectPodRow) -> str:
        """Sync project/pod DB status from live observation. Returns action taken."""
        return await self.promote_or_demote(project=project, pod=pod)

    async def promote_or_demote(self, *, project: ProjectRow, pod: ProjectPodRow) -> str:
        """Sync DB orchestrator status from observation. Returns action taken."""
        now = datetime.now(UTC)
        obs = await self.observe(project=project, pod=pod)
        state = obs.get("observed_state")

        if pod.desired_state == PodDesiredState.ABSENT.value:
            if state in {ObservedState.PAUSED.value, ObservedState.ABSENT.value}:
                return "noop"

        if pod.status == PodStatus.PROVISIONING:
            age = (now - self._as_utc(pod.updated_at)).total_seconds()
            fail_observed = state == ObservedState.FAILED.value or (
                state == ObservedState.UNKNOWN.value and bool(obs.get("last_error"))
            )
            budget = (
                settings.pod_image_pull_timeout_sec
                if state == ObservedState.PULLING.value
                else settings.pod_provisioning_timeout_sec
            )
            if age > budget or fail_observed:
                pod.status = PodStatus.FAILED
                pod.last_error = obs.get("last_error") or f"provisioning timeout after {int(age)}s"
                if project.status == ProjectStatus.ACTIVE:
                    project.status = ProjectStatus.ERROR
                return "failed_timeout"

        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode != "k8s":
            # When a PROVISIONING pod may be promoted to RUNNING. Stub mode
            # has no cluster: transitional observations are the best signal
            # (L2 semantics). Sandbox mode has a real claim behind the ref —
            # "provisioning"/"hydrating" observations must NOT promote
            # (the sandbox is not Ready yet; bind/register would race), and
            # the observed `running` state must (it is the only path that
            # promotes a healthy sandbox — without it the pod stays
            # PROVISIONING forever while the claim is Ready).
            promote_states = (
                {ObservedState.RUNNING.value}
                if mode == "sandbox"
                else _TRANSITIONAL_OBSERVED
            )
            if (
                pod.status == PodStatus.PROVISIONING
                and pod.desired_state == PodDesiredState.RUNNING.value
                and state in promote_states
            ):
                pod.status = PodStatus.RUNNING
                pod.last_error = None
                pod.last_started_at = now
                return "promoted"
            if (
                state == ObservedState.RUNNING.value
                and pod.desired_state == PodDesiredState.RUNNING.value
                and (
                    pod.status == PodStatus.FAILED
                    or project.status == ProjectStatus.ERROR
                )
            ):
                pod.status = PodStatus.RUNNING
                pod.last_error = None
                pod.last_started_at = now
                if project.status == ProjectStatus.ERROR:
                    project.status = ProjectStatus.ACTIVE
                return "promoted"
            if (
                pod.status == PodStatus.RUNNING
                and pod.desired_state == PodDesiredState.RUNNING.value
                and state != ObservedState.RUNNING.value
            ):
                age = (now - self._as_utc(pod.updated_at)).total_seconds()
                transitional = state in _TRANSITIONAL_OBSERVED
                flap = state in {
                    ObservedState.ABSENT.value,
                    ObservedState.UNKNOWN.value,
                }
                if (
                    (not transitional and not flap)
                    or age > settings.pod_provisioning_timeout_sec
                    or state == ObservedState.FAILED.value
                ):
                    pod.status = PodStatus.FAILED
                    pod.last_error = obs.get("last_error") or f"observed {state}"
                    if project.status == ProjectStatus.ACTIVE:
                        project.status = ProjectStatus.ERROR
                    return "demoted"
            return "noop"

        if state == ObservedState.RUNNING.value and pod.desired_state == PodDesiredState.RUNNING.value:
            if pod.status == PodStatus.PROVISIONING:
                pod.status = PodStatus.RUNNING
                pod.last_error = None
                pod.last_started_at = now
                return "promoted"
            if pod.status == PodStatus.FAILED or project.status == ProjectStatus.ERROR:
                pod.status = PodStatus.RUNNING
                pod.last_error = None
                pod.last_started_at = now
                if project.status == ProjectStatus.ERROR:
                    project.status = ProjectStatus.ACTIVE
                return "promoted"

        age = (now - self._as_utc(pod.updated_at)).total_seconds()
        flap_observed = state in {
            ObservedState.ABSENT.value,
            ObservedState.UNKNOWN.value,
        }
        within_flap_grace = (
            flap_observed
            and pod.desired_state == PodDesiredState.RUNNING.value
            and age <= settings.pod_provisioning_timeout_sec
        )
        if within_flap_grace:
            return "noop"

        if (
            pod.status == PodStatus.RUNNING
            and pod.desired_state == PodDesiredState.RUNNING.value
            and state
            not in {
                ObservedState.RUNNING.value,
                ObservedState.PAUSED.value,
            }
        ):
            transitional = state in _TRANSITIONAL_OBSERVED
            if not transitional or age > settings.pod_provisioning_timeout_sec:
                pod.status = PodStatus.FAILED
                pod.last_error = obs.get("last_error") or f"observed {state}"
                if project.status == ProjectStatus.ACTIVE:
                    project.status = ProjectStatus.ERROR
                return "demoted"

        unhealthy = state in _UNHEALTHY_OBSERVED or (
            state == ObservedState.UNKNOWN.value and bool(obs.get("last_error"))
        )

        if (
            unhealthy
            and pod.status == PodStatus.RUNNING
            and pod.desired_state == PodDesiredState.RUNNING.value
        ):
            pod.status = PodStatus.FAILED
            pod.last_error = obs.get("last_error") or f"observed {state}"
            if project.status == ProjectStatus.ACTIVE:
                project.status = ProjectStatus.ERROR
            return "demoted"

        if (
            project.status == ProjectStatus.ACTIVE
            and pod.desired_state == PodDesiredState.RUNNING.value
            and state in _UNHEALTHY_OBSERVED
            and pod.status not in {PodStatus.FAILED, PodStatus.TERMINATED}
        ):
            pod.status = PodStatus.FAILED
            pod.last_error = obs.get("last_error") or f"observed {state}"
            project.status = ProjectStatus.ERROR
            return "demoted"

        return "noop"

    async def _observe_k8s(self, project: ProjectRow, pod: ProjectPodRow) -> dict[str, Any]:
        runtime_ref = pod.runtime_ref or ""
        k8s_status: dict[str, Any] = {}
        metrics_body: dict[str, Any] | None = None

        cached = await self._metrics_query.get_project_runtime_metrics(project.id)
        if cached is not None:
            if not cached.get("degraded"):
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

        if runtime_ref:
            try:
                runtime = build_pod_runtime()
                live = await runtime.get_status(runtime_ref=runtime_ref) or {}
                k8s_status = {**k8s_status, **live}
            except Exception as exc:
                logger.exception("observe k8s status failed runtime_ref=%s", runtime_ref)
                if not k8s_status:
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
        pulling = bool(k8s_status.get("pulling"))
        waiting_reason = str(k8s_status.get("waiting_reason") or "").strip() or None
        fatal_failure = str(k8s_status.get("fatal_failure") or "").strip() or None
        timing: dict[str, Any] = {}
        if k8s_status.get("started_at"):
            timing["started_at"] = k8s_status["started_at"]
        if k8s_status.get("created_at"):
            timing["k8s_created_at"] = k8s_status["created_at"]

        def obs(state: ObservedState, **kw: Any) -> dict[str, Any]:
            return self._summary(state, waiting_reason=waiting_reason, **timing, **kw)

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

        if fatal_failure:
            return obs(
                ObservedState.FAILED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=ready,
                last_error=fatal_failure,
                restarts=k8s_status.get("restarts"),
            )

        if phase == "NotFound":
            # Node/WSL flap: absent, not permanent FAILED — reconcile can recreate.
            if pod.desired_state == PodDesiredState.RUNNING.value:
                return obs(
                    ObservedState.ABSENT,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    phase=phase,
                    last_error=pod.last_error or "pod not found in k8s",
                )
            return obs(
                ObservedState.ABSENT,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                last_error=pod.last_error,
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

        if pulling:
            age = (datetime.now(UTC) - self._as_utc(pod.updated_at)).total_seconds()
            if age > settings.pod_image_pull_timeout_sec:
                return obs(
                    ObservedState.FAILED,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    phase=phase,
                    ready=ready,
                    last_error=f"image pull timeout after {int(age)}s",
                    restarts=k8s_status.get("restarts"),
                )
            return obs(
                ObservedState.PULLING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=False,
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
            if metrics_body is None and runtime_ref:
                metrics_port = build_pod_metrics()
                if metrics_port is not None:
                    try:
                        live_metrics = await metrics_port.get_pod_metrics(runtime_ref=runtime_ref)
                        if live_metrics:
                            metrics_body = {
                                **live_metrics,
                                "timestamp": EventEnvelope.now_iso(),
                            }
                            await PodMetricsSampler(self._session).cache_live_metrics(
                                pod=pod,
                                project=project,
                                metrics=live_metrics,
                                status=k8s_status,
                            )
                    except Exception:
                        logger.exception("live metrics fetch failed runtime_ref=%s", runtime_ref)

            metrics_available = self._metrics_available(metrics_body, cached)
            return obs(
                ObservedState.RUNNING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                phase=phase,
                ready=ready,
                metrics=metrics_body if metrics_available else None,
                metrics_fresh=metrics_available,
                metrics_available=metrics_available,
                metrics_unavailable_reason=(
                    None if metrics_available else "metrics not yet available"
                ),
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

    async def _observe_sandbox(self, project: ProjectRow, pod: ProjectPodRow) -> dict[str, Any]:
        """agent-sandbox mode — observed_state from the sandbox adapter contract.

        Adapter get_status returns ``observed_state`` in
        {running, suspended, pausing, provisioning, absent, failed}; map to
        ObservedState and keep the runtime detail fields for the UI.
        """
        runtime_ref = pod.runtime_ref or ""
        status: dict[str, Any] = {}
        if runtime_ref:
            try:
                runtime = build_pod_runtime()
                status = await runtime.get_status(runtime_ref=runtime_ref) or {}
            except Exception as exc:
                logger.exception("observe sandbox status failed runtime_ref=%s", runtime_ref)
                return self._summary(
                    ObservedState.UNKNOWN,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    last_error=str(exc)[:500],
                    sandbox=True,
                )

        state = str(status.get("observed_state") or "absent")
        _SANDBOX_STATE_TO_OBSERVED = {
            "running": ObservedState.RUNNING,
            "suspended": ObservedState.SUSPENDED,
            "pausing": ObservedState.PAUSING,
            "provisioning": ObservedState.PROVISIONING,
            "absent": ObservedState.ABSENT,
            "failed": ObservedState.FAILED,
        }
        observed = _SANDBOX_STATE_TO_OBSERVED.get(state, ObservedState.UNKNOWN)

        kw: dict[str, Any] = {
            "orchestrator_status": pod.status,
            "desired_state": pod.desired_state,
            "sandbox": True,
        }
        if status.get("phase") is not None:
            kw["phase"] = status.get("phase")
        if status.get("ready") is not None:
            kw["ready"] = status.get("ready")
        if status.get("waiting_reason"):
            kw["waiting_reason"] = status.get("waiting_reason")
        if status.get("claim_name") or runtime_ref:
            kw["claim_name"] = status.get("claim_name") or runtime_ref
        if status.get("sandbox_name"):
            kw["sandbox_name"] = status.get("sandbox_name")
        if status.get("service_fqdn"):
            kw["service_fqdn"] = status.get("service_fqdn")
        if status.get("launch_type"):
            kw["launch_type"] = status.get("launch_type")
        if status.get("restarts") is not None:
            kw["restarts"] = status.get("restarts")
        if state == "failed":
            kw["last_error"] = status.get("waiting_reason") or "sandbox claim failed"
        if observed == ObservedState.ABSENT and pod.desired_state == PodDesiredState.RUNNING.value:
            # Flap tolerance mirrors k8s: absent is reconcile-recoverable.
            kw["last_error"] = pod.last_error or "sandbox claim not found"
        return self._summary(observed, **kw)

    async def _observe_stub(self, project: ProjectRow, pod: ProjectPodRow) -> dict[str, Any]:
        """Stub mode — no k8s pod; promote DB RUNNING for in-process agent tests."""
        if (
            pod.status == PodStatus.RUNNING
            and pod.desired_state == PodDesiredState.RUNNING.value
        ):
            return self._summary(
                ObservedState.RUNNING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                stub=True,
            )
        if pod.status == PodStatus.FAILED:
            return self._summary(
                ObservedState.FAILED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                last_error=pod.last_error,
                stub=True,
            )
        if pod.desired_state == PodDesiredState.ABSENT.value or pod.status in {
            PodStatus.PAUSED,
            PodStatus.PAUSING,
        }:
            return self._summary(
                ObservedState.PAUSED,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                stub=True,
            )
        if pod.desired_state == PodDesiredState.RUNNING.value:
            age = (datetime.now(UTC) - self._as_utc(pod.updated_at)).total_seconds()
            if age > settings.pod_provisioning_timeout_sec:
                return self._summary(
                    ObservedState.FAILED,
                    orchestrator_status=pod.status,
                    desired_state=pod.desired_state,
                    last_error=(
                        "pod runtime unavailable (no k8s pod verified within "
                        f"{int(settings.pod_provisioning_timeout_sec)}s)"
                    ),
                    stub=True,
                )
            return self._summary(
                ObservedState.STARTING,
                orchestrator_status=pod.status,
                desired_state=pod.desired_state,
                stub=True,
            )
        return self._summary(
            ObservedState.PROVISIONING,
            orchestrator_status=pod.status,
            desired_state=pod.desired_state,
            stub=True,
        )

    @staticmethod
    def _metrics_available(
        metrics_body: dict[str, Any] | None,
        cached: dict[str, Any] | None,
    ) -> bool:
        """True when a fresh CPU/RAM sample exists for UI display (not health)."""
        source = metrics_body
        if source is None and cached is not None and not cached.get("degraded"):
            source = cached
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
            return age <= min(60, int(settings.metrics_sample_ttl_sec))
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
        metrics_available: bool | None = None,
        metrics_unavailable_reason: str | None = None,
        last_error: str | None = None,
        restarts: Any = None,
        stub: bool = False,
        started_at: str | None = None,
        k8s_created_at: str | None = None,
        waiting_reason: str | None = None,
        sandbox: bool = False,
        sandbox_name: str | None = None,
        claim_name: str | None = None,
        service_fqdn: str | None = None,
        launch_type: str | None = None,
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
        if sandbox:
            out["sandbox"] = True
        if sandbox_name:
            out["sandbox_name"] = sandbox_name
        if claim_name:
            out["claim_name"] = claim_name
        if service_fqdn:
            out["service_fqdn"] = service_fqdn
        if launch_type:
            out["launch_type"] = launch_type
        if waiting_reason:
            out["waiting_reason"] = waiting_reason
        if metrics:
            out["metrics"] = metrics
        out["metrics_fresh"] = metrics_fresh
        if metrics_available is not None:
            out["metrics_available"] = metrics_available
        if metrics_unavailable_reason:
            out["metrics_unavailable_reason"] = metrics_unavailable_reason
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
