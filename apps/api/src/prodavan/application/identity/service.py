"""Identity application services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from prodavan.domain.errors import AppError
from prodavan.domain.identity import (
    ROLE_COMPANY_ADMIN,
    ROLE_EMPLOYEE,
    Contour,
    EmployeeStatus,
    MembershipRole,
    Principal,
)
from prodavan.infrastructure.keycloak.provisioning import IdentityProvisioningPort
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

    async def get_company_by_sub(self, sub: str) -> CompanyRow | None:
        q = await self._session.execute(select(CompanyRow).where(CompanyRow.keycloak_sub == sub))
        return q.scalar_one_or_none()

    async def ensure_company_principal(self, principal: Principal) -> CompanyRow | None:
        """Resolve Company org principal by keycloak_sub or username==company_id."""
        if not principal.is_company_principal:
            return None
        company = await self.get_company_by_sub(principal.sub)
        if company is not None:
            return company
        # Soft-bind: KC username is company_id (no email login for companies).
        if principal.username:
            company = await self._session.get(CompanyRow, principal.username)
            if company is not None and (
                company.keycloak_sub is None or company.keycloak_sub == principal.sub
            ):
                company.keycloak_sub = principal.sub
                await self._session.commit()
                await self._session.refresh(company)
                return company
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="No company record for company principal",
        )

    async def _bind_invited_by_email(self, principal: Principal) -> EmployeeRow | None:
        if not principal.email:
            return None
        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.email == principal.email.lower())
            .options(selectinload(EmployeeRow.memberships))
            .order_by(EmployeeRow.created_at.desc())
        )
        rows = list(q.scalars().unique().all())
        if not rows:
            return None
        # Prefer active, then unbound invite. Never hijack a row already bound to another sub.
        emp = next((r for r in rows if r.status == EmployeeStatus.ACTIVE), None)
        if emp is None:
            emp = next(
                (
                    r
                    for r in rows
                    if r.status == EmployeeStatus.INVITED and r.keycloak_sub is None
                ),
                None,
            )
        if emp is None:
            emp = next(
                (r for r in rows if r.keycloak_sub == principal.sub),
                None,
            )
        if emp is None:
            return None
        if emp.keycloak_sub is not None and emp.keycloak_sub != principal.sub:
            return None
        if emp.status == EmployeeStatus.INVITED and (
            emp.keycloak_sub is None or emp.keycloak_sub == principal.sub
        ):
            emp.keycloak_sub = principal.sub
            emp.status = EmployeeStatus.ACTIVE
            await self._session.commit()
            await self._session.refresh(emp)
        return emp

    async def ensure_active_employee(self, principal: Principal) -> EmployeeRow | None:
        """Platform admin / company principal may have no Employee row."""
        emp = await self.get_employee_by_sub(principal.sub)
        if emp is None:
            emp = await self._bind_invited_by_email(principal)
        elif emp.status == EmployeeStatus.INVITED:
            # Invite already stored keycloak_sub — first successful token activates.
            emp.status = EmployeeStatus.ACTIVE
            await self._session.commit()
            await self._session.refresh(emp)

        if principal.is_platform_admin or principal.is_company_principal:
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

    async def require_company_actor(
        self,
        principal: Principal,
        company_id: str,
        *,
        employee: EmployeeRow | None,
    ) -> CompanyRow:
        """Platform admin, org principal for this company, or human company.admin membership."""
        if principal.is_platform_admin:
            company = await self._session.get(CompanyRow, company_id)
            if company is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
            return company

        if principal.is_company_principal:
            company = await self.ensure_company_principal(principal)
            assert company is not None
            if company.id != company_id:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="company principal scope mismatch",
                )
            return company

        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        await self.require_company_admin(employee.id, company_id)
        company = await self._session.get(CompanyRow, company_id)
        if company is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        return company

    def contours_for(
        self,
        principal: Principal,
        employee: EmployeeRow | None,
        *,
        company: CompanyRow | None = None,
    ) -> list[str]:
        out: list[str] = []
        if principal.is_platform_admin:
            out.append(Contour.PLATFORM_ADMIN)
        if principal.is_company_principal or company is not None:
            if Contour.COMPANY not in out:
                out.append(Contour.COMPANY)
        if employee is not None:
            roles = {m.role for m in employee.memberships}
            if MembershipRole.COMPANY_ADMIN in roles or ROLE_COMPANY_ADMIN in roles:
                if Contour.COMPANY not in out:
                    out.append(Contour.COMPANY)
            if employee.memberships:
                out.append(Contour.EMPLOYEE)
        return out


class IdentityCommandService:
    def __init__(self, session: AsyncSession, provisioning: IdentityProvisioningPort) -> None:
        self._session = session
        self._provisioning = provisioning

    async def _upsert_invited_employee(
        self,
        *,
        email: str,
        display_name: str | None,
        keycloak_user_id: str,
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
        if employee.keycloak_sub is None:
            employee.keycloak_sub = keycloak_user_id
        if display_name and not employee.display_name:
            employee.display_name = display_name
        return employee

    async def _ensure_membership(
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

    async def create_company_with_admin(
        self,
        *,
        name: str,
        password: str,
        admin_email: str | None = None,
        admin_display_name: str | None = None,
        description: str | None = None,
        contact_email: str | None = None,
    ) -> tuple[CompanyRow, EmployeeRow | None]:
        """Create Company row, then KC principal username=company_id + password (email optional/unused for login)."""
        pwd = (password or "").strip()
        if len(pwd) < 8:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="password required (min 8 chars) for company Keycloak principal",
            )

        contact = (contact_email or "").strip().lower() or None
        if contact is not None and "@" not in contact:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="contact_email must be an email when provided",
            )

        company = CompanyRow(
            name=name.strip(),
            description=description.strip() if description and description.strip() else None,
            contact_email=contact,
            login_email=None,
        )
        self._session.add(company)
        await self._session.flush()

        principal = await self._provisioning.create_company_principal(
            username=company.id,
            password=pwd,
            display_name=name.strip(),
        )
        company.keycloak_sub = principal.keycloak_user_id

        email_raw = (admin_email or "").strip()
        if not email_raw:
            await self._session.commit()
            await self._session.refresh(company)
            return company, None

        if "@" not in email_raw:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="email required")

        email = email_raw.lower()
        invite = await self._provisioning.invite_employee(
            email=email,
            display_name=admin_display_name,
            realm_roles=[ROLE_EMPLOYEE],
        )
        employee = await self._upsert_invited_employee(
            email=email,
            display_name=admin_display_name,
            keycloak_user_id=invite.keycloak_user_id,
        )
        await self._ensure_membership(
            company_id=company.id,
            employee_id=employee.id,
            role=MembershipRole.COMPANY_ADMIN,
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
        invite = await self._provisioning.invite_employee(
            email=email.lower(),
            display_name=display_name,
            realm_roles=[ROLE_EMPLOYEE],
        )
        employee = await self._upsert_invited_employee(
            email=email,
            display_name=display_name,
            keycloak_user_id=invite.keycloak_user_id,
        )
        await self._ensure_membership(company_id=company_id, employee_id=employee.id, role=role)
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
        await self._provisioning.disable_user(keycloak_user_id=emp.keycloak_sub, email=emp.email)
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
