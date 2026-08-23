"""Shared runtime stop side-effects (L07/L08).

Project pause and company subscription suspend must stop the same agent surface.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def stop_project_runtime(session: AsyncSession, *, project_id: str) -> int:
    """Cancel ACTIVE agent sessions for a project about to be / just marked paused.

    Returns number of sessions cancelled. Does not commit — caller owns the transaction.
    Lazy-imports AgentSessionService to avoid projects↔agent package cycles.
    """
    from prodavan.application.agent.session_service import AgentSessionService

    return await AgentSessionService(session).cancel_active_for_project(project_id=project_id)


async def stop_company_runtime(session: AsyncSession, *, company_id: str) -> int:
    """Cancel ACTIVE agent sessions for all projects of a suspended company."""
    from prodavan.application.agent.session_service import AgentSessionService

    return await AgentSessionService(session).cancel_active_for_company(company_id=company_id)
