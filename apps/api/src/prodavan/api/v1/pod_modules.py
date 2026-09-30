"""Pod-facing module meta + data + actions routes (Bridge JWT + module scopes)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from prodavan.api.agent_auth import PodBridgeDep
from prodavan.api.deps import SessionDep
from prodavan.application.modules.chat_scope import SESSION_HEADER
from prodavan.application.tenant_infra.equipment_catalog_search_service import (
    EquipmentCatalogPodSearchService,
)
from prodavan.application.tenant_infra.pod_modules import PodModuleDataService
from prodavan.application.websearch import WebSearchService

router = APIRouter(tags=["pod-modules"])


class PodModuleDataBody(BaseModel):
    body: dict[str, Any] = Field(default_factory=dict)


class PodModuleMetaBody(BaseModel):
    body: Any = None


class PodModuleActionBody(BaseModel):
    row_id: str | None = None


class EquipmentCatalogSearchBody(BaseModel):
    query: str | None = None
    part_number: str | None = None
    brand: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    in_stock_only: bool = True
    catalog_ids: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


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


@router.get("/projects/{project_id}/modules/{module_id}/meta/documents")
async def list_pod_module_meta_documents(
    project_id: str,
    module_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    items = await PodModuleDataService(session).list_meta_documents(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
    )
    return {"items": items}


@router.get("/projects/{project_id}/modules/{module_id}/meta/documents/{slug}")
async def get_pod_module_meta_document(
    project_id: str,
    module_id: str,
    slug: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await PodModuleDataService(session).get_meta_document(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        slug=slug,
    )


@router.put("/projects/{project_id}/modules/{module_id}/meta/documents/{slug}")
async def put_pod_module_meta_document(
    project_id: str,
    module_id: str,
    slug: str,
    body: PodModuleMetaBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await PodModuleDataService(session).put_meta_document(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        slug=slug,
        body=body.body,
    )


@router.get("/projects/{project_id}/modules/{module_id}/data/{table_slug}")
async def list_pod_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict[str, Any]:
    items = await PodModuleDataService(session).list_data_rows(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        session_id=x_prodavan_session_id,
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
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict[str, Any]:
    return await PodModuleDataService(session).create_data_row(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        body=body.body,
        session_id=x_prodavan_session_id,
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
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict[str, Any]:
    return await PodModuleDataService(session).update_data_row(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        body=body.body,
        session_id=x_prodavan_session_id,
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


@router.post("/projects/{project_id}/modules/{module_id}/actions/{action_id}/invoke")
async def invoke_pod_module_action(
    project_id: str,
    module_id: str,
    action_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    body: PodModuleActionBody | None = None,
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict[str, Any]:
    return await PodModuleDataService(session).invoke_action(
        bridge=bridge,
        project_id=project_id,
        module_id=module_id,
        action_id=action_id,
        row_id=body.row_id if body else None,
        session_id=x_prodavan_session_id,
    )


@router.get("/projects/{project_id}/modules/mod_equipment/equipment/catalog-sources")
async def equipment_catalog_sources(
    project_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await EquipmentCatalogPodSearchService(session).catalog_sources(
        bridge=bridge, project_id=project_id
    )


@router.post("/projects/{project_id}/modules/mod_equipment/equipment/catalog-search")
async def equipment_catalog_search(
    project_id: str,
    body: EquipmentCatalogSearchBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await EquipmentCatalogPodSearchService(session).catalog_search(
        bridge=bridge,
        project_id=project_id,
        query=body.query,
        part_number=body.part_number,
        brand=body.brand,
        price_min=body.price_min,
        price_max=body.price_max,
        in_stock_only=body.in_stock_only,
        catalog_ids=body.catalog_ids,
        limit=body.limit,
        offset=body.offset,
    )


class WebSearchBody(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)


@router.post("/projects/{project_id}/web-search")
async def web_search(
    project_id: str,
    body: WebSearchBody,
    bridge: PodBridgeDep,
) -> dict[str, Any]:
    """CLAW-WEB — web.search proxy for the sandbox agent-runtime.

    The pod's built-in `web.search` tool is configured (via env) to call this
    endpoint as its search provider. The pod never reaches SearxNG directly:
    the API proxies the query, enforces a per-pod rate limit, and emits
    metrics. Returns SearxNG-shaped JSON (`{"results": [...]}`) so the SDK
    tool consumes it without knowing it is a proxy.
    """
    bridge.require_project(project_id)
    return await WebSearchService().search(
        bridge=bridge,
        query=body.query,
        limit=body.limit,
    )
