"""Admin recycle bin HTTP — soft-deleted entities restore / purge."""

from __future__ import annotations

from fastapi import APIRouter

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.admin.recycle_service import RecycleService

router = APIRouter(prefix="/admin/recycle", tags=["admin-recycle"])


@router.get("")
async def list_recycle(admin: PlatformAdminDep, session: SessionDep) -> dict:
    return await RecycleService(session).list_all()


@router.post("/{entity}/{entity_id}/restore")
async def restore_entity(
    entity: str, entity_id: str, admin: PlatformAdminDep, session: SessionDep
) -> dict:
    return await RecycleService(session).restore(
        entity=entity, entity_id=entity_id, principal=admin
    )


@router.delete("/{entity}/{entity_id}/purge")
async def purge_entity(
    entity: str, entity_id: str, admin: PlatformAdminDep, session: SessionDep
) -> dict:
    return await RecycleService(session).purge(
        entity=entity, entity_id=entity_id, principal=admin
    )
