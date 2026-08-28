"""Platform-wide trigger drain + idle pause sweep (L07/L08/L09)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
from prodavan.application.project_service import ProjectIdlePauseService
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key

router = APIRouter(prefix="/admin/triggers", tags=["admin-triggers"])


@router.post("/drain")
async def drain_triggers(
    _: PlatformAdminDep,
    session: SessionDep,
    max_projects: int = Query(default=20, ge=1, le=100),
    max_per_project: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Drain queued triggers across active projects (manual / CI substitute for daemon)."""
    await enforce_rate_limit(
        cache_key("rl", "admin", "trigger-drain"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin trigger drain rate limit exceeded",
    )
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
    await enforce_rate_limit(
        cache_key("rl", "admin", "idle-pause-sweep"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin idle-pause sweep rate limit exceeded",
    )
    return await ProjectIdlePauseService(session).sweep_all(principal=admin)
