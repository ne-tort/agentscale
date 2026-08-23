"""Agent token + USD budget enforcement — company month + per-run (L08)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.domain.admin import CompanyAgentRuntimePolicy
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow, AgentUsageRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


def _month_start_utc() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _token_sum_expr():
    return func.coalesce(AgentUsageRow.input_tokens, 0) + func.coalesce(AgentUsageRow.output_tokens, 0)


class AgentBudgetService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def company_tokens_used_month(self, company_id: str) -> int:
        q = await self._session.execute(
            select(func.coalesce(func.sum(_token_sum_expr()), 0))
            .select_from(AgentUsageRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentUsageRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(
                ProjectRow.company_id == company_id,
                AgentUsageRow.created_at >= _month_start_utc(),
            )
        )
        return int(q.scalar_one() or 0)

    async def company_cost_usd_month(self, company_id: str) -> Decimal:
        q = await self._session.execute(
            select(func.coalesce(func.sum(AgentUsageRow.cost_usd), 0))
            .select_from(AgentUsageRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentUsageRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(
                ProjectRow.company_id == company_id,
                AgentUsageRow.created_at >= _month_start_utc(),
            )
        )
        return Decimal(str(q.scalar_one() or 0))

    async def session_tokens_used(self, session_id: str) -> int:
        q = await self._session.execute(
            select(func.coalesce(func.sum(_token_sum_expr()), 0)).where(
                AgentUsageRow.session_id == session_id
            )
        )
        return int(q.scalar_one() or 0)

    async def enforce_before_turn(
        self,
        *,
        company_id: str,
        session_id: str | None,
        policy: CompanyAgentRuntimePolicy | None = None,
    ) -> None:
        if policy is None:
            policy = await AdminCompanyService(self._session).get_agent_policy(company_id)

        if policy.max_agent_tokens_month is not None:
            used = await self.company_tokens_used_month(company_id)
            if used >= policy.max_agent_tokens_month:
                raise AppError(
                    code="AGENT_BUDGET",
                    title="Agent budget exceeded",
                    status=429,
                    detail="company monthly token limit reached",
                )

        if policy.max_cost_usd_month is not None:
            used_usd = await self.company_cost_usd_month(company_id)
            if used_usd >= policy.max_cost_usd_month:
                raise AppError(
                    code="AGENT_BUDGET",
                    title="Agent budget exceeded",
                    status=429,
                    detail="company monthly USD cost limit reached",
                )

        if session_id is not None and policy.max_tokens_per_run is not None:
            used = await self.session_tokens_used(session_id)
            if used >= policy.max_tokens_per_run:
                raise AppError(
                    code="AGENT_BUDGET",
                    title="Agent budget exceeded",
                    status=429,
                    detail="session per-run token limit reached",
                )
