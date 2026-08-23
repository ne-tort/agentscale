"""Platform-wide trigger drain + idle pause sweep (L07/L08/L09)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
from prodavan.application.projects.idle_pause_service import IdlePauseService

router = APIRouter(prefix="/admin/triggers", tags=["admin-triggers"])


@router.post("/drain")
async def drain_triggers(
    _: PlatformAdminDep,
    session: SessionDep,
    max_projects: int = Query(default=20, ge=1, le=100),
    max_per_project: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Drain queued triggers across active projects (manual / CI substitute for daemon)."""
    return await AgentTriggerDispatcher(session).drain_all(
        max_projects=max_projects,
        max_per_project=max_per_project,
    )


@router.post("/idle-pause/sweep")
async def sweep_idle_pause(
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    """Pause active projects idle longer than company idle_pause_after_hours (default off)."""
    return await IdlePauseService(session).sweep_all(principal=admin)
