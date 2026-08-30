"""Company-scoped Project Containers API (employees' projects only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.api.v1.workspace_bodies import WorkspaceCopyBody, WorkspaceMoveBody
from prodavan.application.admin.container_read_service import AdminContainerReadService
from prodavan.application.identity.service import EntitlementService
from prodavan.application.pod_service.workspace_service import PodWorkspaceService
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/companies/{company_id}/containers", tags=["company-containers"])


@router.get("")
async def list_company_containers(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).list_containers_for_company(company_id, limit=limit)


@router.get("/{project_id}/metrics")
async def get_company_container_metrics(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    window: str = Query(default="1h"),
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    from prodavan.application.metrics.query import MetricsQuery

    return await MetricsQuery(session).get_project_metrics(project_id, window=window)


@router.get("/{project_id}")
async def get_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).get_container_for_company(company_id, project_id)


@router.post("/{project_id}/pause")
async def pause_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).pause_for_company(
        company_id=company_id, project_id=project_id, principal=principal, employee=employee
    )


@router.post("/{project_id}/resume")
async def resume_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).resume_for_company(
        company_id=company_id, project_id=project_id, principal=principal, employee=employee
    )


@router.post("/{project_id}/reload")
async def reload_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).reload_for_company(
        company_id=company_id, project_id=project_id, principal=principal, employee=employee
    )


@router.get("/{project_id}/workspace/entries")
async def list_company_workspace_entries(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    path: str = Query(default=""),
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    return await PodWorkspaceService(session).list_entries(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )


@router.get("/{project_id}/workspace/content")
async def download_company_workspace_content(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    path: str = Query(min_length=1),
) -> Response:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    data, entry = await PodWorkspaceService(session).read_content(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )
    safe_name = entry.name.replace('"', "")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/{project_id}/workspace/preview")
async def preview_company_workspace_text(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    path: str = Query(min_length=1),
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    return await PodWorkspaceService(session).preview_text(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )


@router.delete("/{project_id}/workspace/entries")
async def delete_company_workspace_entry(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    path: str = Query(min_length=1),
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    return await PodWorkspaceService(session).delete_entry(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )


@router.post("/{project_id}/workspace/move")
async def move_company_workspace_entry(
    company_id: str,
    project_id: str,
    body: WorkspaceMoveBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    return await PodWorkspaceService(session).move_entry(
        project_id=project_id,
        src=body.src,
        dst=body.dst,
        principal=principal,
        employee=employee,
    )


@router.post("/{project_id}/workspace/copy")
async def copy_company_workspace_entry(
    company_id: str,
    project_id: str,
    body: WorkspaceCopyBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AdminContainerReadService(session).get_container_for_company(company_id, project_id)
    return await PodWorkspaceService(session).copy_entry(
        project_id=project_id,
        src=body.src,
        dst=body.dst,
        principal=principal,
        employee=employee,
    )


@router.delete("/{project_id}")
async def delete_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).delete_for_company(
        company_id=company_id, project_id=project_id, principal=principal, employee=employee
    )
