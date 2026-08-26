"""Admin read-model for Project Containers (P1 transitional — Project as proxy)."""

from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.project_service import ProjectService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

# active/running first, then paused, then other non-deleted
_STATUS_ORDER = case(
    (ProjectRow.status == ProjectStatus.ACTIVE, 0),
    (ProjectRow.status == ProjectStatus.PAUSED, 1),
    else_=2,
)


def _item(
    *,
    project: ProjectRow,
    company_name: str | None,
    cabinet_name: str | None,
    owner_email: str | None,
    owner_display_name: str | None,
) -> dict:
    return {
        "id": project.id,
        "project_id": project.id,
        "project_name": project.name,
        "status": project.status,
        "container_ref": project.container_ref,
        "workspace_key": project.workspace_key,
        "agent_provider": project.agent_provider,
        "company_id": project.company_id,
        "company_name": company_name,
        "cabinet_id": project.cabinet_id,
        "cabinet_name": cabinet_name,
        "owner_employee_id": project.owner_employee_id,
        "owner_email": owner_email,
        "owner_display_name": owner_display_name,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        # P1 holes — filled in P3/P4
        "runtime_metrics": None,
        "k8s_phase": None,
        "last_error": None,
    }


class AdminContainerReadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectService(session)

    async def list_containers(self, *, limit: int = 200) -> dict:
        stmt = (
            select(
                ProjectRow,
                CompanyRow.name,
                CabinetInstanceRow.name,
                EmployeeRow.email,
                EmployeeRow.display_name,
            )
            .join(CompanyRow, CompanyRow.id == ProjectRow.company_id)
            .join(CabinetInstanceRow, CabinetInstanceRow.id == ProjectRow.cabinet_id)
            .join(EmployeeRow, EmployeeRow.id == ProjectRow.owner_employee_id)
            .where(ProjectRow.status != ProjectStatus.DELETED)
            .order_by(_STATUS_ORDER, ProjectRow.updated_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        items = [
            _item(
                project=project,
                company_name=company_name,
                cabinet_name=cabinet_name,
                owner_email=owner_email,
                owner_display_name=owner_display_name,
            )
            for project, company_name, cabinet_name, owner_email, owner_display_name in rows
        ]
        return {"items": items}

    async def get_container(self, project_id: str) -> dict:
        stmt = (
            select(
                ProjectRow,
                CompanyRow.name,
                CabinetInstanceRow.name,
                EmployeeRow.email,
                EmployeeRow.display_name,
            )
            .join(CompanyRow, CompanyRow.id == ProjectRow.company_id)
            .join(CabinetInstanceRow, CabinetInstanceRow.id == ProjectRow.cabinet_id)
            .join(EmployeeRow, EmployeeRow.id == ProjectRow.owner_employee_id)
            .where(ProjectRow.id == project_id)
            .where(ProjectRow.status != ProjectStatus.DELETED)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Container not found")
        project, company_name, cabinet_name, owner_email, owner_display_name = row
        return _item(
            project=project,
            company_name=company_name,
            cabinet_name=cabinet_name,
            owner_email=owner_email,
            owner_display_name=owner_display_name,
        )

    async def pause(self, *, project_id: str, principal: Principal) -> dict:
        await self._projects.pause(project_id=project_id, principal=principal, employee=None)
        return await self.get_container(project_id)

    async def resume(self, *, project_id: str, principal: Principal) -> dict:
        await self._projects.resume(project_id=project_id, principal=principal, employee=None)
        return await self.get_container(project_id)

    async def delete(self, *, project_id: str, principal: Principal) -> dict:
        return await self._projects.delete(project_id=project_id, principal=principal, employee=None)
