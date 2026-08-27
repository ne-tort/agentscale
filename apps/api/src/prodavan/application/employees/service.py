"""Employees BC — invite / pause (disable) / soft-delete (Auth via Kafka only)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from prodavan.application.auth.register import publish_register_command
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_EMPLOYEE, EmployeeStatus, MembershipRole, Principal
from prodavan.domain.lifecycle import employee_is_soft_deleted, soft_deleted_at_now
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
            select(EmployeeRow)
            .where(EmployeeRow.email == email_l, EmployeeRow.deleted_at.is_(None))
            .order_by(EmployeeRow.created_at.asc())
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
        from prodavan.application.relations.commands import RelationsCommand

        await RelationsCommand(self._session).ensure_membership(
            company_id=company_id, employee_id=employee.id, role=role
        )
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
        publish_auth: bool = True,
        publish_auth_delete: bool | None = None,
    ) -> EmployeeRow:
        """Pause employee (visible): status=disabled + Auth disable. No project wipe."""
        from prodavan.application.auth.lifecycle import publish_disable_command
        from prodavan.application.projects.platform_event_service import PlatformEventService

        do_publish = publish_auth if publish_auth_delete is None else publish_auth_delete
        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is None or employee_is_soft_deleted(emp):
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
                payload={"employee_id": emp.id, "email": emp.email, "paused": True},
            )
        await self._session.commit()
        await self._session.refresh(emp)

        if do_publish:
            await publish_disable_command(
                client_ref=f"employee:{emp.id}",
                sub=emp.keycloak_sub,
                username=emp.email,
                email=emp.email,
            )
        return emp

    async def soft_delete(
        self,
        *,
        employee_id: str,
        principal: Principal | None = None,
        publish_auth: bool = True,
    ) -> EmployeeRow:
        """Soft-delete employee (hidden). No cascade to projects."""
        from prodavan.application.auth.lifecycle import publish_delete_command, publish_disable_command
        from prodavan.application.projects.platform_event_service import PlatformEventService

        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is None or employee_is_soft_deleted(emp):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")

        emp.status = EmployeeStatus.DISABLED
        emp.deleted_at = soft_deleted_at_now()
        actor = principal or Principal(sub="system")
        events = PlatformEventService(self._session)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.soft_deleted",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "email": emp.email},
            )
        await self._session.commit()
        await self._session.refresh(emp)

        if publish_auth:
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

    async def restore(
        self,
        *,
        employee_id: str,
        principal: Principal | None = None,
    ) -> EmployeeRow:
        """Restore soft-deleted employee → paused (disabled). Explicit re-enable separately."""
        from prodavan.application.projects.platform_event_service import PlatformEventService

        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is None or not employee_is_soft_deleted(emp):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="employee is not soft-deleted",
            )
        emp.deleted_at = None
        emp.status = EmployeeStatus.DISABLED
        actor = principal or Principal(sub="system")
        events = PlatformEventService(self._session)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.restored",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "email": emp.email},
            )
        await self._session.commit()
        await self._session.refresh(emp)
        return emp
