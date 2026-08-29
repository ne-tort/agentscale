"""Admin read-model for Project Containers (P1 transitional — Project as proxy)."""

from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.query import PodQuery
from prodavan.application.project_service import ProjectCommand
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

# active/running first, then paused, then other non-deleted
_STATUS_ORDER = case(
    (ProjectRow.status == ProjectStatus.ACTIVE, 0),
    (ProjectRow.status == ProjectStatus.ERROR, 1),
    (ProjectRow.status == ProjectStatus.PAUSED, 2),
    else_=3,
)


def _item(
    *,
    project: ProjectRow,
    company_name: str | None,
    cabinet_name: str | None,
    owner_email: str | None,
    owner_display_name: str | None,
    runtime: dict | None = None,
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
        "runtime": runtime,
        "runtime_metrics": runtime.get("metrics") if runtime else None,
        "k8s_phase": (runtime.get("phase") or runtime.get("status")) if runtime else None,
        "last_error": runtime.get("last_error") if runtime else None,
    }


class AdminContainerReadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectCommand(session)
        self._pods = PodQuery(session)

    async def _runtime_for(self, project_id: str) -> dict | None:
        return await self._pods.runtime_summary(project_id)

    def _base_stmt(self):
        return (
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
        )

    async def list_containers(self, *, limit: int = 200) -> dict:
        stmt = (
            self._base_stmt()
            .order_by(_STATUS_ORDER, ProjectRow.updated_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        items = []
        for project, company_name, cabinet_name, owner_email, owner_display_name in rows:
            runtime = await self._runtime_for(project.id)
            items.append(
                _item(
                    project=project,
                    company_name=company_name,
                    cabinet_name=cabinet_name,
                    owner_email=owner_email,
                    owner_display_name=owner_display_name,
                    runtime=runtime,
                )
            )
        return {"items": items}

    async def list_containers_for_company(self, company_id: str, *, limit: int = 200) -> dict:
        stmt = (
            self._base_stmt()
            .where(ProjectRow.company_id == company_id)
            .order_by(_STATUS_ORDER, ProjectRow.updated_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        items = []
        for project, company_name, cabinet_name, owner_email, owner_display_name in rows:
            runtime = await self._runtime_for(project.id)
            items.append(
                _item(
                    project=project,
                    company_name=company_name,
                    cabinet_name=cabinet_name,
                    owner_email=owner_email,
                    owner_display_name=owner_display_name,
                    runtime=runtime,
                )
            )
        return {"items": items}

    async def get_container(self, project_id: str) -> dict:
        stmt = self._base_stmt().where(ProjectRow.id == project_id)
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Container not found")
        project, company_name, cabinet_name, owner_email, owner_display_name = row
        runtime = await self._runtime_for(project.id)
        return _item(
            project=project,
            company_name=company_name,
            cabinet_name=cabinet_name,
            owner_email=owner_email,
            owner_display_name=owner_display_name,
            runtime=runtime,
        )

    async def get_container_for_company(self, company_id: str, project_id: str) -> dict:
        item = await self.get_container(project_id)
        if item.get("company_id") != company_id:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="Container not found",
            )
        return item

    async def _require_company_container(self, company_id: str, project_id: str) -> None:
        await self.get_container_for_company(company_id, project_id)

    async def pause(self, *, project_id: str, principal: Principal) -> dict:
        await self._projects.pause(project_id=project_id, principal=principal, employee=None)
        return await self.get_container(project_id)

    async def resume(self, *, project_id: str, principal: Principal) -> dict:
        await self._projects.resume(project_id=project_id, principal=principal, employee=None)
        return await self.get_container(project_id)

    async def delete(self, *, project_id: str, principal: Principal) -> dict:
        return await self._projects.delete(project_id=project_id, principal=principal, employee=None)

    async def pause_for_company(
        self,
        *,
        company_id: str,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        await self._require_company_container(company_id, project_id)
        await self._projects.pause(
            project_id=project_id, principal=principal, employee=employee
        )
        return await self.get_container(project_id)

    async def resume_for_company(
        self,
        *,
        company_id: str,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        await self._require_company_container(company_id, project_id)
        await self._projects.resume(
            project_id=project_id, principal=principal, employee=employee
        )
        return await self.get_container(project_id)

    async def delete_for_company(
        self,
        *,
        company_id: str,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        await self._require_company_container(company_id, project_id)
        return await self._projects.delete(
            project_id=project_id, principal=principal, employee=employee
        )
