"""Admin project maintenance hooks (P0 / C-MATERIALIZE)."""

from __future__ import annotations

from fastapi import APIRouter

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.projects.container_ref_backfill import backfill_object_ws_container_refs

router = APIRouter(prefix="/admin/projects", tags=["admin-projects"])


@router.post("/container-refs/backfill-object-ws")
async def backfill_object_ws_refs(
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    """Rewrite remaining ``local-ws:`` container_ref values to ``object-ws:``."""
    return await backfill_object_ws_container_refs(session)
