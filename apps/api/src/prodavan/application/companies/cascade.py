"""Async cascade after company soft-delete — soft children only (no wipe)."""

from __future__ import annotations

import logging

from sqlalchemy import select

from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.identity import Principal
from prodavan.domain.lifecycle import employee_is_soft_deleted
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow, MembershipRow

logger = logging.getLogger(__name__)


async def cascade_company_deleted(company_id: str, *, actor_sub: str = "system") -> dict:
    """Soft-delete members / projects / cabinets for a soft-deleted company.

    No MinIO wipe and no DROP schema — that is hard-purge only.
    Company row stays with deleted_at set; Auth KC delete is published by REST.
    """
    from prodavan.application.employees.service import EmployeesCommandService
    from prodavan.application.project_service import ProjectCommand, ProjectQuery
    from prodavan.application.projects.pause_runtime import stop_company_runtime
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
        employees_soft_deleted: list[str] = []
        for employee_id in employee_ids:
            emp = await session.get(EmployeeRow, employee_id)
            if emp is None or employee_is_soft_deleted(emp):
                continue
            await employees.soft_delete(
                employee_id=employee_id,
                principal=principal,
                publish_auth=True,
            )
            employees_soft_deleted.append(employee_id)

        projects = ProjectCommand(session)
        query = ProjectQuery(session)
        projects_soft_deleted: list[str] = []
        for project_id in await query.list_ids(
            company_id=company_id, exclude_status=ProjectStatus.DELETED
        ):
            await projects.delete(
                project_id=project_id,
                principal=principal,
                employee=None,
                purge_workspace=False,
                skip_access=True,
            )
            projects_soft_deleted.append(project_id)

        cab_q = await session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status != CabinetStatus.DELETED,
            )
        )
        cabinets_soft_deleted: list[str] = []
        for cabinet_id in cab_q.scalars().all():
            # Projects already soft-deleted above; set cabinet tombstone only.
            cab = await session.get(CabinetInstanceRow, cabinet_id)
            if cab is None or cab.status == CabinetStatus.DELETED:
                continue
            cab.status = CabinetStatus.DELETED
            cabinets_soft_deleted.append(cabinet_id)

        await session.commit()

        from prodavan.application.admin.company_runtime_cache import invalidate_company_runtime_cache

        await invalidate_company_runtime_cache(company_id)

        result = {
            "ok": True,
            "company_id": company_id,
            "soft": True,
            "sessions_cancelled": sessions_cancelled,
            "employees_disabled": employees_soft_deleted,
            "employees_soft_deleted": employees_soft_deleted,
            "projects_deleted": projects_soft_deleted,
            "projects_soft_deleted": projects_soft_deleted,
            "cabinets_deleted": cabinets_soft_deleted,
            "cabinets_soft_deleted": cabinets_soft_deleted,
        }
        logger.info("cascade_company_deleted %s", result)
        return result
