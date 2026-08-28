"""Pod reconcile — drift correction worker."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.adapters.stub_pod_runtime import StubPodRuntimeAdapter
from prodavan.application.pod_service.command import PodCommand
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort
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
        self._runtime = runtime or StubPodRuntimeAdapter()
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
            if pod.desired_state != desired.value:
                await self._pods.sync_desired(
                    project.id,
                    desired,
                    principal=_SYSTEM,
                    reason="reconcile",
                )
                fixed += 1
        await self._session.commit()
        return {"fixed": fixed}
