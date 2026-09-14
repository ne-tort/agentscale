"""Rebuild Metrics BC counters from PG / object store (no text_delta pollution)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.adapters.redis_counter_store import build_counter_store
from prodavan.application.metrics.storage_sampler import StorageMetricsSampler
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.cabinets.types import CabinetAssignmentStatus
from prodavan.domain.metrics.types import (
    ENTITY_CABINET,
    ENTITY_COMPANY,
    ENTITY_EMPLOYEE,
    ENTITY_PROJECT,
    ENTITY_SESSION,
    METRIC_AGENT_REQUESTS,
    METRIC_AGENT_TOKENS,
    METRIC_CABINETS_TOTAL,
    METRIC_EMPLOYEES_TOTAL,
    METRIC_PROJECTS_TOTAL,
)
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow, AgentUsageRow
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)


class MetricsBackfillService:
    """One-shot baseline rebuild for overview counters."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = build_counter_store()

    async def rebuild_all(self, *, storage: bool = True) -> dict[str, Any]:
        companies = 0
        cabinets = 0
        projects = 0
        sessions = 0
        employees = 0

        co_rows = (
            await self._session.execute(select(CompanyRow).where(CompanyRow.deleted_at.is_(None)))
        ).scalars().all()
        for co in co_rows:
            await self._rebuild_company(co.id)
            companies += 1

        cab_rows = (
            await self._session.execute(
                select(CabinetInstanceRow).where(CabinetInstanceRow.status == CabinetStatus.ACTIVE)
            )
        ).scalars().all()
        for cab in cab_rows:
            await self._rebuild_cabinet(cab.id, company_id=cab.company_id)
            cabinets += 1

        proj_rows = (
            await self._session.execute(
                select(ProjectRow).where(ProjectRow.status != ProjectStatus.DELETED)
            )
        ).scalars().all()
        for proj in proj_rows:
            await self._rebuild_project(proj)
            projects += 1

        sess_ids = (
            await self._session.execute(select(AgentSessionRow.id))
        ).scalars().all()
        for sid in sess_ids:
            await self._rebuild_session(str(sid))
            sessions += 1

        emp_ids = (
            await self._session.execute(
                select(func.distinct(AgentUsageRow.employee_id)).where(
                    AgentUsageRow.employee_id.is_not(None)
                )
            )
        ).scalars().all()
        for eid in emp_ids:
            if eid:
                await self._rebuild_employee(str(eid))
                employees += 1

        storage_stats: dict[str, Any] = {}
        if storage:
            storage_stats = await StorageMetricsSampler(self._session).sample_all(apply_local=True)

        return {
            "companies": companies,
            "cabinets": cabinets,
            "projects": projects,
            "sessions": sessions,
            "employees": employees,
            "storage": storage_stats,
        }

    async def _rebuild_company(self, company_id: str) -> None:
        emp_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id))).where(
                MembershipRow.company_id == company_id
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
        await self._store.set_counter(
            ENTITY_COMPANY, company_id, METRIC_EMPLOYEES_TOTAL, int(emp_q.scalar_one() or 0)
        )
        await self._store.set_counter(
            ENTITY_COMPANY, company_id, METRIC_CABINETS_TOTAL, int(cab_q.scalar_one() or 0)
        )
        await self._store.set_counter(
            ENTITY_COMPANY, company_id, METRIC_PROJECTS_TOTAL, int(proj_q.scalar_one() or 0)
        )

        emp_ids = (
            await self._session.execute(
                select(func.distinct(MembershipRow.employee_id)).where(
                    MembershipRow.company_id == company_id
                )
            )
        ).scalars().all()
        for eid in emp_ids:
            if eid:
                await self._store.put_link(
                    parent_type=ENTITY_COMPANY,
                    parent_id=company_id,
                    child_type=ENTITY_EMPLOYEE,
                    child_id=str(eid),
                )

        cab_ids = (
            await self._session.execute(
                select(CabinetInstanceRow.id).where(
                    CabinetInstanceRow.company_id == company_id,
                    CabinetInstanceRow.status == CabinetStatus.ACTIVE,
                )
            )
        ).scalars().all()
        for cid in cab_ids:
            await self._store.put_link(
                parent_type=ENTITY_COMPANY,
                parent_id=company_id,
                child_type=ENTITY_CABINET,
                child_id=str(cid),
            )

        req, tokens = await self._usage_for_scope(company_id=company_id)
        await self._store.set_counter(ENTITY_COMPANY, company_id, METRIC_AGENT_REQUESTS, req)
        await self._store.set_counter(ENTITY_COMPANY, company_id, METRIC_AGENT_TOKENS, tokens)

    async def _rebuild_cabinet(self, cabinet_id: str, *, company_id: str | None) -> None:
        emp_q = await self._session.execute(
            select(func.count(func.distinct(CabinetEmployeeAssignmentRow.employee_id))).where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
            )
        )
        proj_q = await self._session.execute(
            select(func.count())
            .select_from(ProjectRow)
            .where(
                ProjectRow.cabinet_id == cabinet_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        await self._store.set_counter(
            ENTITY_CABINET, cabinet_id, METRIC_EMPLOYEES_TOTAL, int(emp_q.scalar_one() or 0)
        )
        await self._store.set_counter(
            ENTITY_CABINET, cabinet_id, METRIC_PROJECTS_TOTAL, int(proj_q.scalar_one() or 0)
        )
        if company_id:
            await self._store.put_link(
                parent_type=ENTITY_COMPANY,
                parent_id=company_id,
                child_type=ENTITY_CABINET,
                child_id=cabinet_id,
            )

        emp_ids = (
            await self._session.execute(
                select(func.distinct(CabinetEmployeeAssignmentRow.employee_id)).where(
                    CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                    CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
                )
            )
        ).scalars().all()
        for eid in emp_ids:
            if eid:
                await self._store.put_link(
                    parent_type=ENTITY_CABINET,
                    parent_id=cabinet_id,
                    child_type=ENTITY_EMPLOYEE,
                    child_id=str(eid),
                )

        proj_ids = (
            await self._session.execute(
                select(ProjectRow.id).where(
                    ProjectRow.cabinet_id == cabinet_id,
                    ProjectRow.status != ProjectStatus.DELETED,
                )
            )
        ).scalars().all()
        for pid in proj_ids:
            await self._store.put_link(
                parent_type=ENTITY_CABINET,
                parent_id=cabinet_id,
                child_type=ENTITY_PROJECT,
                child_id=str(pid),
            )

        req, tokens = await self._usage_for_scope(cabinet_id=cabinet_id)
        await self._store.set_counter(ENTITY_CABINET, cabinet_id, METRIC_AGENT_REQUESTS, req)
        await self._store.set_counter(ENTITY_CABINET, cabinet_id, METRIC_AGENT_TOKENS, tokens)

    async def _rebuild_project(self, row: ProjectRow) -> None:
        if row.cabinet_id:
            await self._store.put_link(
                parent_type=ENTITY_CABINET,
                parent_id=row.cabinet_id,
                child_type=ENTITY_PROJECT,
                child_id=row.id,
            )
        req, tokens = await self._usage_for_scope(project_id=row.id)
        await self._store.set_counter(ENTITY_PROJECT, row.id, METRIC_AGENT_REQUESTS, req)
        await self._store.set_counter(ENTITY_PROJECT, row.id, METRIC_AGENT_TOKENS, tokens)

    async def _rebuild_session(self, session_id: str) -> None:
        msg_q = await self._session.execute(
            select(func.count())
            .select_from(AgentEventRow)
            .where(
                AgentEventRow.session_id == session_id,
                AgentEventRow.event_type == "user_message",
            )
        )
        usage_q = await self._session.execute(
            select(
                func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
                func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
            ).where(AgentUsageRow.session_id == session_id)
        )
        usage_row = usage_q.one()
        tokens = int(usage_row[0] or 0) + int(usage_row[1] or 0)
        req = int(msg_q.scalar_one() or 0)
        await self._store.set_counter(ENTITY_SESSION, session_id, METRIC_AGENT_REQUESTS, req)
        await self._store.set_counter(ENTITY_SESSION, session_id, METRIC_AGENT_TOKENS, tokens)

    async def _rebuild_employee(self, employee_id: str) -> None:
        usage_q = await self._session.execute(
            select(
                func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
                func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
            ).where(AgentUsageRow.employee_id == employee_id)
        )
        usage_row = usage_q.one()
        tokens = int(usage_row[0] or 0) + int(usage_row[1] or 0)
        msg_q = await self._session.execute(
            select(func.count())
            .select_from(AgentEventRow)
            .where(
                AgentEventRow.event_type == "user_message",
                AgentEventRow.payload["employee_id"].as_string() == employee_id,
            )
        )
        req = int(msg_q.scalar_one() or 0)
        await self._store.set_counter(ENTITY_EMPLOYEE, employee_id, METRIC_AGENT_REQUESTS, req)
        await self._store.set_counter(ENTITY_EMPLOYEE, employee_id, METRIC_AGENT_TOKENS, tokens)

    async def _usage_for_scope(
        self,
        *,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
    ) -> tuple[int, int]:
        """agent_requests = user_message count (not text_delta); tokens from AgentUsageRow."""
        msg_stmt = (
            select(func.count())
            .select_from(AgentEventRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
        )
        usage_stmt = (
            select(
                func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
                func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
            )
            .select_from(AgentUsageRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentUsageRow.session_id)
        )

        if project_id:
            msg_stmt = msg_stmt.where(
                AgentSessionRow.project_id == project_id,
                AgentEventRow.event_type == "user_message",
            )
            usage_stmt = usage_stmt.where(AgentSessionRow.project_id == project_id)
        else:
            msg_stmt = msg_stmt.join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            usage_stmt = usage_stmt.join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            if cabinet_id:
                msg_stmt = msg_stmt.where(
                    ProjectRow.cabinet_id == cabinet_id,
                    AgentEventRow.event_type == "user_message",
                )
                usage_stmt = usage_stmt.where(ProjectRow.cabinet_id == cabinet_id)
            elif company_id:
                msg_stmt = msg_stmt.where(
                    ProjectRow.company_id == company_id,
                    AgentEventRow.event_type == "user_message",
                )
                usage_stmt = usage_stmt.where(ProjectRow.company_id == company_id)
            else:
                return 0, 0

        req = int((await self._session.execute(msg_stmt)).scalar_one() or 0)
        usage_row = (await self._session.execute(usage_stmt)).one()
        tokens = int(usage_row[0] or 0) + int(usage_row[1] or 0)
        return req, tokens
