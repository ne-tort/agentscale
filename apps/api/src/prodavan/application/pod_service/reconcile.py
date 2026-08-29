"""Pod reconcile — drift correction worker."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.factory import build_pod_runtime
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort
from prodavan.application.pod_service.query import PodQuery
from prodavan.domain.identity import Principal
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_SYSTEM = Principal(sub="system:pod-reconcile", roles=frozenset({"platform.admin"}))


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
        fixed = 0
        q = await self._session.execute(
            select(ProjectPodRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ProjectPodRow.project_id, isouter=True)
            .where(ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)))
        )
        for pod, project in q.all():
            if project is None:
                if pod.runtime_ref:
                    await self._runtime.terminate(runtime_ref=pod.runtime_ref)
                    pod.status = PodStatus.TERMINATED
                    fixed += 1
                continue
            desired = (
                PodDesiredState.RUNNING
                if project.status == ProjectStatus.ACTIVE
                else PodDesiredState.ABSENT
            )
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
            select(ProjectRow).where(ProjectRow.status == ProjectStatus.ACTIVE)
        )
        pod_query = PodQuery(self._session)
        for project in active_q.scalars().all():
            if project.status == ProjectStatus.ERROR:
                continue
            if await pod_query.get_for_project(project.id) is not None:
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

        await self._session.commit()
        return {"fixed": fixed, "zombies_deleted": zombies, "metrics": sample_stats}

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
            logger.info("pod reconcile: delete zombie runtime_ref=%s pod_id=%s", ref, pod_id)
            await self._runtime.terminate(runtime_ref=str(ref))
            deleted += 1
        return deleted
