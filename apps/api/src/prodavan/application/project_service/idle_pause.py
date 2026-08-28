"""Idle pause sweep — internal to project_service BC."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.project_service.command import ProjectCommand
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus, project_is_idle
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class ProjectIdlePauseService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._companies = AdminCompanyService(session)
        self._commands = ProjectCommand(session)

    async def _last_activity_at(self, project: ProjectRow) -> datetime | None:
        sess_q = await self._session.execute(
            select(func.max(AgentSessionRow.updated_at)).where(AgentSessionRow.project_id == project.id)
        )
        evt_q = await self._session.execute(
            select(func.max(AgentEventRow.created_at))
            .select_from(AgentEventRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
            .where(AgentSessionRow.project_id == project.id)
        )
        candidates = [sess_q.scalar_one(), evt_q.scalar_one(), project.updated_at, project.created_at]
        times = [t for t in candidates if t is not None]
        return max(times) if times else None

    async def sweep_company(
        self,
        company_id: str,
        *,
        principal: Principal | None = None,
        now: datetime | None = None,
    ) -> dict:
        policy = await self._companies.get_agent_policy(company_id)
        if not policy.idle_pause_enabled():
            return {
                "company_id": company_id,
                "idle_pause_enabled": False,
                "paused": [],
                "checked": 0,
            }
        hours = int(policy.idle_pause_after_hours or 0)
        clock = now or datetime.now(UTC)
        actor = principal or Principal(sub="system")
        q = await self._session.execute(
            select(ProjectRow).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status == ProjectStatus.ACTIVE,
            )
        )
        paused: list[dict] = []
        checked = 0
        for project in q.scalars().all():
            checked += 1
            last = await self._last_activity_at(project)
            if not project_is_idle(
                last_activity_at=last,
                now=clock,
                idle_pause_after_hours=hours,
            ):
                continue
            await self._commands.pause(
                project_id=project.id,
                principal=actor,
                employee=None,
                skip_access=True,
                payload={
                    "reason": "idle_pause",
                    "idle_pause_after_hours": hours,
                    "last_activity_at": last.isoformat() if last else None,
                },
            )
            paused.append(
                {
                    "project_id": project.id,
                    "last_activity_at": last.isoformat() if last else None,
                }
            )
        return {
            "company_id": company_id,
            "idle_pause_enabled": True,
            "idle_pause_after_hours": hours,
            "checked": checked,
            "paused": paused,
            "count": len(paused),
        }

    async def sweep_all(self, *, principal: Principal | None = None) -> dict:
        q = await self._session.execute(select(CompanyRow.id))
        companies: list[dict] = []
        total = 0
        for cid in q.scalars().all():
            result = await self.sweep_company(cid, principal=principal)
            if result.get("idle_pause_enabled"):
                companies.append(result)
                total += int(result.get("count") or 0)
        return {"count": total, "companies": companies}
