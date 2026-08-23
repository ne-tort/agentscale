"""Identity application services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from prodavan.domain.errors import AppError
from prodavan.domain.identity import (
    ROLE_COMPANY_ADMIN,
    Contour,
    EmployeeStatus,
    MembershipRole,
    Principal,
)
from prodavan.infrastructure.keycloak.invite import KeycloakInvitePort
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow


class EntitlementService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_employee_by_sub(self, sub: str) -> EmployeeRow | None:
        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.keycloak_sub == sub)
            .options(selectinload(EmployeeRow.memberships))
        )
        return q.scalar_one_or_none()

    async def _bind_invited_by_email(self, principal: Principal) -> EmployeeRow | None:
        if not principal.email:
            return None
        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.email == principal.email.lower())
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is not None and emp.status == EmployeeStatus.INVITED:
            emp.keycloak_sub = principal.sub
            emp.status = EmployeeStatus.ACTIVE
            await self._session.commit()
            await self._session.refresh(emp)
        return emp

    async def ensure_active_employee(self, principal: Principal) -> EmployeeRow | None:
        """Platform admin may have no Employee row; dual-role loads row when email/sub present."""
        emp = await self.get_employee_by_sub(principal.sub)
        if emp is None:
            emp = await self._bind_invited_by_email(principal)

        if principal.is_platform_admin:
            if emp is not None and emp.status == EmployeeStatus.DISABLED:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="Employee disabled",
                )
            return emp

        if emp is None:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="No employee record for subject",
            )
        if emp.status == EmployeeStatus.DISABLED:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="Employee disabled",
            )
        if emp.status == EmployeeStatus.INVITED and emp.keycloak_sub is None:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="Invite not activated",
            )
        return emp

    async def require_membership(self, employee_id: str, company_id: str) -> MembershipRow:
        q = await self._session.execute(
            select(MembershipRow).where(
                MembershipRow.employee_id == employee_id,
                MembershipRow.company_id == company_id,
            )
        )
        mem = q.scalar_one_or_none()
        if mem is None:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="Not a member of company",
            )
        return mem

    async def require_company_admin(self, employee_id: str, company_id: str) -> MembershipRow:
        mem = await self.require_membership(employee_id, company_id)
        if mem.role != MembershipRole.COMPANY_ADMIN and mem.role != ROLE_COMPANY_ADMIN:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="company.admin required",
            )
        return mem

    def contours_for(self, principal: Principal, employee: EmployeeRow | None) -> list[str]:
        out: list[str] = []
        if principal.is_platform_admin:
            out.append(Contour.PLATFORM_ADMIN)
        if employee is not None:
            roles = {m.role for m in employee.memberships}
            if MembershipRole.COMPANY_ADMIN in roles or ROLE_COMPANY_ADMIN in roles:
                out.append(Contour.COMPANY)
            if employee.memberships:
                out.append(Contour.EMPLOYEE)
        return out


class IdentityCommandService:
    def __init__(self, session: AsyncSession, invites: KeycloakInvitePort) -> None:
        self._session = session
        self._invites = invites

    async def create_company_with_admin(
        self,
        *,
        name: str,
        admin_email: str,
        admin_display_name: str | None,
    ) -> tuple[CompanyRow, EmployeeRow]:
        if not admin_email or "@" not in admin_email:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="email required")
        # No password accepted — invite via KC only
        await self._invites.invite_user(email=admin_email.lower(), display_name=admin_display_name)
        company = CompanyRow(name=name.strip())
        employee = EmployeeRow(
            email=admin_email.lower(),
            display_name=admin_display_name,
            status=EmployeeStatus.INVITED,
        )
        self._session.add(company)
        await self._session.flush()
        self._session.add(employee)
        await self._session.flush()
        self._session.add(
            MembershipRow(
                company_id=company.id,
                employee_id=employee.id,
                role=MembershipRole.COMPANY_ADMIN,
            )
        )
        await self._session.commit()
        await self._session.refresh(company)
        await self._session.refresh(employee)
        return company, employee

    async def invite_employee(
        self,
        *,
        company_id: str,
        email: str,
        display_name: str | None,
        role: str = MembershipRole.MEMBER,
    ) -> EmployeeRow:
        await self._invites.invite_user(email=email.lower(), display_name=display_name)
        employee = EmployeeRow(
            email=email.lower(),
            display_name=display_name,
            status=EmployeeStatus.INVITED,
        )
        self._session.add(employee)
        await self._session.flush()
        self._session.add(
            MembershipRow(company_id=company_id, employee_id=employee.id, role=role)
        )
        await self._session.commit()
        await self._session.refresh(employee)
        return employee

    async def disable_employee(self, *, employee_id: str, principal: Principal | None = None) -> EmployeeRow:
        from prodavan.application.projects.platform_event_service import PlatformEventService

        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        emp.status = EmployeeStatus.DISABLED
        await self._invites.disable_user(keycloak_user_id=emp.keycloak_sub, email=emp.email)
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
        return emp
