"""Employees BC — invite / soft-disable (Auth via Kafka only)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from prodavan.application.auth.register import publish_register_command
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_EMPLOYEE, EmployeeStatus, MembershipRole, Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow, MembershipRow


class EmployeesCommandService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_invited(
        self,
        *,
        email: str,
        display_name: str | None,
        keycloak_user_id: str | None = None,
    ) -> EmployeeRow:
        email_l = email.lower().strip()
        existing = await self._session.execute(
            select(EmployeeRow).where(EmployeeRow.email == email_l).order_by(EmployeeRow.created_at.asc())
        )
        employee = existing.scalars().first()
        if employee is None:
            employee = EmployeeRow(
                email=email_l,
                display_name=display_name,
                status=EmployeeStatus.INVITED,
                keycloak_sub=keycloak_user_id,
            )
            self._session.add(employee)
            await self._session.flush()
            return employee
        if keycloak_user_id is not None and employee.keycloak_sub is None:
            employee.keycloak_sub = keycloak_user_id
        if display_name and not employee.display_name:
            employee.display_name = display_name
        return employee

    async def ensure_membership(
        self,
        *,
        company_id: str,
        employee_id: str,
        role: str,
    ) -> None:
        existing = await self._session.execute(
            select(MembershipRow).where(
                MembershipRow.company_id == company_id,
                MembershipRow.employee_id == employee_id,
            )
        )
        mem = existing.scalar_one_or_none()
        if mem is None:
            self._session.add(
                MembershipRow(company_id=company_id, employee_id=employee_id, role=role)
            )
        elif mem.role != role and role == MembershipRole.COMPANY_ADMIN:
            mem.role = role

    async def invite_employee(
        self,
        *,
        company_id: str,
        email: str,
        display_name: str | None,
        role: str = MembershipRole.MEMBER,
    ) -> EmployeeRow:
        employee = await self.upsert_invited(
            email=email,
            display_name=display_name,
            keycloak_user_id=None,
        )
        await self.ensure_membership(company_id=company_id, employee_id=employee.id, role=role)
        await self._session.commit()
        await self._session.refresh(employee)

        await publish_register_command(
            client_ref=f"employee:{employee.id}",
            username=employee.email,
            email=employee.email,
            password=None,
            realm_roles=[ROLE_EMPLOYEE],
            display_name=display_name,
        )
        await self._session.refresh(employee)
        return employee

    async def disable_employee(
        self,
        *,
        employee_id: str,
        principal: Principal | None = None,
        publish_auth_delete: bool = True,
    ) -> EmployeeRow:
        from prodavan.application.auth.lifecycle import publish_delete_command, publish_disable_command
        from prodavan.application.projects.platform_event_service import PlatformEventService

        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        if emp.status == EmployeeStatus.DISABLED:
            return emp

        emp.status = EmployeeStatus.DISABLED
        actor = principal or Principal(sub="system")
        events = PlatformEventService(self._session)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.disabled",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "email": emp.email},
            )
        await self._session.commit()
        await self._session.refresh(emp)

        if publish_auth_delete:
            # Prefer delete of KC user on soft-remove; disable if no sub yet (register may still race).
            if emp.keycloak_sub:
                await publish_delete_command(
                    client_ref=f"employee:{emp.id}",
                    sub=emp.keycloak_sub,
                    username=emp.email,
                    email=emp.email,
                )
            else:
                await publish_disable_command(
                    client_ref=f"employee:{emp.id}",
                    sub=None,
                    username=emp.email,
                    email=emp.email,
                )
        return emp
