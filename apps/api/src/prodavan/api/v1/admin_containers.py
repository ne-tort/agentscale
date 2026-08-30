"""Admin Project Containers API (P1 read-model + project cascade)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.v1.workspace_bodies import WorkspaceCopyBody, WorkspaceMoveBody
from prodavan.application.admin.container_read_service import AdminContainerReadService
from prodavan.application.pod_service import PodCommand, PodReconcileService
from prodavan.application.pod_service.workspace_service import PodWorkspaceService

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


@router.post("/{project_id}/reload")
async def reload_container(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminContainerReadService(session).reload(project_id=project_id, principal=admin)


@router.get("/{project_id}/workspace/entries")
async def list_admin_workspace_entries(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
    path: str = Query(default=""),
) -> dict:
    await AdminContainerReadService(session).get_container(project_id)
    return await PodWorkspaceService(session).list_entries(
        project_id=project_id,
        path=path,
        principal=admin,
        employee=None,
    )


@router.get("/{project_id}/workspace/content")
async def download_admin_workspace_content(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
    path: str = Query(min_length=1),
) -> Response:
    await AdminContainerReadService(session).get_container(project_id)
    data, entry = await PodWorkspaceService(session).read_content(
        project_id=project_id,
        path=path,
        principal=admin,
        employee=None,
    )
    safe_name = entry.name.replace('"', "")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/{project_id}/workspace/preview")
async def preview_admin_workspace_text(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
    path: str = Query(min_length=1),
) -> dict:
    await AdminContainerReadService(session).get_container(project_id)
    return await PodWorkspaceService(session).preview_text(
        project_id=project_id,
        path=path,
        principal=admin,
        employee=None,
    )


@router.delete("/{project_id}/workspace/entries")
async def delete_admin_workspace_entry(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
    path: str = Query(min_length=1),
) -> dict:
    await AdminContainerReadService(session).get_container(project_id)
    return await PodWorkspaceService(session).delete_entry(
        project_id=project_id,
        path=path,
        principal=admin,
        employee=None,
    )


@router.post("/{project_id}/workspace/move")
async def move_admin_workspace_entry(
    project_id: str,
    body: WorkspaceMoveBody,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    await AdminContainerReadService(session).get_container(project_id)
    return await PodWorkspaceService(session).move_entry(
        project_id=project_id,
        src=body.src,
        dst=body.dst,
        principal=admin,
        employee=None,
    )


@router.post("/{project_id}/workspace/copy")
async def copy_admin_workspace_entry(
    project_id: str,
    body: WorkspaceCopyBody,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    await AdminContainerReadService(session).get_container(project_id)
    return await PodWorkspaceService(session).copy_entry(
        project_id=project_id,
        src=body.src,
        dst=body.dst,
        principal=admin,
        employee=None,
    )


@router.delete("/{project_id}")
async def delete_container(
    project_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    """Cascade: soft-delete Project (+ wipe) via ProjectCommand."""
    return await AdminContainerReadService(session).delete(project_id=project_id, principal=admin)


@router.post("/pods/{pod_id}/force-kill")
async def force_kill_pod(
    pod_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await PodCommand(session).force_kill(pod_id=pod_id, principal=admin)


@router.post("/reconcile")
async def reconcile_pods(
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    _ = admin
    return await PodReconcileService(session).run()
