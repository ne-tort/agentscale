"""Project access — cabinet scope + optional restricted visibility."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.relations.query import RelationsQuery
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.lifecycle import (
    project_is_completed,
    project_is_paused,
    project_is_soft_deleted,
    raise_if_paused,
)
from prodavan.domain.projects import ProjectVisibilityMode
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class ProjectAccessPolicy:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cabinets = CabinetAccessService(session)
        self._relations = RelationsQuery(session)

    async def get_project(self, project_id: str) -> ProjectRow:
        row = await self._session.get(ProjectRow, project_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        return row

    def _is_restricted(self, project: ProjectRow) -> bool:
        mode = getattr(project, "visibility_mode", None) or ProjectVisibilityMode.CABINET_SHARED
        return mode == ProjectVisibilityMode.RESTRICTED

    async def _bypasses_visibility(
        self,
        *,
        project: ProjectRow,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> bool:
        if principal.is_platform_admin:
            return True
        if principal.is_company_principal and principal.username == project.company_id:
            return True
        if employee is not None:
            from prodavan.application.identity.service import EntitlementService

            try:
                await EntitlementService(self._session).require_company_admin(
                    employee.id, project.company_id
                )
                return True
            except AppError:
                pass
        return False

    async def can_view_project(
        self,
        *,
        project: ProjectRow,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> bool:
        if await self._bypasses_visibility(project=project, principal=principal, employee=employee):
            return True
        if employee is None:
            return False
        if not await self._relations.has_cabinet_assignment(
            cabinet_id=project.cabinet_id, employee_id=employee.id
        ):
            return False
        if not self._is_restricted(project):
            return True
        return await self._relations.has_project_assignment(
            project_id=project.id, employee_id=employee.id
        )

    async def require_access(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
        allow_paused: bool = False,
        allow_deleted: bool = False,
        skip_visibility: bool = False,
    ) -> ProjectRow:
        project = await self.get_project(project_id)
        if project_is_soft_deleted(project) and not allow_deleted:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        await self._cabinets.require_access(
            cabinet_id=project.cabinet_id,
            principal=principal,
            employee=employee,
            write=write,
        )
        if not skip_visibility and not await self.can_view_project(
            project=project, principal=principal, employee=employee
        ):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        if write and (project_is_paused(project) or project_is_completed(project)) and not allow_paused:
            raise_if_paused(code="PROJECT_PAUSED", detail="project is paused or completed")
        return project

