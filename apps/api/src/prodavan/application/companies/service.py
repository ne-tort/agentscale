"""Companies BC — org CRUD + soft-delete (Auth via Kafka only)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.auth.register import publish_register_command
from prodavan.application.employees.service import EmployeesCommandService
from prodavan.domain.companies.login import company_effective_login
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
            from prodavan.application.relations.commands import RelationsCommand

            await RelationsCommand(self._session).ensure_membership(
                company_id=company.id,
                employee_id=employee.id,
                role=MembershipRole.COMPANY_ADMIN,
            )

        await self._session.commit()
        await self._session.refresh(company)
        if employee is not None:
            await self._session.refresh(employee)

        login = company_effective_login(company)
        await publish_register_command(
            client_ref=f"company:{company.id}",
            username=login,
            email=f"{login}@companies.prodavan.local",
            password=pwd,
            realm_roles=[ROLE_COMPANY],
            display_name=name.strip(),
        )
        if employee is not None:
            from prodavan.domain.employees.login import employee_effective_login

            await publish_register_command(
                client_ref=f"employee:{employee.id}",
                username=employee_effective_login(employee),
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
            username=company_effective_login(company),
            email=f"{company_effective_login(company)}@companies.prodavan.local",
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

    async def restore(self, company_id: str, *, principal: Principal) -> dict:
        """Restore soft-deleted company → paused. Children stay soft-deleted."""
        from prodavan.application.auth.register import publish_register_command
        from prodavan.application.projects.platform_event_service import PlatformEventService
        from prodavan.domain.identity import ROLE_COMPANY

        company = await self._session.get(CompanyRow, company_id)
        if company is None or company.deleted_at is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="company is not soft-deleted",
            )
        company.deleted_at = None
        company.status = "paused"
        await PlatformEventService(self._session).emit(
            event_type="company.restored",
            company_id=company.id,
            principal=principal,
            payload={"company_id": company.id, "status": "paused"},
        )
        await self._session.commit()
        await self._session.refresh(company)
        if company.keycloak_sub is None:
            login = company_effective_login(company)
            await publish_register_command(
                client_ref=f"company:{company.id}",
                username=login,
                email=f"{login}@companies.prodavan.local",
                password=None,
                realm_roles=[ROLE_COMPANY],
                display_name=company.name,
            )
        return {
            "id": company.id,
            "name": company.name,
            "restored": True,
            "status": company.status,
            "soft": False,
        }

    async def purge(self, company_id: str, *, principal: Principal) -> dict:
        """Hard-purge company after soft-delete: require children already soft/purged, then wipe."""
        from sqlalchemy import select

        from prodavan.application.cabinets.instance_service import CabinetInstanceService
        from prodavan.application.project_service import ProjectCommand, ProjectQuery
        from prodavan.application.projects.platform_event_service import PlatformEventService
        from prodavan.domain.projects import ProjectStatus
        from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow

        company = await self._session.get(CompanyRow, company_id)
        if company is None or company.deleted_at is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="soft-delete the company before purge",
            )

        query = ProjectQuery(self._session)
        if await query.list_ids(
            company_id=company_id, exclude_status=ProjectStatus.DELETED
        ):
            raise AppError(
                code="CASCADE_INCOMPLETE",
                title="Cascade incomplete",
                status=409,
                detail="live projects remain; wait for soft-cascade or soft-delete them",
            )

        projects = ProjectCommand(self._session)
        projects_purged: list[str] = []
        for project_id in await query.list_ids(
            company_id=company_id, status=ProjectStatus.DELETED
        ):
            await projects.purge(project_id=project_id, principal=principal, employee=None)
            projects_purged.append(project_id)

        cabinets = CabinetInstanceService(self._session)
        cab_q = await self._session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.company_id == company_id,
            )
        )
        cabinets_purged: list[str] = []
        for cabinet_id in cab_q.scalars().all():
            await cabinets.delete_with_cascade(cabinet_id=cabinet_id)
            cabinets_purged.append(cabinet_id)

        await PlatformEventService(self._session).emit(
            event_type="company.purged",
            company_id=company.id,
            principal=principal,
            payload={
                "company_id": company.id,
                "projects_purged": projects_purged,
                "cabinets_purged": cabinets_purged,
            },
        )
        # Keep tombstone row for audit (deleted_at stays); mark status purged.
        company.status = "purged"
        await self._session.commit()
        return {
            "id": company.id,
            "purged": True,
            "projects_purged": projects_purged,
            "cabinets_purged": cabinets_purged,
        }
