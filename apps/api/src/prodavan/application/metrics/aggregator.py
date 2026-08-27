"""Company-level SQL metrics aggregates (PG) — extracted from AdminCompanyService."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.config.settings import settings
from prodavan.domain.admin import subscription_read_model
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import EmployeeStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow, AgentUsageRow
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class CompanyMetricsAggregator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._quotas = CompanyQuotaService(session)

    async def _require_company(self, company_id: str) -> CompanyRow:
        row = await self._session.get(CompanyRow, company_id)
        if row is None or row.deleted_at is not None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        return row

    async def _count_running_cabinets(self, company_id: str) -> int:
        q = await self._session.execute(
            select(func.count(func.distinct(ProjectRow.cabinet_id)))
            .select_from(ProjectRow)
            .join(CabinetInstanceRow, CabinetInstanceRow.id == ProjectRow.cabinet_id)
            .where(
                ProjectRow.company_id == company_id,
                ProjectRow.status == ProjectStatus.ACTIVE,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        return int(q.scalar_one() or 0)

    async def _last_activity_at(self, company_id: str) -> datetime | None:
        sess_q = await self._session.execute(
            select(func.max(AgentSessionRow.updated_at))
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
        )
        proj_q = await self._session.execute(
            select(func.max(ProjectRow.updated_at)).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        evt_q = await self._session.execute(
            select(func.max(AgentEventRow.created_at))
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
        )
        candidates = [sess_q.scalar_one(), proj_q.scalar_one(), evt_q.scalar_one()]
        times = [t for t in candidates if t is not None]
        return max(times) if times else None

    async def _storage_bytes(self, company_id: str) -> int:
        from prodavan.application.admin.storage_metrics import company_blob_storage_bytes

        keys_q = await self._session.execute(
            select(ProjectRow.workspace_key).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        cabinet_q = await self._session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        return company_blob_storage_bytes(
            workspace_keys=list(keys_q.scalars().all()),
            cabinet_ids=list(cabinet_q.scalars().all()),
        )

    async def membership_employee_ids(self, company_id: str) -> list[str]:
        q = await self._session.execute(
            select(func.distinct(MembershipRow.employee_id)).where(
                MembershipRow.company_id == company_id
            )
        )
        return [str(eid) for eid in q.scalars().all() if eid]

    async def aggregate(self, company_id: str) -> dict:
        company = await self._require_company(company_id)
        emp_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id))).where(
                MembershipRow.company_id == company_id
            )
        )
        emp_active_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id)))
            .select_from(MembershipRow)
            .join(EmployeeRow, EmployeeRow.id == MembershipRow.employee_id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.status != EmployeeStatus.DISABLED,
            )
        )
        cab_q = await self._session.execute(
            select(func.count())
            .select_from(CabinetInstanceRow)
            .where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        proj_q = await self._session.execute(
            select(func.count())
            .select_from(ProjectRow)
            .where(ProjectRow.company_id == company_id, ProjectRow.status != ProjectStatus.DELETED)
        )
        usage_q = await self._session.execute(
            select(
                func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
                func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
            )
            .select_from(AgentUsageRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentUsageRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
        )
        usage_row = usage_q.one()
        input_tok = int(usage_row[0] or 0)
        output_tok = int(usage_row[1] or 0)
        msg_q = await self._session.execute(
            select(func.count())
            .select_from(AgentEventRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(
                ProjectRow.company_id == company_id,
                AgentEventRow.event_type == "text_delta",
            )
        )
        quota = await self._quotas.get_quota(company_id)
        active_cabinets = int(cab_q.scalar_one() or 0)
        running_cabinets = await self._count_running_cabinets(company_id)
        employees_total = int(emp_q.scalar_one() or 0)
        employees_active = int(emp_active_q.scalar_one() or 0)
        projects_total = int(proj_q.scalar_one() or 0)
        agent_messages = int(msg_q.scalar_one() or 0)
        key_metrics = await AiKeysService(self._session).company_key_metrics(company_id)
        last_activity = await self._last_activity_at(company_id)
        storage_bytes = await self._storage_bytes(company_id)
        tokens_used = input_tok + output_tok
        threshold = settings.admin_metrics_token_alert_threshold
        high_usage = threshold > 0 and tokens_used >= threshold
        sub = subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=datetime.now(UTC),
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )
        unbound_emp_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id)))
            .select_from(MembershipRow)
            .join(EmployeeRow, EmployeeRow.id == MembershipRow.employee_id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.keycloak_sub.is_(None),
                EmployeeRow.status != EmployeeStatus.DISABLED,
                EmployeeRow.deleted_at.is_(None),
            )
        )
        employees_keycloak_unbound = int(unbound_emp_q.scalar_one() or 0)
        return {
            "employees_total": employees_total,
            "employees_active": employees_active,
            "employees": employees_total,
            "active_cabinets": active_cabinets,
            "cabinets_active": active_cabinets,
            "running_cabinets": running_cabinets,
            "cabinets_quota": quota.max_cabinets,
            "cabinets_quota_used_pct": round(100 * active_cabinets / quota.max_cabinets, 1)
            if quota.max_cabinets
            else 0,
            "projects_total": projects_total,
            "agent_tokens_used": tokens_used,
            "agent_input_tokens": input_tok,
            "agent_output_tokens": output_tok,
            "agent_messages": agent_messages,
            "last_activity_at": last_activity.isoformat() if last_activity else None,
            "storage_bytes": storage_bytes,
            "high_agent_usage": high_usage,
            "keycloak_unbound": company.keycloak_sub is None,
            "employees_keycloak_unbound": employees_keycloak_unbound,
            **key_metrics,
            **sub,
        }
