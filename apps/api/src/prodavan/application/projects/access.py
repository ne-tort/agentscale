"""Project access — cabinet-scoped ACL (L07)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class ProjectAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cabinets = CabinetAccessService(session)

    async def get_project(self, project_id: str) -> ProjectRow:
        row = await self._session.get(ProjectRow, project_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        return row

    async def require_access(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
        allow_paused: bool = False,
    ) -> ProjectRow:
        project = await self.get_project(project_id)
        if project.status == ProjectStatus.DELETED:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        await self._cabinets.require_access(
            cabinet_id=project.cabinet_id,
            principal=principal,
            employee=employee,
            write=write,
        )
        if write and project.status == ProjectStatus.PAUSED and not allow_paused:
            raise AppError(
                code="PROJECT_PAUSED",
                title="Project paused",
                status=409,
                detail="project is paused",
            )
        return project
