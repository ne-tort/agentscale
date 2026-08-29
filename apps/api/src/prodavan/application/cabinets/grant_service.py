"""Cabinet company grants + employee assignments (N:M)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets.types import (
    CabinetAssignmentStatus,
    CabinetCompanyGrantScope,
    CabinetGrantMode,
    CabinetGrantStatus,
    CabinetOwnerScope,
    CabinetStatus,
)
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow


class CabinetGrantService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_company_ids(self, cabinet_id: str, *, active_only: bool = True) -> list[str]:
        q = select(CabinetCompanyGrantRow.company_id).where(
            CabinetCompanyGrantRow.cabinet_id == cabinet_id
        )
        if active_only:
            q = q.where(CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE)
        rows = await self._session.execute(q)
        return list(rows.scalars().all())

    async def list_companies(self, cabinet_id: str) -> list[dict]:
        q = await self._session.execute(
            select(CabinetCompanyGrantRow, CompanyRow.name)
            .join(CompanyRow, CompanyRow.id == CabinetCompanyGrantRow.company_id)
            .where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
            )
            .order_by(CompanyRow.name)
        )
        return [
            {
                "company_id": grant.company_id,
                "company_name": name,
                "mode": grant.mode,
                "status": grant.status,
            }
            for grant, name in q.all()
        ]

    async def replace_company_grants(
        self,
        cabinet_id: str,
        company_ids: list[str],
        *,
        mode: str = CabinetGrantMode.ASSIGNED_RO,
    ) -> list[str]:
        unique = list(dict.fromkeys(company_ids))
        for cid in unique:
            co = await self._session.get(CompanyRow, cid)
            if co is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown company_id: {cid}",
                )
        existing = await self._session.execute(
            select(CabinetCompanyGrantRow).where(CabinetCompanyGrantRow.cabinet_id == cabinet_id)
        )
        for row in existing.scalars().all():
            await self._session.delete(row)
        await self._session.flush()
        for cid in unique:
            self._session.add(
                CabinetCompanyGrantRow(
                    cabinet_id=cabinet_id,
                    company_id=cid,
                    mode=mode,
                    status=CabinetGrantStatus.ACTIVE,
                )
            )
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is not None:
            inst.company_id = unique[0] if unique else None
        await self._session.flush()
        return unique

    async def add_company_grant(
        self,
        cabinet_id: str,
        company_id: str,
        *,
        mode: str = CabinetGrantMode.ASSIGNED_RO,
    ) -> None:
        co = await self._session.get(CompanyRow, company_id)
        if co is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="company not found")
        existing = await self._session.execute(
            select(CabinetCompanyGrantRow).where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.company_id == company_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            self._session.add(
                CabinetCompanyGrantRow(
                    cabinet_id=cabinet_id,
                    company_id=company_id,
                    mode=mode,
                    status=CabinetGrantStatus.ACTIVE,
                )
            )
        else:
            row.status = CabinetGrantStatus.ACTIVE
            row.mode = mode
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is not None and inst.company_id is None:
            inst.company_id = company_id
        await self._session.flush()

    async def has_active_company_grant(self, cabinet_id: str, company_id: str) -> bool:
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is None:
            return False
        if inst.company_grant_scope == CabinetCompanyGrantScope.ALL:
            return True
        if (
            inst.owner_scope == CabinetOwnerScope.COMPANY
            and inst.owner_company_id == company_id
            and inst.status != CabinetStatus.DELETED
        ):
            return True
        q = await self._session.execute(
            select(CabinetCompanyGrantRow.id).where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.company_id == company_id,
                CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
            )
        )
        return q.scalar_one_or_none() is not None

    async def list_assignments(self, cabinet_id: str) -> list[dict]:
        q = await self._session.execute(
            select(CabinetEmployeeAssignmentRow, EmployeeRow.email)
            .join(EmployeeRow, EmployeeRow.id == CabinetEmployeeAssignmentRow.employee_id)
            .where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
            )
            .order_by(EmployeeRow.email)
        )
        return [
            {
                "employee_id": row.employee_id,
                "employee_email": email,
                "role": row.role,
                "status": row.status,
            }
            for row, email in q.all()
        ]

    async def assign_employee(
        self,
        *,
        cabinet_id: str,
        employee_id: str,
        company_id: str,
    ) -> None:
        if not await self.has_active_company_grant(cabinet_id, company_id):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="cabinet not granted to company",
            )
        from prodavan.application.relations.query import RelationsQuery

        if not await RelationsQuery(self._session).has_membership(
            employee_id=employee_id, company_id=company_id
        ):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="employee is not a member of company",
            )
        existing = await self._session.execute(
            select(CabinetEmployeeAssignmentRow).where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.employee_id == employee_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            self._session.add(
                CabinetEmployeeAssignmentRow(
                    cabinet_id=cabinet_id,
                    employee_id=employee_id,
                    status=CabinetAssignmentStatus.ACTIVE,
                )
            )
        else:
            row.status = CabinetAssignmentStatus.ACTIVE
        await self._session.flush()

    async def revoke_employee(self, *, cabinet_id: str, employee_id: str) -> None:
        existing = await self._session.execute(
            select(CabinetEmployeeAssignmentRow).where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.employee_id == employee_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="assignment not found")
        row.status = CabinetAssignmentStatus.REVOKED
        await self._session.flush()

    async def has_active_assignment(self, cabinet_id: str, employee_id: str) -> bool:
        q = await self._session.execute(
            select(CabinetEmployeeAssignmentRow.id).where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.employee_id == employee_id,
                CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
            )
        )
        return q.scalar_one_or_none() is not None

    async def assignment_count(self, cabinet_id: str) -> int:
        q = await self._session.execute(
            select(CabinetEmployeeAssignmentRow.id).where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
            )
        )
        return len(list(q.scalars().all()))
