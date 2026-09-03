"""Project reads — list/get with visibility filtering."""

from __future__ import annotations

from sqlalchemy import func, select
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
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
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
        created_by_login: str | None = None
        if row.owner_employee_id:
            owner = await self._session.get(EmployeeRow, row.owner_employee_id)
            if owner is not None:
                created_by_login = owner.login
        runtime: dict | None = None
        if include_runtime:
            runtime = await PodQuery(self._session).runtime_view(row.id)
        out = project_public(
            row,
            limits=limits,
            company_subscription=subscription,
            created_by_login=created_by_login,
        )
        if runtime is not None:
            out["runtime"] = runtime
        return out

    async def get_container(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        row = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False, allow_paused=True
        )
        runtime = await PodQuery(self._session).runtime_view(row.id)
        observed_state = runtime.get("observed_state") if runtime else None
        return {
            "project_id": row.id,
            "project_name": row.name,
            "status": row.status,
            "observed_state": observed_state,
            "container_ref": row.container_ref,
            "workspace_key": row.workspace_key,
            "runtime": runtime,
            "runtime_metrics": runtime.get("metrics") if runtime else None,
            "k8s_phase": runtime.get("phase") if runtime else None,
            "last_error": runtime.get("last_error") if runtime else None,
        }

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
        rows = list(q.scalars().all())
        owner_ids = {r.owner_employee_id for r in rows if r.owner_employee_id}
        logins: dict[str, str] = {}
        if owner_ids:
            eq = await self._session.execute(
                select(EmployeeRow.id, EmployeeRow.login).where(EmployeeRow.id.in_(owner_ids))
            )
            logins = {eid: login for eid, login in eq.all()}
        chat_counts: dict[str, int] = {}
        if rows:
            cq = await self._session.execute(
                select(AgentSessionRow.project_id, func.count())
                .where(AgentSessionRow.project_id.in_([r.id for r in rows]))
                .group_by(AgentSessionRow.project_id)
            )
            chat_counts = {pid: int(n) for pid, n in cq.all()}
        items: list[dict] = []
        for row in rows:
            if not await self._access.can_view_project(
                project=row, principal=principal, employee=employee
            ):
                continue
            login = logins.get(row.owner_employee_id) if row.owner_employee_id else None
            runtime: dict | None = None
            if row.status in {
                ProjectStatus.ACTIVE,
                ProjectStatus.ERROR,
                ProjectStatus.PAUSED,
            } and row.container_ref:
                runtime = await PodQuery(self._session).runtime_view(row.id)
            item = project_public(
                row,
                limits=limits,
                company_subscription=subscription,
                created_by_login=login,
            )
            item["chat_count"] = chat_counts.get(row.id, 0)
            if runtime:
                item["observed_state"] = runtime.get("observed_state")
                item["container_last_error"] = runtime.get("last_error")
            items.append(item)
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
