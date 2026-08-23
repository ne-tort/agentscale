"""Shared pause side-effects for project lifecycle (L07).

Manual pause and idle sweep must stop the same runtime surface.
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
