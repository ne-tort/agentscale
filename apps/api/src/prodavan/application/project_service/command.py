"""Project writes — CRUD, lifecycle, visibility, pod delegation."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.pod_service import PodCommand
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.application.project_service.lifecycle_emitter import ProjectLifecycleEmitter
from prodavan.application.project_service.public import normalize_agent_provider
from prodavan.application.project_service.query import ProjectQuery
from prodavan.application.projects.materialize import get_materialize_service
from prodavan.application.projects.pause_runtime import stop_project_runtime
from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.application.relations.commands import RelationsCommand
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.lifecycle import project_alive_clause
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import (
    ProjectStatus,
    ProjectVisibilityMode,
    container_ref_for,
    new_project_id,
    slugify_name,
    workspace_key_for,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter

logger = logging.getLogger(__name__)

_SYSTEM_JOB = Principal(sub="system:jobs", roles=frozenset({"platform.admin"}))


class ProjectCommand:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessPolicy(session)
        self._query = ProjectQuery(session)
        self._cabinets = CabinetAccessService(session)
        self._materialize = get_materialize_service()
        self._triggers = ProjectTriggerService(session)
        self._companies = AdminCompanyService(session)
        self._events = ProjectLifecycleEmitter(session)
        self._subscription = CompanySubscriptionGate(session)
        self._pods = PodCommand(session)
        self._relations = RelationsCommand(session)

    async def _project_public(self, row: ProjectRow, *, include_runtime: bool = False) -> dict:
        return await self._query._project_public(row, include_runtime=include_runtime)

    async def create(
        self,
        *,
        cabinet_id: str,
        name: str,
        employee: EmployeeRow,
        principal: Principal,
        agent_provider: str | None = None,
    ) -> dict:
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        inst = await self._cabinets.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
        )
        cabinet_company_id = inst.company_id or inst.owner_company_id
        if cabinet_company_id is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="cabinet has no company anchor",
            )
        await self._subscription.require_active(cabinet_company_id)
        await CompanyQuotaService(self._session).assert_can_create_project_in_cabinet(
            cabinet_id=cabinet_id,
            max_projects=inst.max_projects,
        )
        slug = slugify_name(name)
        existing = await self._session.execute(
            select(ProjectRow).where(
                ProjectRow.cabinet_id == cabinet_id,
                ProjectRow.slug == slug,
                project_alive_clause(ProjectRow),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise AppError(
                code="PROJECT_EXISTS",
                title="Project exists",
                status=409,
                detail=f"slug {slug} already used in cabinet",
            )

        project_id = new_project_id()
        ws_key = workspace_key_for(project_id)
        row = ProjectRow(
            id=project_id,
            company_id=cabinet_company_id,
            cabinet_id=cabinet_id,
            owner_employee_id=employee.id,
            name=name.strip(),
            slug=slug,
            status=ProjectStatus.DRAFT,
            visibility_mode=ProjectVisibilityMode.CABINET_SHARED,
            workspace_key=ws_key,
            container_ref=container_ref_for(ws_key),
            agent_provider=normalize_agent_provider(agent_provider),
        )
        self._session.add(row)
        await self._session.flush()

        await self._events.emit(
            event_type="project.created",
            company_id=row.company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            principal=principal,
            payload={
                "name": row.name,
                "slug": row.slug,
                "created_by_employee_id": employee.id,
                "visibility_mode": row.visibility_mode,
            },
        )
        from prodavan.application.project_service.module_settings import ProjectModuleSettingsService

        await ProjectModuleSettingsService(self._session).ensure_default_profiles_for_project(
            project=row,
            principal=principal,
            employee=employee,
        )
        await self._triggers.enqueue(project_id=project_id, kind="project.prepare", payload={})
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def _get_live_pod(self, project_id: str) -> ProjectPodRow | None:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
            )
        )
        return q.scalar_one_or_none()

    async def _resolve_enabled_module_ids(self, row: ProjectRow) -> list[str]:
        from prodavan.application.modules.module_binding_service import ModuleBindingService
        from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow

        q = await self._session.execute(
            select(ProjectModuleBindingRow.module_id).where(
                ProjectModuleBindingRow.project_id == row.id
            )
        )
        bound = list(q.scalars().all())
        if bound:
            return bound
        return await ModuleBindingService(self._session).list_module_ids_for_cabinet(row.cabinet_id)

    async def launch(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        from prodavan.application.ai_keys.service import AiKeysService

        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        live_pod = await self._get_live_pod(row.id)
        if live_pod is not None:
            raise AppError(
                code="POD_ALREADY_EXISTS",
                title="Conflict",
                status=409,
                detail="project pod already exists",
            )
        if row.status not in {ProjectStatus.DRAFT, ProjectStatus.ACTIVE}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project cannot be launched in current status",
            )
        if not row.agent_provider:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="agent_provider required before launch",
            )
        if not row.resolved_ai_key_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="resolved_ai_key_id required before launch",
            )
        inst = await self._cabinets.get_instance(row.cabinet_id)
        company_policy = await AdminCompanyService(self._session).get_agent_policy(row.company_id)
        await AiKeysService(self._session).resolve_credentials_for_project(
            project=row,
            preferred_provider=row.agent_provider or company_policy.preferred_provider,
            platform_fallback=company_policy.platform_fallback,
        )
        enabled = await self._resolve_enabled_module_ids(row)
        row.launch_phase = "preparing"
        await self._session.flush()
        mat = await self._materialize.materialize_project(
            session=self._session,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            cabinet_name=inst.name,
            project_name=row.name,
            when="project.created",
            enabled_module_ids=enabled,
        )
        row.materialize_manifest = dict(mat.module_paths or {})
        row.launch_phase = None
        await self._session.flush()
        await self._pods.provision_for_project(row.id, principal=principal, start=False)
        try:
            await self._pods.sync_desired(
                row.id,
                PodDesiredState.RUNNING,
                principal=principal,
                reason="launch",
            )
            row.status = ProjectStatus.ACTIVE
            await self._events.emit(
                event_type="project.started",
                company_id=row.company_id,
                project_id=row.id,
                cabinet_id=row.cabinet_id,
                principal=principal,
                payload={"source": "launch"},
            )
        except Exception:
            row.status = ProjectStatus.ERROR
            row.launch_phase = None
        await self._session.commit()
        await self._session.refresh(row)
        out = await self._project_public(row, include_runtime=True)
        out["materialize"] = {
            "workspace_root": mat.workspace_root,
            "mcp_config_path": mat.mcp_config_path,
            "status": mat.status,
            "package_names": list(mat.package_names),
            "sandbox_packages": list(mat.sandbox_packages),
            "agents_source": mat.agents_source,
        }
        return out

    async def _stop_and_pause_runtime(
        self, row: ProjectRow, *, principal: Principal, reason: str | None = None
    ) -> None:
        await stop_project_runtime(self._session, project_id=row.id)
        await self._pods.sync_desired(
            row.id,
            PodDesiredState.ABSENT,
            principal=principal,
            reason=reason,
        )

    async def patch(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        name: str | None = None,
        about: str | None = None,
        update_about: bool = False,
        budget_tokens: int | None = None,
        update_budget_tokens: bool = False,
        agent_provider: str | None = None,
        update_agent_provider: bool = False,
        resolved_ai_key_id: str | None = None,
        update_resolved_ai_key_id: bool = False,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        if name is not None:
            trimmed = name.strip()
            if not trimmed:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="name required",
                )
            row.name = trimmed
        if update_about:
            row.about = (about or "").strip() or None
        if update_budget_tokens:
            row.budget_tokens = budget_tokens
        if update_agent_provider:
            row.agent_provider = normalize_agent_provider(agent_provider)
        if update_resolved_ai_key_id:
            if resolved_ai_key_id:
                from prodavan.application.ai_keys.service import AiKeysService

                keys = AiKeysService(self._session)
                row.agent_provider = await keys.agent_provider_for_project_key(
                    project=row,
                    key_id=resolved_ai_key_id,
                )
            else:
                row.agent_provider = None
            row.resolved_ai_key_id = resolved_ai_key_id
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def set_visibility(
        self,
        *,
        project_id: str,
        visibility_mode: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        if visibility_mode not in {m.value for m in ProjectVisibilityMode}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"visibility_mode must be one of: {', '.join(m.value for m in ProjectVisibilityMode)}",
            )
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        previous = row.visibility_mode or ProjectVisibilityMode.CABINET_SHARED
        if previous == visibility_mode:
            return await self._project_public(row)
        row.visibility_mode = visibility_mode
        await self._events.emit(
            event_type="project.visibility.changed",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
            payload={"from": previous, "to": visibility_mode},
        )
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def assign_employee(
        self,
        *,
        project_id: str,
        employee_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        await self._relations.assign_employee_to_project(
            project_id=project_id,
            cabinet_id=row.cabinet_id,
            company_id=row.company_id,
            employee_id=employee_id,
        )
        await self._session.commit()
        return {"project_id": project_id, "employee_id": employee_id, "assigned": True}

    async def revoke_employee(
        self,
        *,
        project_id: str,
        employee_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        await self._relations.revoke_employee_from_project(
            project_id=project_id,
            employee_id=employee_id,
            company_id=row.company_id,
            cabinet_id=row.cabinet_id,
        )
        await self._session.commit()
        return {"project_id": project_id, "employee_id": employee_id, "revoked": True}

    async def rematerialize_for_cabinet(self, *, cabinet_id: str) -> dict:
        projects: list[dict] = []
        for project_id in await self._query.list_ids(
            cabinet_id=cabinet_id, exclude_status=ProjectStatus.DELETED
        ):
            projects.append(await self.rematerialize_background(project_id=project_id))
        return {"cabinet_id": cabinet_id, "count": len(projects), "projects": projects}

    async def rematerialize_background(self, *, project_id: str) -> dict:
        """System/Celery rematerialize — sync workspace and bump hydrate for live pods."""
        row = await self._access.get_project_or_none(project_id)
        if row is None:
            return {"ok": False, "reason": "not_found", "project_id": project_id}
        if row.status == ProjectStatus.DELETED:
            return {"ok": False, "reason": "deleted", "project_id": project_id}
        live_pod = await self._get_live_pod(row.id)
        mat = await self._sync_project_workspace(row)
        if live_pod is not None:
            await self._apply_post_rematerialize(
                row, live_pod, principal=_SYSTEM_JOB, reason="rematerialize"
            )
        await self._session.commit()
        return self._rematerialize_result(row, mat, live_pod, ok=True)

    async def stop_runtime_system(self, *, project_id: str, reason: str = "purge") -> None:
        """Terminate/pause pod runtime without ACL (cabinet purge, cascade jobs)."""
        row = await self._access.get_project_or_none(project_id)
        if row is None:
            return
        await self._stop_and_pause_runtime(row, principal=_SYSTEM_JOB, reason=reason)

    async def _sync_project_workspace(self, row: ProjectRow):
        inst = await self._cabinets.get_instance(row.cabinet_id)
        from prodavan.application.modules.module_binding_service import ModuleBindingService

        all_modules = await ModuleBindingService(self._session).list_module_ids_for_cabinet(row.cabinet_id)
        enabled = await self._resolve_enabled_module_ids(row)
        return await self._materialize.sync_project(
            session=self._session,
            project=row,
            cabinet_name=inst.name,
            when="project.sync",
            enabled_module_ids=enabled,
            all_cabinet_module_ids=all_modules,
        )

    async def _apply_post_rematerialize(
        self,
        row: ProjectRow,
        live_pod: ProjectPodRow,
        *,
        principal: Principal,
        reason: str,
    ) -> None:
        live_pod.hydrate_generation += 1
        if row.status == ProjectStatus.ACTIVE:
            await self._pods.sync_desired(
                row.id,
                PodDesiredState.RUNNING,
                principal=principal,
                reason=reason,
            )

    @staticmethod
    def _rematerialize_result(
        row: ProjectRow,
        mat,
        live_pod: ProjectPodRow | None,
        *,
        ok: bool | None = None,
    ) -> dict:
        out: dict = {
            "project_id": row.id,
            "workspace_root": mat.workspace_root,
            "mcp_config_path": mat.mcp_config_path,
            "status": mat.status,
            "package_names": list(mat.package_names),
            "sandbox_packages": list(getattr(mat, "sandbox_packages", ()) or ()),
            "agents_source": getattr(mat, "agents_source", "default"),
        }
        if live_pod is not None:
            out["hydrate_generation"] = live_pod.hydrate_generation
        if ok is not None:
            out["ok"] = ok
        return out

    async def rematerialize(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        return await self.sync_project(
            project_id=project_id, principal=principal, employee=employee
        )

    async def sync_project(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        live_pod = await self._get_live_pod(row.id)
        if live_pod is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project pod does not exist; launch project first",
            )
        mat = await self._sync_project_workspace(row)

        await self._apply_post_rematerialize(row, live_pod, principal=principal, reason="sync")

        await self._session.commit()

        return self._rematerialize_result(row, mat, live_pod)

    async def pause(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        skip_access: bool = False,
        payload: dict | None = None,
    ) -> dict:
        if skip_access:
            row = await self._access.get_project(project_id)
        else:
            row = await self._access.require_access(
                project_id=project_id,
                principal=principal,
                employee=employee,
                write=True,
                allow_paused=True,
            )
        if row.status == ProjectStatus.PAUSED:
            return await self._project_public(row)
        if await self._get_live_pod(row.id) is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project has no pod to pause",
            )
        await self._stop_and_pause_runtime(row, principal=principal, reason="pause")
        row.status = ProjectStatus.PAUSED
        emit_payload = dict(payload or {})
        await self._events.emit(
            event_type="project.paused",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
            payload=emit_payload or None,
        )
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def resume(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        from prodavan.application.ai_keys.service import AiKeysService

        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        if row.status not in {ProjectStatus.PAUSED, ProjectStatus.COMPLETED}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project is not paused or completed",
            )
        if await self._get_live_pod(row.id) is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project has no pod to resume",
            )
        company_policy = await AdminCompanyService(self._session).get_agent_policy(row.company_id)
        await AiKeysService(self._session).resolve_credentials_for_project(
            project=row,
            preferred_provider=row.agent_provider or company_policy.preferred_provider,
            platform_fallback=company_policy.platform_fallback,
        )
        row.launch_phase = "resuming"
        await self._session.commit()
        await self._session.refresh(row)
        asyncio.create_task(
            self._resume_background(
                project_id=row.id,
                principal=principal,
                employee=employee,
            ),
            name=f"resume-{row.id}",
        )
        return await self._project_public(row, include_runtime=True)

    async def _resume_background(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        from prodavan.infrastructure.persistence.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            cmd = ProjectCommand(session)
            row = await cmd._access.get_project_or_none(project_id)
            if row is None:
                return
            try:
                await cmd._pods.sync_desired(
                    row.id,
                    PodDesiredState.RUNNING,
                    principal=principal,
                    reason="resume",
                )
                row.status = ProjectStatus.ACTIVE
                row.launch_phase = None
                await cmd._events.emit(
                    event_type="project.resumed",
                    company_id=row.company_id,
                    project_id=row.id,
                    cabinet_id=row.cabinet_id,
                    principal=principal,
                )
            except Exception as exc:
                logger.warning("resume background failed project_id=%s: %s", project_id, exc)
                row.status = ProjectStatus.ERROR
                row.launch_phase = None
            await session.commit()
            if row.status == ProjectStatus.ACTIVE:
                try:
                    from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
                    from prodavan.core.jobs.enqueue import enqueue_trigger_drain

                    await AgentTriggerDispatcher(session).dispatch_batch(
                        project_id=row.id,
                        principal=principal,
                        employee=employee,
                        max_n=10,
                    )
                    enqueue_trigger_drain()
                except Exception:
                    pass

    async def reload_project(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        from prodavan.core.infra.cache import cache_key, rate_limit_enforce

        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        from prodavan.application.pod_service.query import PodQuery

        await PodQuery(self._session).runtime_view(project_id)
        await self._session.refresh(row)
        await rate_limit_enforce(
            cache_key("pod-reload", project_id, "1m"),
            limit=1,
            window_sec=60,
            detail="pod reload rate limit exceeded (1 per minute)",
        )
        await rate_limit_enforce(
            cache_key("pod-reload", project_id, "30m"),
            limit=3,
            window_sec=1800,
            detail="pod reload rate limit exceeded (3 per 30 minutes)",
        )
        try:
            await self._pods.sync_desired(
                row.id,
                PodDesiredState.RUNNING,
                principal=principal,
                reason="reload",
            )
            row.status = ProjectStatus.ACTIVE
        except Exception:
            row.status = ProjectStatus.ERROR
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row, include_runtime=True)

    async def complete(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        if row.status == ProjectStatus.COMPLETED:
            return await self._project_public(row)
        await self._stop_and_pause_runtime(row, principal=principal, reason="complete")
        row.status = ProjectStatus.COMPLETED
        await self._events.emit(
            event_type="project.completed",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
        )
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def delete(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        purge_workspace: bool = False,
        skip_access: bool = False,
    ) -> dict:
        if skip_access:
            row = await self._access.get_project(project_id)
        else:
            row = await self._access.require_access(
                project_id=project_id,
                principal=principal,
                employee=employee,
                write=True,
                allow_paused=True,
            )
        return await self._soft_delete_row(row, principal=principal, purge_workspace=purge_workspace)

    async def _soft_delete_row(
        self,
        row: ProjectRow,
        *,
        principal: Principal,
        purge_workspace: bool,
    ) -> dict:
        await self._stop_and_pause_runtime(row, principal=principal, reason="delete")
        row.status = ProjectStatus.DELETED
        await self._events.emit(
            event_type="project.deleted",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
            payload={"purge_workspace": purge_workspace, "soft": True},
        )
        await self._session.commit()
        wipe: dict = {"ok": True, "skipped": True}
        if purge_workspace:
            wipe = await self._wipe_workspace(row)
        await self._session.refresh(row)
        out = await self._project_public(row)
        out["soft"] = True
        out["workspace_wipe"] = wipe
        return out

    async def restore(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
            allow_deleted=True,
        )
        if row.status != ProjectStatus.DELETED:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project is not soft-deleted",
            )
        row.status = ProjectStatus.PAUSED
        await self._events.emit(
            event_type="project.restored",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
        )
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def purge(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
            allow_deleted=True,
        )
        if row.status != ProjectStatus.DELETED:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="soft-delete the project before purge",
            )
        await self._stop_and_pause_runtime(row, principal=principal, reason="purge")
        wipe = await self._wipe_workspace(row)
        await self._events.emit(
            event_type="project.purged",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
            payload={"workspace_wipe": wipe},
        )
        await self._session.commit()
        out = await self._project_public(row)
        out["purged"] = True
        out["workspace_wipe"] = wipe
        return out

    async def list_module_ids(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[str]:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow

        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=False,
            allow_paused=True,
        )
        q = await self._session.execute(
            select(ProjectModuleBindingRow.module_id).where(
                ProjectModuleBindingRow.project_id == row.id
            )
        )
        bound = list(q.scalars().all())
        if bound:
            return bound
        from prodavan.application.modules.module_binding_service import ModuleBindingService

        return await ModuleBindingService(self._session).list_module_ids_for_cabinet(row.cabinet_id)

    async def set_module_ids(
        self,
        *,
        project_id: str,
        module_ids: list[str],
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        from sqlalchemy import delete

        from prodavan.application.modules.module_binding_service import ModuleBindingService
        from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow

        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        allowed = set(
            await ModuleBindingService(self._session).list_module_ids_for_cabinet(row.cabinet_id)
        )
        unique = list(dict.fromkeys(module_ids))
        for mid in unique:
            if mid not in allowed:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"module not bound to cabinet: {mid}",
                )
        await self._session.execute(
            delete(ProjectModuleBindingRow).where(ProjectModuleBindingRow.project_id == row.id)
        )
        for mid in unique:
            self._session.add(ProjectModuleBindingRow(project_id=row.id, module_id=mid))
        await self._session.commit()
        sync = await self.rematerialize_background(project_id=row.id)
        return {"module_ids": unique, "sync": sync}

    async def _wipe_workspace(self, row: ProjectRow) -> dict:
        try:
            from prodavan.core.jobs.enqueue import enqueue_wipe_project_tree

            wipe = WorkspaceLayoutWriter(workspace_key=row.workspace_key).remove_project_tree()
            if not wipe.get("ok"):
                retry = enqueue_wipe_project_tree(row.workspace_key)
                wipe["retry_enqueued"] = bool(retry.get("enqueued"))
                wipe["retry"] = retry
            return wipe
        except Exception:
            logger.exception("project wipe failed project_id=%s", row.id)
            return {"ok": False, "deleted": 0, "remaining": 0, "error": "wipe_failed"}
