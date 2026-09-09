"""Pod-facing module data routes (Bridge JWT + module scopes)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.agent_auth import PodBridgeDep
from prodavan.api.deps import SessionDep
from prodavan.application.tenant_infra.pod_modules import PodModuleDataService

router = APIRouter(tags=["pod-modules"])


class PodModuleDataBody(BaseModel):
    body: dict[str, Any] = Field(default_factory=dict)


@router.get("/projects/{project_id}/modules")
async def list_pod_modules(
    project_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    items = await PodModuleDataService(session).list_bound_modules(
        bridge=bridge, project_id=project_id
    )
    return {"items": items}


@router.get("/projects/{project_id}/modules/{module_id}/data/{table_slug}")
async def list_pod_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    items = await PodModuleDataService(session).list_data_rows(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
    )
    return {"items": items}


@router.post("/projects/{project_id}/modules/{module_id}/data/{table_slug}")
async def create_pod_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    body: PodModuleDataBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await PodModuleDataService(session).create_data_row(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        body=body.body,
    )


@router.patch("/projects/{project_id}/modules/{module_id}/data/{table_slug}/{row_id}")
async def update_pod_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    row_id: str,
    body: PodModuleDataBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await PodModuleDataService(session).update_data_row(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        body=body.body,
    )


@router.delete("/projects/{project_id}/modules/{module_id}/data/{table_slug}/{row_id}")
async def delete_pod_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    row_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await PodModuleDataService(session).delete_data_row(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
    )
