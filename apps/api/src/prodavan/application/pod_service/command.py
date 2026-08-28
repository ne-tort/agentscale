"""PodCommand — runtime orchestration for project pods."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.adapters.k8s_pod_runtime import K8sPodRuntimeAdapter
from prodavan.application.pod_service.adapters.stub_pod_runtime import StubPodRuntimeAdapter
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort
from prodavan.application.pod_service.query import PodQuery
from prodavan.application.relations.commands import RelationsCommand
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.pods import (
    POD_TERMINAL_STATUSES,
    PodDesiredState,
    PodStatus,
    new_pod_id,
    runtime_ref_for,
)
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)


def _runtime_adapter() -> PodRuntimePort:
    if settings.pod_runtime_mode == "k8s":
        return K8sPodRuntimeAdapter()
    return StubPodRuntimeAdapter()


class PodCommand:
    def __init__(
        self,
        session: AsyncSession,
        *,
        runtime: PodRuntimePort | None = None,
        events: PodLifecycleEmitter | None = None,
    ) -> None:
        self._session = session
        self._runtime = runtime or _runtime_adapter()
        self._events = events or PodLifecycleEmitter(session)
        self._relations = RelationsCommand(session)

    async def sync_desired(
        self,
        project_id: str,
        desired: PodDesiredState,
        *,
        principal: Principal,
        reason: str | None = None,
    ) -> None:
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")

        pod = await self._get_live_row(project_id)
        if pod is None and desired == PodDesiredState.ABSENT:
            if project.container_ref:
                await self._runtime.pause(runtime_ref=project.container_ref)
            return

        if pod is None:
            pod = await self._create_row(project, desired=desired)

        if pod.desired_state == desired.value and self._status_matches_desired(pod, desired):
            return

        pod.desired_state = desired.value
        payload = {"reason": reason} if reason else None

        try:
            if desired == PodDesiredState.RUNNING:
                await self._apply_running(project, pod)
                await self._events.emit(
                    event_type="pod.started",
                    company_id=project.company_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    principal=principal,
                    pod_id=pod.id,
                    payload=payload,
                )
            else:
                await self._apply_absent(project, pod)
                await self._events.emit(
                    event_type="pod.paused",
                    company_id=project.company_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    principal=principal,
                    pod_id=pod.id,
                    payload=payload,
                )
        except Exception as exc:
            pod.status = PodStatus.FAILED
            pod.last_error = str(exc)[:2000]
            await self._events.emit(
                event_type="pod.failed",
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                principal=principal,
                pod_id=pod.id,
                payload={"error": pod.last_error, **(payload or {})},
            )
            raise

        project.container_ref = pod.runtime_ref or project.container_ref

    async def provision_for_project(
        self,
        project_id: str,
        *,
        principal: Principal,
        start: bool = False,
    ) -> dict:
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")

        pod = await self._get_live_row(project_id)
        if pod is None:
            desired = PodDesiredState.RUNNING if start else PodDesiredState.ABSENT
            pod = await self._create_row(project, desired=desired)
            await self._events.emit(
                event_type="pod.provisioned",
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                principal=principal,
                pod_id=pod.id,
            )

        if start:
            await self.sync_desired(
                project_id,
                PodDesiredState.RUNNING,
                principal=principal,
                reason="provision.start",
            )

        return PodQuery._public(pod)

    async def attach_to_project(
        self,
        *,
        pod_id: str,
        project_id: str,
        principal: Principal,
    ) -> dict:
        pod = await self._session.get(ProjectPodRow, pod_id)
        if pod is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Pod not found")
        if pod.project_id is not None and pod.project_id != project_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="pod already attached to another project",
            )
        existing = await self._get_live_row(project_id)
        if existing is not None and existing.id != pod_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project already has a live pod",
            )
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")

        pod.project_id = project_id
        pod.workspace_key = project.workspace_key
        await self._relations.bind_pod_to_project(pod_id=pod.id, project_id=project_id, company_id=project.company_id)
        await self._session.flush()
        return PodQuery._public(pod)

    async def detach_from_project(
        self,
        *,
        pod_id: str,
        principal: Principal,
    ) -> dict:
        pod = await self._session.get(ProjectPodRow, pod_id)
        if pod is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Pod not found")
        project_id = pod.project_id
        if project_id is None:
            return PodQuery._public(pod)

        project = await self._session.get(ProjectRow, project_id)
        company_id = project.company_id if project else ""
        await self.sync_desired(
            project_id,
            PodDesiredState.ABSENT,
            principal=principal,
            reason="detach",
        )
        pod.project_id = None
        await self._relations.unbind_pod_from_project(pod_id=pod.id, project_id=project_id, company_id=company_id)
        await self._session.flush()
        return PodQuery._public(pod)

    async def force_kill(self, *, pod_id: str, principal: Principal) -> dict:
        pod = await self._session.get(ProjectPodRow, pod_id)
        if pod is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Pod not found")
        if pod.runtime_ref:
            runtime = self._runtime
            if hasattr(runtime, "force_kill"):
                await runtime.force_kill(runtime_ref=pod.runtime_ref)  # type: ignore[attr-defined]
            else:
                await runtime.terminate(runtime_ref=pod.runtime_ref)
        pod.status = PodStatus.TERMINATED
        pod.desired_state = PodDesiredState.ABSENT
        if pod.project_id:
            project = await self._session.get(ProjectRow, pod.project_id)
            if project:
                await self._events.emit(
                    event_type="pod.terminated",
                    company_id=project.company_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    principal=principal,
                    pod_id=pod.id,
                    payload={"force": True},
                )
        await self._session.flush()
        return PodQuery._public(pod)

    async def _get_live_row(self, project_id: str) -> ProjectPodRow | None:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        return q.scalar_one_or_none()

    async def _create_row(self, project: ProjectRow, *, desired: PodDesiredState) -> ProjectPodRow:
        runtime_ref = runtime_ref_for(project.workspace_key)
        pod = ProjectPodRow(
            id=new_pod_id(),
            project_id=project.id,
            workspace_key=project.workspace_key,
            status=PodStatus.PENDING,
            desired_state=desired.value,
            runtime_ref=runtime_ref,
        )
        self._session.add(pod)
        await self._session.flush()
        project.container_ref = runtime_ref
        await self._relations.bind_pod_to_project(
            pod_id=pod.id,
            project_id=project.id,
            company_id=project.company_id,
        )
        return pod

    async def _apply_running(self, project: ProjectRow, pod: ProjectPodRow) -> None:
        ref = pod.runtime_ref or runtime_ref_for(project.workspace_key)
        pod.runtime_ref = ref
        pod.status = PodStatus.PROVISIONING
        await self._runtime.ensure_running(runtime_ref=ref)
        pod.status = PodStatus.RUNNING
        project.container_ref = ref

    async def _apply_absent(self, project: ProjectRow, pod: ProjectPodRow) -> None:
        ref = pod.runtime_ref or project.container_ref
        pod.status = PodStatus.PAUSING
        if ref:
            await self._runtime.pause(runtime_ref=ref)
        pod.status = PodStatus.PAUSED

    @staticmethod
    def _status_matches_desired(pod: ProjectPodRow, desired: PodDesiredState) -> bool:
        if desired == PodDesiredState.RUNNING:
            return pod.status == PodStatus.RUNNING
        return pod.status in {PodStatus.PAUSED, PodStatus.PENDING, PodStatus.PAUSING}