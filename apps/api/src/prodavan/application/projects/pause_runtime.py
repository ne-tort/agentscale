"""Shared runtime stop side-effects (L07/L08).

Project pause suspends agent sessions (recoverable). Delete with workspace wipe / purge cancels them.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.projects.runtime_ops import op_for_reason


async def stop_project_runtime(
    session: AsyncSession,
    *,
    project_id: str,
    reason: str | None = None,
    purge_workspace: bool = False,
) -> int:
    """Apply session side-effect for project pod teardown. Caller owns commit."""
    from prodavan.application.projects.runtime_side_effects import apply_session_action

    op = op_for_reason(reason)
    return await apply_session_action(
        session,
        project_id=project_id,
        op=op,
        purge_workspace=purge_workspace,
    )


async def stop_company_runtime(session: AsyncSession, *, company_id: str) -> int:
    """Cancel ACTIVE agent sessions for all projects of a suspended company."""
    from prodavan.application.agent.session_service import AgentSessionService

    return await AgentSessionService(session).cancel_active_for_company(company_id=company_id)
