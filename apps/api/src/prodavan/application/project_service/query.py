"""Project reads — list/get with visibility filtering."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.pod_service.query import PodQuery
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.application.project_service.public import project_public
from prodavan.domain.admin import attachment_max_bytes
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class ProjectQuery:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessPolicy(session)
        self._cabinets = CabinetAccessService(session)
        self._companies = AdminCompanyService(session)
        self._subscription = CompanySubscriptionGate(session)

    async def _attachment_limits(self, company_id: str) -> dict:
        policy = await self._companies.get_agent_policy(company_id)
        return {"attachment_max_bytes": attachment_max_bytes(policy)}

    async def _project_public(self, row: ProjectRow, *, include_runtime: bool = False) -> dict:
        limits = await self._attachment_limits(row.company_id)
        subscription = await self._subscription.subscription_state(row.company_id)
        out = project_public(row, limits=limits, company_subscription=subscription)
        if include_runtime:
            out["runtime"] = await PodQuery(self._session).runtime_summary(row.id)
        return out

    async def get(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        return await self._project_public(row, include_runtime=True)

    async def list_for_cabinet(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._cabinets.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        limits = await self._attachment_limits(inst.company_id)
        subscription = await self._subscription.subscription_state(inst.company_id)
        q = await self._session.execute(
            select(ProjectRow)
            .where(ProjectRow.cabinet_id == cabinet_id, ProjectRow.status != ProjectStatus.DELETED)
            .order_by(ProjectRow.created_at.desc())
        )
        items: list[dict] = []
        for row in q.scalars().all():
            if not await self._access.can_view_project(
                project=row, principal=principal, employee=employee
            ):
                continue
            items.append(project_public(row, limits=limits, company_subscription=subscription))
        return items

    async def list_ids(
        self,
        *,
        cabinet_id: str | None = None,
        company_id: str | None = None,
        status: str | None = None,
        exclude_status: str | None = None,
    ) -> list[str]:
        stmt = select(ProjectRow.id)
        if cabinet_id is not None:
            stmt = stmt.where(ProjectRow.cabinet_id == cabinet_id)
        if company_id is not None:
            stmt = stmt.where(ProjectRow.company_id == company_id)
        if status is not None:
            stmt = stmt.where(ProjectRow.status == status)
        if exclude_status is not None:
            stmt = stmt.where(ProjectRow.status != exclude_status)
        q = await self._session.execute(stmt)
        return list(q.scalars().all())

    async def list_workspace_refs_for_cabinet(self, cabinet_id: str) -> list[dict]:
        q = await self._session.execute(
            select(ProjectRow.id, ProjectRow.workspace_key).where(ProjectRow.cabinet_id == cabinet_id)
        )
        return [{"project_id": pid, "workspace_key": wk} for pid, wk in q.all()]
