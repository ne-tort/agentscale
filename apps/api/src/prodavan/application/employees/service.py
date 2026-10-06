"""Employees BC — create / pause (disable) / soft-delete (Auth via Kafka only)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from prodavan.application.auth.register import publish_register_command
from prodavan.domain.companies.login import validate_login_username
from prodavan.domain.employees.login import employee_effective_login, employee_login_handle
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_EMPLOYEE, EmployeeStatus, MembershipRole, Principal
from prodavan.domain.lifecycle import employee_is_soft_deleted, soft_deleted_at_now
from prodavan.infrastructure.persistence.models.identity import EmployeeRow, MembershipRow


class EmployeesCommandService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _company_login_slug(self, company_id: str) -> str:
        from prodavan.infrastructure.persistence.models.identity import CompanyRow

        company = await self._session.get(CompanyRow, company_id)
        if company is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        slug = (company.login_slug or "").strip()
        if not slug:
            # Pre-migration row (should not exist): derive from name.
            from prodavan.domain.companies.login import slugify_company_name

            slug = slugify_company_name(company.name)
        return slug

    async def _login_taken(self, login: str, *, exclude_id: str | None = None) -> bool:
        q = select(EmployeeRow.id).where(
            EmployeeRow.login == login,
            EmployeeRow.deleted_at.is_(None),
        )
        if exclude_id is not None:
            q = q.where(EmployeeRow.id != exclude_id)
        row = await self._session.execute(q)
        return row.scalar_one_or_none() is not None

    async def _assign_login(
        self,
        employee: EmployeeRow,
        *,
        candidate: str | None = None,
        company_login_slug: str | None = None,
    ) -> None:
        raw = (candidate or "").strip()
        if len(raw) >= 3:
            try:
                local = validate_login_username(raw)
            except AppError:
                local = None
        else:
            local = None
        if local is not None and company_login_slug:
            handle = employee_login_handle(local, company_login_slug)
            if not await self._login_taken(handle, exclude_id=employee.id):
                employee.login = handle
                return
        if local is None:
            employee.login = employee.id
        elif await self._login_taken(local, exclude_id=employee.id):
            employee.login = employee.id
        else:
            employee.login = local

    async def upsert_invited(
        self,
        *,
        email: str,
        display_name: str | None,
        keycloak_user_id: str | None = None,
        company_login_slug: str | None = None,
    ) -> EmployeeRow:
        email_l = email.lower().strip()
        existing = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.email == email_l, EmployeeRow.deleted_at.is_(None))
            .order_by(EmployeeRow.created_at.asc())
        )
        employee = existing.scalars().first()
        if employee is None:
            local = email_l.split("@", 1)[0] if "@" in email_l else email_l
            handle = (
                employee_login_handle(local, company_login_slug)
                if company_login_slug
                else (local or "pending")
            )
            employee = EmployeeRow(
                email=email_l,
                login=handle[:200],
                display_name=display_name,
                status=EmployeeStatus.INVITED,
                keycloak_sub=keycloak_user_id,
            )
            self._session.add(employee)
            await self._session.flush()
            await self._assign_login(employee, candidate=local, company_login_slug=company_login_slug)
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

    async def create_employee(
        self,
        *,
        company_id: str,
        login: str,
        password: str,
        contact_email: str | None = None,
        display_name: str | None = None,
        role: str = MembershipRole.MEMBER,
    ) -> EmployeeRow:
        login_val = validate_login_username(login)
        pwd = (password or "").strip()
        if len(pwd) < 8:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="password required (min 8 chars)",
            )
        contact = (contact_email or "").strip().lower() or None
        if contact is not None and "@" not in contact:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="contact_email must be an email when provided",
            )
        company_slug = await self._company_login_slug(company_id)
        handle = employee_login_handle(login_val, company_slug)
        if await self._login_taken(handle):
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail="employee name already used in this company",
            )

        kc_email = handle
        employee = EmployeeRow(
            login=handle,
            email=kc_email,
            contact_email=contact,
            display_name=display_name,
            status=EmployeeStatus.INVITED,
            keycloak_sub=None,
        )
        self._session.add(employee)
        await self._session.flush()

        from prodavan.application.relations.commands import RelationsCommand

        await RelationsCommand(self._session).ensure_membership(
            company_id=company_id, employee_id=employee.id, role=role
        )
        await self._session.commit()
        await self._session.refresh(employee)

        await publish_register_command(
            client_ref=f"employee:{employee.id}",
            username=handle,
            email=kc_email,
            password=pwd,
            realm_roles=[ROLE_EMPLOYEE],
            display_name=display_name,
        )
        await self._session.refresh(employee)
        return employee

    async def set_password(self, *, employee_id: str, password: str) -> EmployeeRow:
        pwd = (password or "").strip()
        if len(pwd) < 8:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="password required (min 8 chars)",
            )
        q = await self._session.execute(
            select(EmployeeRow).where(EmployeeRow.id == employee_id)
        )
        emp = q.scalar_one_or_none()
        if emp is None or employee_is_soft_deleted(emp):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")

        login = employee_effective_login(emp)
        kc_email = emp.email
        if emp.keycloak_sub is None:
            await publish_register_command(
                client_ref=f"employee:{emp.id}",
                username=login,
                email=kc_email,
                password=pwd,
                realm_roles=[ROLE_EMPLOYEE],
                display_name=emp.display_name,
            )
            await self._session.refresh(emp)
            return emp

        from prodavan.infrastructure.keycloak.provisioning import get_provisioning

        await get_provisioning().set_company_password(username=login, password=pwd)
        return emp

    async def update_contact_email(
        self,
        *,
        employee_id: str,
        contact_email: str | None,
    ) -> EmployeeRow:
        contact = (contact_email or "").strip().lower() or None
        if contact is not None and "@" not in contact:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="contact_email must be an email when provided",
            )
        q = await self._session.execute(
            select(EmployeeRow).where(EmployeeRow.id == employee_id)
        )
        emp = q.scalar_one_or_none()
        if emp is None or employee_is_soft_deleted(emp):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        emp.contact_email = contact
        await self._session.commit()
        await self._session.refresh(emp)
        return emp

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
        login = employee_effective_login(emp)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.disabled",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "login": login, "email": emp.email, "paused": True},
            )
        await self._session.commit()
        await self._session.refresh(emp)

        if do_publish:
            await publish_disable_command(
                client_ref=f"employee:{emp.id}",
                sub=emp.keycloak_sub,
                username=login,
                email=emp.email,
            )
        return emp

    async def enable_employee(
        self,
        *,
        employee_id: str,
        principal: Principal | None = None,
        publish_auth: bool = True,
    ) -> EmployeeRow:
        """Re-enable paused employee: status=active + Auth enable."""
        from prodavan.application.auth.lifecycle import publish_enable_command
        from prodavan.application.projects.platform_event_service import PlatformEventService

        q = await self._session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        emp = q.scalar_one_or_none()
        if emp is None or employee_is_soft_deleted(emp):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        if emp.status == EmployeeStatus.ACTIVE:
            return emp

        emp.status = EmployeeStatus.ACTIVE
        actor = principal or Principal(sub="system")
        events = PlatformEventService(self._session)
        login = employee_effective_login(emp)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.enabled",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "login": login, "email": emp.email, "paused": False},
            )
        await self._session.commit()
        await self._session.refresh(emp)

        if publish_auth:
            await publish_enable_command(
                client_ref=f"employee:{emp.id}",
                sub=emp.keycloak_sub,
                username=login,
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
        login = employee_effective_login(emp)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.soft_deleted",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "login": login, "email": emp.email},
            )
        await self._session.commit()
        await self._session.refresh(emp)

        if publish_auth:
            if emp.keycloak_sub:
                await publish_delete_command(
                    client_ref=f"employee:{emp.id}",
                    sub=emp.keycloak_sub,
                    username=login,
                    email=emp.email,
                )
            else:
                await publish_disable_command(
                    client_ref=f"employee:{emp.id}",
                    sub=None,
                    username=login,
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
        login = employee_effective_login(emp)
        for membership in emp.memberships:
            await events.emit(
                event_type="employee.restored",
                company_id=membership.company_id,
                principal=actor,
                payload={"employee_id": emp.id, "login": login, "email": emp.email},
            )
        await self._session.commit()
        await self._session.refresh(emp)
        return emp
