"""Project↔employee assignment SoT — mutated only via RelationsCommand."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectEmployeeAssignmentRow


class ProjectGrantService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_employee_ids(self, project_id: str) -> list[str]:
        q = await self._session.execute(
            select(ProjectEmployeeAssignmentRow.employee_id).where(
                ProjectEmployeeAssignmentRow.project_id == project_id,
                ProjectEmployeeAssignmentRow.status == "active",
            )
        )
        return list(q.scalars().all())

    async def assign_employee(
        self,
        *,
        project_id: str,
        cabinet_id: str,
        employee_id: str,
    ) -> None:
        emp = await self._session.get(EmployeeRow, employee_id)
        if emp is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unknown employee_id: {employee_id}",
            )
        existing = await self._session.execute(
            select(ProjectEmployeeAssignmentRow).where(
                ProjectEmployeeAssignmentRow.project_id == project_id,
                ProjectEmployeeAssignmentRow.employee_id == employee_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            self._session.add(
                ProjectEmployeeAssignmentRow(
                    project_id=project_id,
                    cabinet_id=cabinet_id,
                    employee_id=employee_id,
                    status="active",
                )
            )
        else:
            row.status = "active"
        await self._session.flush()

    async def revoke_employee(self, *, project_id: str, employee_id: str) -> None:
        q = await self._session.execute(
            select(ProjectEmployeeAssignmentRow).where(
                ProjectEmployeeAssignmentRow.project_id == project_id,
                ProjectEmployeeAssignmentRow.employee_id == employee_id,
            )
        )
        row = q.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def replace_assignments(
        self,
        *,
        project_id: str,
        cabinet_id: str,
        employee_ids: list[str],
    ) -> list[str]:
        unique = list(dict.fromkeys(employee_ids))
        for eid in unique:
            emp = await self._session.get(EmployeeRow, eid)
            if emp is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown employee_id: {eid}",
                )
        existing = await self._session.execute(
            select(ProjectEmployeeAssignmentRow).where(
                ProjectEmployeeAssignmentRow.project_id == project_id
            )
        )
        for row in existing.scalars().all():
            await self._session.delete(row)
        await self._session.flush()
        for eid in unique:
            self._session.add(
                ProjectEmployeeAssignmentRow(
                    project_id=project_id,
                    cabinet_id=cabinet_id,
                    employee_id=eid,
                    status="active",
                )
            )
        await self._session.flush()
        return unique
