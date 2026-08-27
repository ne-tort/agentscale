"""Async cascade after company soft-delete (Celery / buffer-only inline)."""

from __future__ import annotations

import logging

from sqlalchemy import select

from prodavan.domain.identity import EmployeeStatus, Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)


async def cascade_company_deleted(company_id: str, *, actor_sub: str = "system") -> dict:
    """Disable members, wipe projects/cabinets for a soft-deleted company.

    Company row stays with deleted_at set; Auth KC delete is published separately by REST.
    """
    from prodavan.application.cabinets.instance_service import CabinetInstanceService
    from prodavan.application.employees.service import EmployeesCommandService
    from prodavan.application.projects.pause_runtime import stop_company_runtime
    from prodavan.application.projects.project_service import ProjectService
    from prodavan.infrastructure.persistence.database import get_session_factory
    from prodavan.infrastructure.persistence.models.identity import CompanyRow

    principal = Principal(sub=actor_sub or "system")
    factory = get_session_factory()
    async with factory() as session:
        company = await session.get(CompanyRow, company_id)
        if company is None:
            return {"ok": False, "reason": "not_found", "company_id": company_id}
        if company.deleted_at is None:
            return {"ok": False, "reason": "not_soft_deleted", "company_id": company_id}

        sessions_cancelled = await stop_company_runtime(session, company_id=company_id)
        await session.flush()

        emp_q = await session.execute(
            select(MembershipRow.employee_id).where(MembershipRow.company_id == company_id)
        )
        employee_ids = list({eid for eid in emp_q.scalars().all()})
        employees = EmployeesCommandService(session)
        employees_disabled: list[str] = []
        for employee_id in employee_ids:
            emp = await session.get(EmployeeRow, employee_id)
            if emp is None or emp.status == EmployeeStatus.DISABLED:
                continue
            await employees.disable_employee(
                employee_id=employee_id,
                principal=principal,
                publish_auth_delete=True,
            )
            employees_disabled.append(employee_id)

        proj_q = await session.execute(
            select(ProjectRow.id, ProjectRow.status).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        projects = ProjectService(session)
        projects_deleted: list[str] = []
        for project_id, status in proj_q.all():
            if status == ProjectStatus.ACTIVE:
                await projects.pause(project_id=project_id, principal=principal, employee=None)
            await projects.delete(
                project_id=project_id,
                principal=principal,
                employee=None,
                purge_workspace=True,
            )
            projects_deleted.append(project_id)

        cab_q = await session.execute(
            select(CabinetInstanceRow.id).where(CabinetInstanceRow.company_id == company_id)
        )
        cabinets = CabinetInstanceService(session)
        cabinets_deleted: list[str] = []
        for cabinet_id in cab_q.scalars().all():
            await cabinets.delete_with_cascade(cabinet_id=cabinet_id)
            cabinets_deleted.append(cabinet_id)

        await session.commit()

        from prodavan.application.admin.company_runtime_cache import invalidate_company_runtime_cache

        await invalidate_company_runtime_cache(company_id)

        result = {
            "ok": True,
            "company_id": company_id,
            "soft": True,
            "sessions_cancelled": sessions_cancelled,
            "employees_disabled": employees_disabled,
            "projects_deleted": projects_deleted,
            "cabinets_deleted": cabinets_deleted,
        }
        logger.info("cascade_company_deleted %s", result)
        return result
