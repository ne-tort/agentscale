"""Project CRUD + lifecycle (L07)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.projects.access import ProjectAccessService
from prodavan.application.projects.materialize import get_materialize_service
from prodavan.application.projects.trigger_service import ProjectTriggerService
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


def _public(row: ProjectRow) -> dict:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "cabinet_id": row.cabinet_id,
        "owner_employee_id": row.owner_employee_id,
        "name": row.name,
        "slug": row.slug,
        "status": row.status,
        "workspace_key": row.workspace_key,
        "container_ref": row.container_ref,
        "agent_provider": row.agent_provider,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessService(session)
        self._cabinets = CabinetAccessService(session)
        self._materialize = get_materialize_service()
        self._triggers = ProjectTriggerService(session)

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
            company_id=inst.company_id,
            cabinet_id=cabinet_id,
            owner_employee_id=employee.id,
            name=name.strip(),
            slug=slug,
            status=ProjectStatus.ACTIVE,
            workspace_key=ws_key,
            container_ref=container_ref_for(ws_key),
            agent_provider=agent_provider,
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
        await self._session.commit()
        await self._session.refresh(row)
        out = _public(row)
        out["materialize"] = {
            "workspace_root": mat.workspace_root,
            "mcp_config_path": mat.mcp_config_path,
            "status": mat.status,
            "package_names": list(mat.package_names),
            "sandbox_packages": list(mat.sandbox_packages),
        }
        return out

    async def list_for_cabinet(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        await self._cabinets.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        q = await self._session.execute(
            select(ProjectRow)
            .where(ProjectRow.cabinet_id == cabinet_id, ProjectRow.status != ProjectStatus.DELETED)
            .order_by(ProjectRow.created_at.desc())
        )
        return [_public(r) for r in q.scalars().all()]

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
        return _public(row)

    async def rematerialize(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
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
        }

    async def pause(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        row.status = ProjectStatus.PAUSED
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row)

    async def resume(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        if row.status != ProjectStatus.PAUSED:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project is not paused",
            )
        row.status = ProjectStatus.ACTIVE
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row)

    async def delete(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        purge_workspace: bool = True,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        row.status = ProjectStatus.DELETED
        await self._session.commit()
        if purge_workspace:
            WorkspaceLayoutWriter(workspace_key=row.workspace_key).remove_project_tree()
        await self._session.refresh(row)
        return _public(row)
