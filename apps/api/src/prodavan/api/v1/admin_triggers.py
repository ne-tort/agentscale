"""Platform-wide trigger drain (L07/L08)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher

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
