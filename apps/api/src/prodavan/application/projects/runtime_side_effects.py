"""Apply project runtime side-effects from domain operation matrix."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.projects.runtime_ops import (
    ProjectRuntimeOp,
    SessionRuntimeAction,
    effect_for_op,
    effect_for_stop,
    op_for_reason,
)

logger = logging.getLogger(__name__)


def op_from_reason(reason: str | None) -> ProjectRuntimeOp:
    return op_for_reason(reason)


def should_bootstrap_after(op: ProjectRuntimeOp) -> bool:
    return effect_for_op(op).bootstrap_sessions


async def apply_session_action(
    session: AsyncSession,
    *,
    project_id: str,
    op: ProjectRuntimeOp,
    purge_workspace: bool = False,
) -> int:
    """Apply session lifecycle action for a stop/runtime op. Caller owns commit."""
    if op in {ProjectRuntimeOp.RESUME, ProjectRuntimeOp.RELOAD, ProjectRuntimeOp.SYNC, ProjectRuntimeOp.LAUNCH}:
        effect = effect_for_op(op)
    else:
        effect = effect_for_stop(op, purge_workspace=purge_workspace)

    from prodavan.application.agent.session_service import AgentSessionService

    svc = AgentSessionService(session)
    if effect.session_action == SessionRuntimeAction.SUSPEND:
        return await svc.suspend_active_for_project(project_id=project_id)
    if effect.session_action == SessionRuntimeAction.CANCEL:
        return await svc.cancel_resumable_for_project(project_id=project_id)
    if effect.session_action == SessionRuntimeAction.REACTIVATE:
        return len(await svc.reactivate_resumable_for_project(project_id=project_id))
    return 0


async def bootstrap_project_sessions(
    session: AsyncSession,
    *,
    project_id: str,
    reactivate: bool = True,
) -> dict:
    from prodavan.application.agent.pod_session_bootstrap import PodSessionBootstrap

    return await PodSessionBootstrap(session).bootstrap_project_sessions(
        project_id=project_id,
        reactivate=reactivate,
    )


def schedule_bootstrap_background(*, project_id: str, op: ProjectRuntimeOp) -> None:
    """Unified background entry for post-running pod session bootstrap."""
    if not should_bootstrap_after(op):
        return
    asyncio.create_task(
        _bootstrap_background(project_id=project_id),
        name=f"session-bootstrap-{project_id}",
    )


async def _bootstrap_background(*, project_id: str) -> None:
    from prodavan.infrastructure.persistence.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        try:
            out = await bootstrap_project_sessions(session, project_id=project_id)
            await session.commit()
            logger.info("session bootstrap done project_id=%s out=%s", project_id, out)
        except Exception:
            logger.exception("session bootstrap failed project_id=%s", project_id)
