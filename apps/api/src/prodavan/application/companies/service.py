"""Companies BC — org CRUD + soft-delete (Auth via Kafka only)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.auth.register import publish_register_command
from prodavan.application.employees.service import EmployeesCommandService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_COMPANY, ROLE_EMPLOYEE, MembershipRole, Principal
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow


class CompaniesCommandService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._employees = EmployeesCommandService(session)

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
        """DB-only create; Keycloak via ``auth.user.register`` after commit."""
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
            keycloak_sub=None,
        )
        self._session.add(company)
        await self._session.flush()

        email_raw = (admin_email or "").strip()
        employee: EmployeeRow | None = None
        if email_raw:
            if "@" not in email_raw:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="email required",
                )
            employee = await self._employees.upsert_invited(
                email=email_raw.lower(),
                display_name=admin_display_name,
            )
            await self._employees.ensure_membership(
                company_id=company.id,
                employee_id=employee.id,
                role=MembershipRole.COMPANY_ADMIN,
            )

        await self._session.commit()
        await self._session.refresh(company)
        if employee is not None:
            await self._session.refresh(employee)

        await publish_register_command(
            client_ref=f"company:{company.id}",
            username=company.id,
            email=f"{company.id}@companies.prodavan.local",
            password=pwd,
            realm_roles=[ROLE_COMPANY],
            display_name=name.strip(),
        )
        if employee is not None:
            await publish_register_command(
                client_ref=f"employee:{employee.id}",
                username=employee.email,
                email=employee.email,
                password=None,
                realm_roles=[ROLE_EMPLOYEE],
                display_name=admin_display_name,
            )
            await self._session.refresh(employee)
        await self._session.refresh(company)
        return company, employee

    async def soft_delete(self, company_id: str, *, principal: Principal) -> dict:
        """Mark company deleted (UI hide) and publish cascade events. Returns immediately."""
        from prodavan.application.auth.lifecycle import publish_delete_command
        from prodavan.application.projects.platform_event_service import PlatformEventService
        from prodavan.core.jobs.enqueue import enqueue_cascade_company_deleted

        company = await self._session.get(CompanyRow, company_id)
        if company is None or company.deleted_at is not None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")

        name = company.name
        sub = company.keycloak_sub
        company.deleted_at = datetime.now(UTC)

        await PlatformEventService(self._session).emit(
            event_type="company.deleted",
            company_id=company.id,
            principal=principal,
            payload={"company_id": company.id, "name": name, "keycloak_sub": sub},
        )
        await self._session.commit()

        await publish_delete_command(
            client_ref=f"company:{company.id}",
            sub=sub,
            username=company.id,
            email=f"{company.id}@companies.prodavan.local",
        )
        enq = enqueue_cascade_company_deleted(company.id, actor_sub=principal.sub)
        out: dict = {
            "id": company.id,
            "name": name,
            "deleted": True,
            "soft": True,
            "cascade_enqueued": bool(enq.get("enqueued")),
        }
        if not enq.get("enqueued"):
            from prodavan.application.companies.cascade import cascade_company_deleted

            cascade = await cascade_company_deleted(company.id, actor_sub=principal.sub)
            out.update(cascade)
        return out
