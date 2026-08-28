"""Admin recycle bin — list / restore / purge soft-deleted entities."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.companies.service import CompaniesCommandService
from prodavan.application.employees.service import EmployeesCommandService
from prodavan.application.project_service import ProjectCommand
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class RecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> dict:
        companies_q = await self._session.execute(
            select(CompanyRow)
            .where(CompanyRow.deleted_at.is_not(None))
            .order_by(CompanyRow.deleted_at.desc())
        )
        employees_q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.deleted_at.is_not(None))
            .order_by(EmployeeRow.deleted_at.desc())
        )
        cabinets_q = await self._session.execute(
            select(CabinetInstanceRow)
            .where(CabinetInstanceRow.status == CabinetStatus.DELETED)
            .order_by(CabinetInstanceRow.updated_at.desc())
        )
        projects_q = await self._session.execute(
            select(ProjectRow)
            .where(ProjectRow.status == ProjectStatus.DELETED)
            .order_by(ProjectRow.updated_at.desc())
        )
        return {
            "companies": [
                {
                    "id": c.id,
                    "name": c.name,
                    "deleted_at": c.deleted_at.isoformat() if c.deleted_at else None,
                    "status": c.status,
                }
                for c in companies_q.scalars().all()
            ],
            "employees": [
                {
                    "id": e.id,
                    "email": e.email,
                    "deleted_at": e.deleted_at.isoformat() if e.deleted_at else None,
                    "status": e.status,
                }
                for e in employees_q.scalars().all()
            ],
            "cabinets": [
                {
                    "id": c.id,
                    "name": c.name,
                    "company_id": c.company_id,
                    "status": c.status,
                }
                for c in cabinets_q.scalars().all()
            ],
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "company_id": p.company_id,
                    "cabinet_id": p.cabinet_id,
                    "status": p.status,
                }
                for p in projects_q.scalars().all()
            ],
        }

    async def restore(self, *, entity: str, entity_id: str, principal: Principal) -> dict:
        if entity == "companies":
            return await CompaniesCommandService(self._session).restore(entity_id, principal=principal)
        if entity == "employees":
            emp = await EmployeesCommandService(self._session).restore(
                employee_id=entity_id, principal=principal
            )
            return {"id": emp.id, "restored": True, "status": emp.status}
        if entity == "cabinets":
            return await CabinetInstanceService(self._session).restore(
                cabinet_id=entity_id, principal=principal, employee=None
            )
        if entity == "projects":
            return await ProjectCommand(self._session).restore(
                project_id=entity_id, principal=principal, employee=None
            )
        from prodavan.domain.errors import AppError

        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="entity must be companies|employees|cabinets|projects",
        )

    async def purge(self, *, entity: str, entity_id: str, principal: Principal) -> dict:
        if entity == "companies":
            return await CompaniesCommandService(self._session).purge(entity_id, principal=principal)
        if entity == "cabinets":
            return await CabinetInstanceService(self._session).delete_with_cascade(cabinet_id=entity_id)
        if entity == "projects":
            return await ProjectCommand(self._session).purge(
                project_id=entity_id, principal=principal, employee=None
            )
        if entity == "employees":
            from prodavan.domain.errors import AppError

            emp = await self._session.get(EmployeeRow, entity_id)
            if emp is None or emp.deleted_at is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="soft-delete the employee before purge",
                )
            await self._session.delete(emp)
            await self._session.commit()
            return {"id": entity_id, "purged": True}
        from prodavan.domain.errors import AppError

        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="entity must be companies|employees|cabinets|projects",
        )
