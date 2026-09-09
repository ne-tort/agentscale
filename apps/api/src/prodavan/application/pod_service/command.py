"""PodCommand — runtime orchestration for project pods."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.container_env_loader import ContainerEnvLoader
from prodavan.application.pod_service.factory import build_hydrate, build_pod_runtime
from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter
from prodavan.application.pod_service.ports.hydrate import HydratePort
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort
from prodavan.application.pod_service.query import PodQuery
from prodavan.application.project_service.lifecycle_emitter import ProjectLifecycleEmitter
from prodavan.application.relations.commands import RelationsCommand
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.pods import (
    POD_TERMINAL_STATUSES,
    PodDesiredState,
    PodRuntimeContext,
    PodStatus,
    new_pod_id,
    runtime_ref_for,
)
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_TERMINATE_REASONS = frozenset({"delete", "purge", "terminate", "detach", "force_kill"})
_FORCE_REHYDRATE_REASONS = frozenset({"rematerialize", "sync", "reload"})

_LIFECYCLE_BY_REASON: dict[str, str] = {
    "launch": "project.launch",
    "lazy.start": "project.launch",
    "resume": "project.resumed",
    "reload": "project.reload",
    "sync": "project.sync",
    "rematerialize": "project.sync",
}


class PodCommand:
    def __init__(
        self,
        session: AsyncSession,
        *,
        runtime: PodRuntimePort | None = None,
        events: PodLifecycleEmitter | None = None,
        hydrate: HydratePort | None = None,
    ) -> None:
        self._session = session
        self._runtime = runtime or build_pod_runtime()
        self._events = events or PodLifecycleEmitter(session)
        self._hydrate = hydrate or build_hydrate()
        self._project_events = ProjectLifecycleEmitter(session)
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
                if reason in _TERMINATE_REASONS:
                    await self._runtime.terminate(runtime_ref=project.container_ref)
                else:
                    await self._runtime.pause(runtime_ref=project.container_ref)
            return

        if pod is None:
            pod = await self._ensure_live_row(project, desired=desired)

        if reason == "reload" and desired == PodDesiredState.RUNNING:
            await self._prepare_reload(project, pod)

        force_rehydrate = (
            reason in _FORCE_REHYDRATE_REASONS and desired == PodDesiredState.RUNNING and pod is not None
        )

        if pod.desired_state == desired.value:
            if self._status_matches_desired(pod, desired) and not force_rehydrate:
                return
            if desired == PodDesiredState.RUNNING and pod.status == PodStatus.PROVISIONING:
                if not force_rehydrate:
                    return

        pod.desired_state = desired.value
        payload = {"reason": reason} if reason else None
        terminate = desired == PodDesiredState.ABSENT and reason in _TERMINATE_REASONS

        try:
            if desired == PodDesiredState.RUNNING:
                await self._apply_running(
                    project,
                    pod,
                    principal=principal,
                    lifecycle=_LIFECYCLE_BY_REASON.get(reason or "", "project.launch"),
                )
                if project.status == ProjectStatus.ERROR:
                    project.status = ProjectStatus.ACTIVE
                    await self._project_events.emit(
                        event_type="project.recovered",
                        company_id=project.company_id,
                        project_id=project.id,
                        cabinet_id=project.cabinet_id,
                        principal=principal,
                        payload={"source": reason or "reload"},
                    )
                event_type = "pod.resumed" if reason in {"resume", "reload"} else "pod.started"
                await self._events.emit(
                    event_type=event_type,
                    company_id=project.company_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    principal=principal,
                    pod_id=pod.id,
                    payload=payload,
                )
                if reason == "lazy.start":
                    await self._project_events.emit(
                        event_type="project.started",
                        company_id=project.company_id,
                        project_id=project.id,
                        cabinet_id=project.cabinet_id,
                        principal=principal,
                        payload={"source": "lazy.start"},
                    )
            elif terminate:
                await self._apply_terminate(project, pod)
                await self._events.emit(
                    event_type="pod.terminated",
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
            if reason == "reconcile":
                await self._events.emit(
                    event_type="pod.reconciled",
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
            if desired == PodDesiredState.RUNNING and project.status != ProjectStatus.ERROR:
                project.status = ProjectStatus.ERROR
                await self._project_events.emit(
                    event_type="project.failed",
                    company_id=project.company_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    principal=principal,
                    payload={"error": pod.last_error, "reason": reason},
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
            had_failed = await self._get_failed_row(project_id) is not None
            pod = await self._ensure_live_row(project, desired=desired)
            if not had_failed:
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

    async def ensure_running_for_project(
        self,
        project_id: str,
        *,
        principal: Principal,
    ) -> None:
        """Lazy-start pod for an active project (first agent/trigger)."""
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        if project.status != ProjectStatus.ACTIVE:
            return
        pod = await self._get_live_row(project_id)
        if pod is None:
            await self.provision_for_project(project_id, principal=principal, start=False)
        try:
            await self.sync_desired(
                project_id,
                PodDesiredState.RUNNING,
                principal=principal,
                reason="lazy.start",
            )
        except Exception:
            logger.exception("lazy start failed project_id=%s", project_id)

    async def force_kill(self, *, pod_id: str, principal: Principal) -> dict:
        from prodavan.application.pod_identity.bridge import bump_pod_bridge_generation

        pod = await self._session.get(ProjectPodRow, pod_id)
        if pod is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Pod not found")
        await bump_pod_bridge_generation(pod.id)
        if pod.runtime_ref:
            await self._runtime.force_kill(runtime_ref=pod.runtime_ref)
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
        await self._session.commit()
        await self._session.refresh(pod)
        return PodQuery._public(pod)

    async def _get_live_row(self, project_id: str) -> ProjectPodRow | None:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        return q.scalar_one_or_none()

    async def _get_failed_row(self, project_id: str) -> ProjectPodRow | None:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status == PodStatus.FAILED,
            )
        )
        return q.scalar_one_or_none()

    async def _ensure_live_row(self, project: ProjectRow, *, desired: PodDesiredState) -> ProjectPodRow:
        failed = await self._get_failed_row(project.id)
        if failed is not None:
            failed.status = PodStatus.PENDING
            failed.last_error = None
            failed.desired_state = desired.value
            await self._session.flush()
            return failed
        return await self._create_row(project, desired=desired)

    async def _create_row(self, project: ProjectRow, *, desired: PodDesiredState) -> ProjectPodRow:
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        runtime_ref = runtime_ref_for(project.workspace_key, mode=mode)
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

    async def _prepare_reload(self, project: ProjectRow, pod: ProjectPodRow) -> None:
        """Terminate live k8s workload and reset pod row so reload always recreates."""
        from prodavan.application.pod_identity.bridge import bump_pod_bridge_generation
        from prodavan.application.projects.workspace_checkpoint import checkpoint_project_workspace

        await bump_pod_bridge_generation(pod.id)
        await checkpoint_project_workspace(self._session, project_id=project.id, best_effort=True)
        ref = pod.runtime_ref or project.container_ref
        if ref:
            try:
                await self._runtime.terminate(runtime_ref=ref)
            except Exception:
                logger.exception("reload terminate failed runtime_ref=%s", ref)
        pod.status = PodStatus.PENDING
        pod.last_error = None
        pod.desired_state = PodDesiredState.RUNNING.value
        pod.last_started_at = None
        await self._session.flush()
        from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store

        await build_metrics_store().clear_project_latest(project.id)

    async def _apply_running(
        self,
        project: ProjectRow,
        pod: ProjectPodRow,
        *,
        principal: Principal,
        lifecycle: str = "project.launch",
    ) -> None:
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        ref = pod.runtime_ref or runtime_ref_for(project.workspace_key, mode=mode)
        pod.runtime_ref = ref
        pod.status = PodStatus.PROVISIONING
        extra_env = await ContainerEnvLoader(self._session).load_for_project(
            project, lifecycle=lifecycle
        )
        pod_auth_token = await self._mint_pod_bridge_token(project, pod, principal=principal)
        ctx = self._runtime_context(
            project, pod, extra_env=extra_env, pod_auth_token=pod_auth_token
        )
        # Commit before k8s create/wait so pod_reconcile zombie reaper sees the PG row.
        await self._session.commit()
        await self._runtime.ensure_running(runtime_ref=ref, context=ctx)
        ws_key = pod.workspace_key or project.workspace_key
        await self._hydrate.hydrate(workspace_key=ws_key, runtime_ref=ref)
        pod.status = PodStatus.PROVISIONING
        project.container_ref = ref
        await self._events.emit(
            event_type="pod.hydrated",
            company_id=project.company_id,
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            principal=principal,
            pod_id=pod.id,
            payload={"generation": pod.hydrate_generation, "awaiting_verification": True},
        )

    async def _mint_pod_bridge_token(
        self,
        project: ProjectRow,
        pod: ProjectPodRow,
        *,
        principal: Principal,
    ) -> str | None:
        from prodavan.application.modules.module_binding_service import ModuleBindingService
        from prodavan.application.pod_identity.bridge import (
            build_launch_scopes,
            mint_pod_bridge_token,
        )

        module_ids = await ModuleBindingService(self._session).list_module_ids_for_project(
            project.id
        )
        scopes = build_launch_scopes(module_ids)
        token, _claims = await mint_pod_bridge_token(
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            company_id=project.company_id,
            pod_id=pod.id,
            scopes=scopes,
            acting_employee_id=principal.sub or None,
        )
        return token

    async def _apply_absent(self, project: ProjectRow, pod: ProjectPodRow) -> None:
        from prodavan.application.pod_identity.bridge import bump_pod_bridge_generation

        await bump_pod_bridge_generation(pod.id)
        ref = pod.runtime_ref or project.container_ref
        pod.status = PodStatus.PAUSING
        if ref:
            await self._runtime.pause(runtime_ref=ref)
        pod.status = PodStatus.PAUSED

    async def _apply_terminate(self, project: ProjectRow, pod: ProjectPodRow) -> None:
        from prodavan.application.pod_identity.bridge import bump_pod_bridge_generation

        await bump_pod_bridge_generation(pod.id)
        ref = pod.runtime_ref or project.container_ref
        pod.status = PodStatus.TERMINATING
        if ref:
            await self._runtime.terminate(runtime_ref=ref)
        pod.status = PodStatus.TERMINATED
        pod.desired_state = PodDesiredState.ABSENT

    @staticmethod
    def _runtime_context(
        project: ProjectRow,
        pod: ProjectPodRow,
        *,
        extra_env: tuple[tuple[str, str], ...] = (),
        pod_auth_token: str | None = None,
    ) -> PodRuntimeContext:
        return PodRuntimeContext(
            pod_id=pod.id,
            project_id=project.id,
            company_id=project.company_id,
            workspace_key=pod.workspace_key or project.workspace_key,
            hydrate_generation=pod.hydrate_generation,
            extra_env=extra_env,
            pod_auth_token=pod_auth_token,
        )

    @staticmethod
    def _status_matches_desired(pod: ProjectPodRow, desired: PodDesiredState) -> bool:
        if desired == PodDesiredState.RUNNING:
            return pod.status == PodStatus.RUNNING
        if pod.status == PodStatus.TERMINATED:
            return desired == PodDesiredState.ABSENT
        return pod.status in {PodStatus.PAUSED, PodStatus.PENDING, PodStatus.PAUSING}