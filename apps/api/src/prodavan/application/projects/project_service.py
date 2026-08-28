"""Project CRUD + lifecycle (L07)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.projects.access import ProjectAccessService
from prodavan.application.projects.container_lifecycle import pause_container
from prodavan.application.projects.materialize import get_materialize_service
from prodavan.application.projects.pause_runtime import stop_project_runtime
from prodavan.application.projects.platform_event_service import PlatformEventService
from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.domain.admin import attachment_max_bytes
from prodavan.domain.ai_keys import AiProvider
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import (
    ProjectStatus,
    container_ref_for,
    new_project_id,
    slugify_name,
    workspace_key_for,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter

logger = logging.getLogger(__name__)

_ALLOWED_PROVIDERS = frozenset(p.value for p in AiProvider)


def _normalize_agent_provider(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip()
    if normalized not in _ALLOWED_PROVIDERS:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"agent_provider must be one of: {', '.join(sorted(_ALLOWED_PROVIDERS))}",
        )
    return normalized


def _public(
    row: ProjectRow,
    *,
    limits: dict | None = None,
    company_subscription: dict | None = None,
) -> dict:
    out = {
        "id": row.id,
        "company_id": row.company_id,
        "cabinet_id": row.cabinet_id,
        "owner_employee_id": row.owner_employee_id,
        "created_by_employee_id": row.owner_employee_id,
        "name": row.name,
        "slug": row.slug,
        "status": row.status,
        "workspace_key": row.workspace_key,
        "container_ref": row.container_ref,
        "agent_provider": row.agent_provider,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if limits is not None:
        out["limits"] = limits
    if company_subscription is not None:
        out["company_subscription"] = company_subscription
    return out


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessService(session)
        self._cabinets = CabinetAccessService(session)
        self._materialize = get_materialize_service()
        self._triggers = ProjectTriggerService(session)
        self._companies = AdminCompanyService(session)
        self._platform_events = PlatformEventService(session)
        self._subscription = CompanySubscriptionGate(session)

    async def _attachment_limits(self, company_id: str) -> dict:
        policy = await self._companies.get_agent_policy(company_id)
        return {"attachment_max_bytes": attachment_max_bytes(policy)}

    async def _project_public(self, row: ProjectRow) -> dict:
        limits = await self._attachment_limits(row.company_id)
        subscription = await self._subscription.subscription_state(row.company_id)
        return _public(row, limits=limits, company_subscription=subscription)

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
            workspace_key=ws_key,
            container_ref=container_ref_for(ws_key),
            agent_provider=_normalize_agent_provider(agent_provider),
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
        await self._triggers.enqueue(project_id=project_id, kind="project.prepare", payload={"source": "create"})
        await self._platform_events.emit(
            event_type="project.created",
            company_id=row.company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            principal=principal,
            payload={
                "name": row.name,
                "slug": row.slug,
                "created_by_employee_id": employee.id,
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

    async def list_for_cabinet(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._cabinets.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        limits = await self._attachment_limits(inst.company_id)
        subscription = await self._subscription.subscription_state(inst.company_id)
        q = await self._session.execute(
            select(ProjectRow)
            .where(ProjectRow.cabinet_id == cabinet_id, ProjectRow.status != ProjectStatus.DELETED)
            .order_by(ProjectRow.created_at.desc())
        )
        return [
            _public(r, limits=limits, company_subscription=subscription) for r in q.scalars().all()
        ]

    async def get(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        return await self._project_public(row)

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
        """Update mutable project fields. ``agent_provider`` overrides company preferred_provider."""
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
            row.agent_provider = _normalize_agent_provider(agent_provider)
        await self._session.commit()
        await self._session.refresh(row)
        return await self._project_public(row)

    async def rematerialize_for_cabinet(self, *, cabinet_id: str) -> dict:
        """Rematerialize all non-deleted projects after cabinet MCP package change."""
        q = await self._session.execute(
            select(ProjectRow).where(
                ProjectRow.cabinet_id == cabinet_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        projects: list[dict] = []
        for row in q.scalars().all():
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
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        if row.status == ProjectStatus.PAUSED:
            return await self._project_public(row)
        row.status = ProjectStatus.PAUSED
        # Stop in-flight runtime: cancel ACTIVE agent sessions (leave triggers queued).
        await stop_project_runtime(self._session, project_id=row.id)
        # Contract: project → container pause (keep volume; pod stop when available).
        await pause_container(container_ref=row.container_ref)
        await self._platform_events.emit(
            event_type="project.paused",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
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
        if row.status != ProjectStatus.PAUSED:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project is not paused",
            )
        company_policy = await AdminCompanyService(self._session).get_agent_policy(row.company_id)
        # Manual resume only — require a valid runtime key; never auto-resume from key enable.
        await AiKeysService(self._session).resolve_credentials(
            company_id=row.company_id,
            preferred_provider=row.agent_provider or company_policy.preferred_provider,
            platform_fallback=company_policy.platform_fallback,
        )
        row.status = ProjectStatus.ACTIVE
        await self._platform_events.emit(
            event_type="project.resumed",
            company_id=row.company_id,
            project_id=row.id,
            cabinet_id=row.cabinet_id,
            principal=principal,
        )
        await self._session.commit()
        await self._session.refresh(row)
        from prodavan.application.projects.container_lifecycle import ensure_container_running

        await ensure_container_running(container_ref=row.container_ref)
        # Best-effort: drain leave-queued triggers without requiring a separate worker tick.
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

    async def delete(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        purge_workspace: bool = False,
    ) -> dict:
        """Soft-delete project: stop runtime/Pod, hide from UI; blobs kept unless purge_workspace."""
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        await stop_project_runtime(self._session, project_id=row.id)
        await pause_container(container_ref=row.container_ref)
        row.status = ProjectStatus.DELETED
        await self._platform_events.emit(
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
        """Restore soft-deleted project → paused (explicit resume starts Pod). No cascade."""
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
        await self._platform_events.emit(
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
        """Hard-purge: wipe MinIO after soft-delete. Row stays as deleted tombstone."""
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
        await stop_project_runtime(self._session, project_id=row.id)
        await pause_container(container_ref=row.container_ref)
        wipe = await self._wipe_workspace(row)
        await self._platform_events.emit(
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
