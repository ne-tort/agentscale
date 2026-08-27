"""RelationsQuery — sync ACL / graph reads (facade over existing SoT tables)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets.types import CabinetAssignmentStatus, CabinetGrantStatus
from prodavan.infrastructure.persistence.models.ai_keys import CompanyAiKeyBindingRow
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
)
from prodavan.infrastructure.persistence.models.identity import MembershipRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleCompanyGrantRow,
)


class RelationsQuery:
    """Central read API for who-is-linked-to-whom. Prefer this over raw MembershipRow/grant ORM."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_membership(self, *, employee_id: str, company_id: str) -> bool:
        q = await self._session.execute(
            select(MembershipRow.id).where(
                MembershipRow.employee_id == employee_id,
                MembershipRow.company_id == company_id,
            )
        )
        return q.scalar_one_or_none() is not None

    async def membership_role(self, *, employee_id: str, company_id: str) -> str | None:
        q = await self._session.execute(
            select(MembershipRow.role).where(
                MembershipRow.employee_id == employee_id,
                MembershipRow.company_id == company_id,
            )
        )
        return q.scalar_one_or_none()

    async def company_ids_for_employee(self, employee_id: str) -> set[str]:
        q = await self._session.execute(
            select(MembershipRow.company_id).where(MembershipRow.employee_id == employee_id)
        )
        return set(q.scalars().all())

    async def has_cabinet_company_grant(self, *, cabinet_id: str, company_id: str) -> bool:
        q = await self._session.execute(
            select(CabinetCompanyGrantRow.id).where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.company_id == company_id,
                CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
            )
        )
        return q.scalar_one_or_none() is not None

    async def has_cabinet_assignment(self, *, cabinet_id: str, employee_id: str) -> bool:
        q = await self._session.execute(
            select(CabinetEmployeeAssignmentRow.id).where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.employee_id == employee_id,
                CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
            )
        )
        return q.scalar_one_or_none() is not None

    async def company_ids_for_cabinet(self, cabinet_id: str) -> list[str]:
        q = await self._session.execute(
            select(CabinetCompanyGrantRow.company_id).where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
            )
        )
        return list(q.scalars().all())

    async def has_ai_key_binding(self, *, key_id: str, company_id: str) -> bool:
        q = await self._session.execute(
            select(CompanyAiKeyBindingRow.id).where(
                CompanyAiKeyBindingRow.key_id == key_id,
                CompanyAiKeyBindingRow.company_id == company_id,
            )
        )
        return q.scalar_one_or_none() is not None

    async def has_module_company_grant(self, *, module_id: str, company_id: str) -> bool:
        q = await self._session.execute(
            select(ModuleCompanyGrantRow.id).where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.company_id == company_id,
                ModuleCompanyGrantRow.status == "active",
            )
        )
        return q.scalar_one_or_none() is not None

    async def has_module_cabinet_binding(self, *, module_id: str, cabinet_id: str) -> bool:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.id).where(
                ModuleCabinetBindingRow.module_id == module_id,
                ModuleCabinetBindingRow.cabinet_id == cabinet_id,
            )
        )
        return q.scalar_one_or_none() is not None
