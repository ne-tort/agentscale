"""Pod reconcile — drift correction worker."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.factory import build_pod_runtime
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort
from prodavan.application.pod_service.query import PodQuery
from prodavan.application.pod_service.runtime_observation import RuntimeObservationService
from prodavan.config.settings import settings
from prodavan.core.infra.advisory_lock import advisory_lock
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_SYSTEM = Principal(sub="system:pod-reconcile", roles=frozenset({"platform.admin"}))
# Do not reap pods still pulling images (pull budget + Ready + slack).
_PROVISIONING_GRACE_SEC = float(
    settings.pod_image_pull_timeout_sec + settings.pod_ready_timeout_sec + 40
)

# Session-level PG advisory lock key so only one API process runs reconcile at a
# time, even on multi-replica deployments (audit POD-P2a). PG-backed (not Redis)
# so it stays effective when Redis is down — PG is the system of record.
_RECONCILE_LOCK_KEY = "prodavan.pod_reconcile"


def _managed_pod_age_sec(item: dict) -> float | None:
    created = item.get("created_at")
    if not created:
        return None
    try:
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - dt).total_seconds())


class PodReconcileService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        runtime: PodRuntimePort | None = None,
    ) -> None:
        self._session = session
        self._runtime = runtime or build_pod_runtime()
        self._pods = PodCommand(session, runtime=self._runtime, events=PodLifecycleEmitter(session))

    async def run(self) -> dict:
        # POD-P2a: PG advisory lock so multi-replica deployments (or the admin
        # /reconcile endpoint racing the Celery beat) do not run two reconcile
        # passes in parallel — the zombie reaper and sync_desired are not safe
        # under concurrent reconcile. PG-backed so it works when Redis is down.
        async with advisory_lock(self._session, _RECONCILE_LOCK_KEY) as held:
            if not held:
                return {"skipped": True, "reason": "lock_held"}
            return await self._run_reconcile()

    async def _run_reconcile(self) -> dict:
        fixed = await self._clear_stale_preparing_markers()
        resumed_projects: list[str] = []
        # Include FAILED pods that still desire RUNNING so WSL/node flaps can heal.
        q = await self._session.execute(
            select(ProjectPodRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ProjectPodRow.project_id, isouter=True)
            .where(
                or_(
                    ProjectRow.launch_phase == "resuming",
                    ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
                    (ProjectPodRow.status == PodStatus.FAILED)
                    & (ProjectPodRow.desired_state == PodDesiredState.RUNNING.value),
                )
            )
        )
        for pod, project in q.all():
            if project is None:
                if pod.runtime_ref:
                    await self._runtime.terminate(runtime_ref=pod.runtime_ref)
                    pod.status = PodStatus.TERMINATED
                    fixed += 1
                continue
            # Resume is intentionally asynchronous in the HTTP path. If the API
            # process dies after persisting this marker, reconcile must finish the
            # transition instead of leaving the project paused forever.
            if project.launch_phase == "resuming":
                await self._recover_interrupted_resume(project, pod)
                if project.status == ProjectStatus.ACTIVE and project.launch_phase == "resuming":
                    resumed_projects.append(project.id)
                fixed += 1
                continue
            # Keep desired=RUNNING for ERROR projects that still want a live pod.
            if pod.desired_state == PodDesiredState.RUNNING.value and project.status in {
                ProjectStatus.ACTIVE,
                ProjectStatus.ERROR,
            }:
                desired = PodDesiredState.RUNNING
            elif project.status == ProjectStatus.ACTIVE:
                desired = PodDesiredState.RUNNING
            else:
                desired = PodDesiredState.ABSENT
            drift = pod.desired_state != desired.value or not PodCommand._status_matches_desired(
                pod, desired
            )
            if drift:
                try:
                    await self._pods.sync_desired(
                        project.id,
                        desired,
                        principal=_SYSTEM,
                        reason="reconcile",
                    )
                    fixed += 1
                except Exception:
                    logger.exception("pod reconcile sync failed project_id=%s", project.id)
                    fixed += 1

        active_q = await self._session.execute(
            select(ProjectRow).where(
                ProjectRow.status.in_((ProjectStatus.ACTIVE, ProjectStatus.ERROR))
            )
        )
        pod_query = PodQuery(self._session)
        for project in active_q.scalars().all():
            live = await pod_query.get_for_project(project.id)
            if live is not None:
                continue
            had_pod = await self._session.execute(
                select(ProjectPodRow.id)
                .where(ProjectPodRow.project_id == project.id)
                .limit(1)
            )
            if had_pod.scalar_one_or_none() is None:
                continue
            await self._pods.provision_for_project(project.id, principal=_SYSTEM, start=False)
            try:
                await self._pods.sync_desired(
                    project.id,
                    PodDesiredState.RUNNING,
                    principal=_SYSTEM,
                    reason="reconcile",
                )
                fixed += 1
            except Exception:
                logger.exception("pod reconcile reprovision failed project_id=%s", project.id)
                fixed += 1

        zombies = await self._reap_zombies()
        fixed += zombies

        from prodavan.application.pod_service.metrics_sampler import PodMetricsSampler

        sample_stats = await PodMetricsSampler(self._session).sample_managed_pods()

        observation = RuntimeObservationService(self._session)
        q_obs = await self._session.execute(
            select(ProjectPodRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ProjectPodRow.project_id)
            .where(
                or_(
                    ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
                    (ProjectPodRow.status == PodStatus.FAILED)
                    & (ProjectPodRow.desired_state == PodDesiredState.RUNNING.value),
                )
            )
        )
        promote_actions = 0
        for pod, project in q_obs.all():
            if project is None:
                continue
            action = await observation.sync_runtime_health(project=project, pod=pod)
            if action != "noop":
                promote_actions += 1

        await self._session.commit()
        from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
        from prodavan.core.jobs.enqueue import enqueue_trigger_drain

        for project_id in resumed_projects:
            try:
                project = await self._session.get(ProjectRow, project_id)
                if project is None or not project.owner_employee_id:
                    continue
                from prodavan.infrastructure.persistence.models.identity import EmployeeRow

                employee = await self._session.get(EmployeeRow, project.owner_employee_id)
                if employee is None:
                    continue
                principal = Principal(
                    sub=employee.keycloak_sub or employee.id,
                    email=employee.email,
                    roles=frozenset({"employee"}),
                )
                await AgentTriggerDispatcher(self._session).dispatch_batch(
                    project_id=project_id,
                    principal=principal,
                    employee=employee,
                    max_n=10,
                )
            except Exception:
                logger.exception("resume trigger dispatch failed project_id=%s", project_id)
        if resumed_projects:
            enqueue_trigger_drain()
        return {"fixed": fixed, "zombies_deleted": zombies, "metrics": sample_stats, "observed": promote_actions}

    async def _clear_stale_preparing_markers(self) -> int:
        """End abandoned launch preparation when no pod was ever provisioned."""
        cutoff = datetime.now(UTC) - timedelta(seconds=settings.pod_preparing_timeout_sec)
        q = await self._session.execute(
            select(ProjectRow).where(
                ProjectRow.launch_phase == "preparing",
                ProjectRow.updated_at < cutoff,
                ~exists().where(ProjectPodRow.project_id == ProjectRow.id),
            )
        )
        stale = q.scalars().all()
        for project in stale:
            project.launch_phase = None
            project.status = ProjectStatus.ERROR
        return len(stale)

    async def _recover_interrupted_resume(
        self, project: ProjectRow, pod: ProjectPodRow
    ) -> None:
        try:
            await self._pods.sync_desired(
                project.id,
                PodDesiredState.RUNNING,
                principal=_SYSTEM,
                reason="resume",
            )
        except Exception as exc:
            logger.exception("resume recovery failed project_id=%s", project.id)
            project.status = ProjectStatus.ERROR
            project.launch_phase = None
            pod.last_error = str(exc)[:2000]
            return

        project.status = ProjectStatus.ACTIVE
        from prodavan.application.projects.runtime_side_effects import bootstrap_project_sessions

        # Keep the persisted marker until registration finishes. If this
        # process dies, reconcile retries the same idempotent bootstrap.
        try:
            bootstrap_result = await bootstrap_project_sessions(
                self._session, project_id=project.id
            )
            if bootstrap_result.get("skipped") == "pod_not_running":
                raise RuntimeError("pod is not running for resume session bootstrap")
        except Exception:
            # Pod resume succeeded; leave ``resuming`` durable so the next
            # reconcile retries session reactivation/bridge registration.
            logger.exception("resume session bootstrap failed project_id=%s", project.id)
            return

        # A previously failed pod can be reused for resume. Once both the pod
        # and its sessions are bootstrapped, an old runtime error is no longer
        # truthful and must not leak through the active project's API view.
        pod.last_error = None

        from prodavan.application.project_service.lifecycle_emitter import (
            ProjectLifecycleEmitter,
        )

        if project.launch_phase == "resuming":
            await ProjectLifecycleEmitter(self._session).emit(
                event_type="project.resumed",
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                principal=_SYSTEM,
                payload={"resume_generation": project.resume_generation},
                idempotency_key=(
                    f"project-resumed:{project.id}:{project.resume_generation}"
                ),
            )
            await self._session.commit()
            from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
            from prodavan.infrastructure.persistence.models.identity import EmployeeRow
            from prodavan.core.jobs.enqueue import enqueue_trigger_drain

            employee = await self._session.get(EmployeeRow, project.owner_employee_id)
            if employee is None:
                return
            principal = Principal(
                sub=employee.keycloak_sub or employee.id,
                email=employee.email,
                roles=frozenset({"employee"}),
            )
            dispatch = await AgentTriggerDispatcher(self._session).dispatch_batch(
                project_id=project.id,
                principal=principal,
                employee=employee,
                max_n=10,
            )
            locked = await self._session.execute(
                select(ProjectRow).where(ProjectRow.id == project.id).with_for_update()
            )
            current = locked.scalar_one()
            dispatch_failed = dispatch.get("reason") in {"project_paused", "dispatch_error"} or any(
                item.get("reason") in {"project_paused", "dispatch_error"}
                for item in dispatch.get("items", [])
            )
            if (
                current.launch_phase == "resuming"
                and current.resume_generation == project.resume_generation
                and not dispatch_failed
            ):
                current.launch_phase = None
            await self._session.commit()
            enqueue_trigger_drain()

    async def _reap_zombies(self) -> int:
        """Delete k8s Pods managed by pod-service without a live PG row."""
        try:
            managed = await self._runtime.list_managed_pods()
        except Exception:
            logger.exception("pod reconcile: list_managed_pods failed")
            return 0
        if not managed:
            return 0

        live_q = await self._session.execute(
            select(ProjectPodRow.runtime_ref, ProjectPodRow.id, ProjectPodRow.status).where(
                ProjectPodRow.status.notin_((PodStatus.TERMINATED,))
            )
        )
        live_rows = live_q.all()
        live_refs = {row.runtime_ref for row in live_rows if row.runtime_ref}
        live_ids = {row.id for row in live_rows}

        deleted = 0
        for item in managed:
            ref = item.get("runtime_ref") or item.get("name")
            pod_id = item.get("pod_id")
            if not ref:
                continue
            if ref in live_refs:
                continue
            if pod_id and pod_id in live_ids:
                continue
            age = _managed_pod_age_sec(item)
            if age is None:
                # Without created_at the provisioning grace cannot be
                # applied — deleting could kill a just-created sandbox.
                logger.warning(
                    "pod reconcile: managed pod without created_at, skip "
                    "(grace unavailable) runtime_ref=%s pod_id=%s",
                    ref,
                    pod_id,
                )
                continue
            if age < _PROVISIONING_GRACE_SEC:
                logger.debug(
                    "pod reconcile: skip young managed pod runtime_ref=%s pod_id=%s age=%.0fs",
                    ref,
                    pod_id,
                    age,
                )
                continue
            logger.info("pod reconcile: delete zombie runtime_ref=%s pod_id=%s", ref, pod_id)
            await self._runtime.terminate(runtime_ref=str(ref))
            deleted += 1
        return deleted
