"""Admin Project Containers API (P1 read-model + project cascade)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.admin.container_read_service import AdminContainerReadService

router = APIRouter(prefix="/admin/containers", tags=["admin-containers"])


@router.get("")
async def list_containers(
    _: PlatformAdminDep,
    session: SessionDep,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    """List project containers (Project proxy until ProjectContainer ORM)."""
    return await AdminContainerReadService(session).list_containers(limit=limit)


@router.get("/{project_id}")
async def get_container(
    project_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminContainerReadService(session).get_container(project_id)


@router.post("/{project_id}/pause")
async def pause_container(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminContainerReadService(session).pause(project_id=project_id, principal=admin)


@router.post("/{project_id}/resume")
async def resume_container(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminContainerReadService(session).resume(project_id=project_id, principal=admin)


@router.delete("/{project_id}")
async def delete_container(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    """Cascade: soft-delete Project (+ wipe) via ProjectService."""
    return await AdminContainerReadService(session).delete(project_id=project_id, principal=admin)
