"""Platform lifecycle events HTTP (L07)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.projects.platform_event_service import PlatformEventService

router = APIRouter(prefix="/admin/platform-events", tags=["admin-platform-events"])


@router.get("")
async def list_platform_events(
    _: PlatformAdminDep,
    session: SessionDep,
    company_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items = await PlatformEventService(session).list_events(
        company_id=company_id,
        project_id=project_id,
        event_type=event_type,
        limit=limit,
    )
    return {"items": items}
