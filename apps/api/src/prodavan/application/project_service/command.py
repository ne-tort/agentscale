"""Project writes — CRUD, lifecycle, visibility, runtime units."""

from __future__ import annotations

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
from prodavan.domain.pods import PodDesiredState
from prodavan.domain.projects import (
    ProjectStatus,
    ProjectVisibilityMode,
    container_ref_for,
    new_project_id,
    slugify_name,
    workspace_key_for,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter

logger = logging.getLogger(__name__)


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

    async def _project_public(self, row: ProjectRow) -> dict:
        return await self._query._project_public(row)

    async def create(
        self,
        *,
        cabinet_id: str,
        name: str,
        employee: EmployeeRow,
        principal: Principal,
        agent_provider: str | None = None,
        with_runtime_unit: bool = False,
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
            select(ProjectRow).where(ProjectRow.cabinet_id == cabinet_id, ProjectRow.slug == slug)
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
            status=ProjectStatus.ACTIVE,
            visibility_mode=ProjectVisibilityMode.CABINET_SHARED,
            workspace_key=ws_key,
            container_ref=container_ref_for(ws_key),
            agent_provider=normalize_agent_provider(agent_provider),
        )
        self._session.add(row)
        await self._session.flush()

        mat = await self._materialize.materialize_project(
            session=self._session,
            project_id=project_id,
            cabinet_id=cabinet_id,
            cabinet_name=inst.name,
            project_name=row.name,
        )
        if with_runtime_unit:
            await self._pods.provision_for_project(row.id, principal=principal, start=False)
        await self._triggers.enqueue(project_id=project_id, kind="project.prepare", payload={"source": "create"})
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
        await self._session.commit()
        await self._session.refresh(row)
        out = await self._project_public(row)
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
        agent_provider: str | None = None,
        update_agent_provider: bool = False,
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
        if update_agent_provider:
            row.agent_provider = normalize_agent_provider(agent_provider)
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
            row = await self._access.get_project(project_id)
            mat = await self._materialize.materialize_project(
                session=self._session,
                project_id=row.id,
                cabinet_id=row.cabinet_id,
                cabinet_name=None,
                project_name=row.name,
            )
            projects.append(
                {
                    "project_id": row.id,
                    "status": mat.status,
                    "package_names": list(mat.package_names),
                    "mcp_config_path": mat.mcp_config_path,
                }
            )
        return {"cabinet_id": cabinet_id, "count": len(projects), "projects": projects}

    async def rematerialize(
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
        inst = await self._cabinets.get_instance(row.cabinet_id)
        mat = await self._materialize.materialize_project(
            session=self._session,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            cabinet_name=inst.name,
            project_name=row.name,
        )
        return {
            "project_id": row.id,
            "workspace_root": mat.workspace_root,
            "mcp_config_path": mat.mcp_config_path,
            "status": mat.status,
            "package_names": list(mat.package_names),
            "sandbox_packages": list(mat.sandbox_packages),
            "agents_source": mat.agents_source,
        }

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
        company_policy = await AdminCompanyService(self._session).get_agent_policy(row.company_id)
        await AiKeysService(self._session).resolve_credentials(
            company_id=row.company_id,
            preferred_provider=row.agent_provider or company_policy.preferred_provider,
            platform_fallback=company_policy.platform_fallback,
        )
        row.status = ProjectStatus.ACTIVE
        await self._pods.sync_desired(
            row.id,
            PodDesiredState.RUNNING,
            principal=principal,
            reason="resume",
        )
        await self._events.emit(
            event_type="project.resumed",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
        )
        await self._session.commit()
        await self._session.refresh(row)
        try:
            from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
            from prodavan.core.jobs.enqueue import enqueue_trigger_drain

            await AgentTriggerDispatcher(self._session).dispatch_batch(
                project_id=row.id,
                principal=principal,
                employee=employee,
                max_n=10,
            )
            enqueue_trigger_drain()
        except Exception:
            pass
        return await self._project_public(row)

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
