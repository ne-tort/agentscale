"""Tenant Infra HTTP — Cache / Docs / UserDB / Events / Objects for Project Pods."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from prodavan.api.agent_auth import PodBridgeDep
from prodavan.api.deps import SessionDep
from prodavan.application.tenant_infra.docs_service import TenantDocsService
from prodavan.application.tenant_infra.equipment_catalog_search_service import TenantSearchService
from prodavan.application.tenant_infra.events_service import TenantEventsService
from prodavan.application.tenant_infra.objects_service import TenantObjectsService
from prodavan.application.tenant_infra.service import TenantInfraService
from prodavan.application.tenant_infra.userdb_service import TenantUserDbService

router = APIRouter(tags=["tenant-infra"])


class CacheSetBody(BaseModel):
    value: str
    ttl_sec: int | None = Field(default=None, ge=1, le=86400 * 7)


class CacheIncrBody(BaseModel):
    amount: int = 1


class DocUpsertBody(BaseModel):
    document: dict[str, Any] = Field(default_factory=dict)


class UserDbCreateTableBody(BaseModel):
    columns: list[dict[str, str]] = Field(default_factory=list)


class UserDbInsertBody(BaseModel):
    row: dict[str, Any] = Field(default_factory=dict)


class EventPublishBody(BaseModel):
    type: str = "message"
    payload: dict[str, Any] = Field(default_factory=dict)


class ObjectPutBody(BaseModel):
    name: str = "object"
    content_b64: str
    mime: str | None = None


class SearchQueryBody(BaseModel):
    namespace: str
    index: str
    query: dict[str, Any] | None = None
    from_: int = Field(default=0, ge=0, alias="from")
    size: int = Field(default=20, ge=1, le=100)

    model_config = {"populate_by_name": True}


@router.get("/projects/{project_id}/infra/cache/{key:path}")
async def cache_get(
    project_id: str,
    key: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantInfraService().get(
        bridge=bridge, project_id=project_id, key=key, session=session
    )


@router.put("/projects/{project_id}/infra/cache/{key:path}")
async def cache_set(
    project_id: str,
    key: str,
    body: CacheSetBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantInfraService().set(
        bridge=bridge,
        project_id=project_id,
        key=key,
        value=body.value,
        ttl_sec=body.ttl_sec,
        session=session,
    )


@router.delete("/projects/{project_id}/infra/cache/{key:path}")
async def cache_delete(
    project_id: str,
    key: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantInfraService().delete(
        bridge=bridge, project_id=project_id, key=key, session=session
    )


@router.post("/projects/{project_id}/infra/cache/{key:path}/incr")
async def cache_incr(
    project_id: str,
    key: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    body: CacheIncrBody | None = None,
) -> dict[str, Any]:
    amount = body.amount if body is not None else 1
    return await TenantInfraService().incr(
        bridge=bridge,
        project_id=project_id,
        key=key,
        amount=amount,
        session=session,
    )


@router.put("/projects/{project_id}/infra/docs/{collection}/{doc_id}")
async def docs_upsert(
    project_id: str,
    collection: str,
    doc_id: str,
    body: DocUpsertBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantDocsService().upsert(
        bridge=bridge,
        project_id=project_id,
        collection=collection,
        doc_id=doc_id,
        document=body.document,
        session=session,
    )


@router.get("/projects/{project_id}/infra/docs/{collection}/{doc_id}")
async def docs_get(
    project_id: str,
    collection: str,
    doc_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantDocsService().get(
        bridge=bridge,
        project_id=project_id,
        collection=collection,
        doc_id=doc_id,
        session=session,
    )


@router.get("/projects/{project_id}/infra/docs/{collection}")
async def docs_find(
    project_id: str,
    collection: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    limit: int = 50,
    skip: int = 0,
) -> dict[str, Any]:
    return await TenantDocsService().find(
        bridge=bridge,
        project_id=project_id,
        collection=collection,
        limit=limit,
        skip=skip,
        session=session,
    )


@router.delete("/projects/{project_id}/infra/docs/{collection}/{doc_id}")
async def docs_delete(
    project_id: str,
    collection: str,
    doc_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantDocsService().delete(
        bridge=bridge,
        project_id=project_id,
        collection=collection,
        doc_id=doc_id,
        session=session,
    )


@router.post("/projects/{project_id}/infra/userdb/tables/{table}")
async def userdb_create_table(
    project_id: str,
    table: str,
    body: UserDbCreateTableBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantUserDbService().create_table(
        bridge=bridge,
        project_id=project_id,
        table=table,
        columns=body.columns,
        session=session,
    )


@router.get("/projects/{project_id}/infra/userdb/tables")
async def userdb_list_tables(
    project_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantUserDbService().list_tables(
        bridge=bridge, project_id=project_id, session=session
    )


@router.post("/projects/{project_id}/infra/userdb/tables/{table}/rows")
async def userdb_insert(
    project_id: str,
    table: str,
    body: UserDbInsertBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantUserDbService().insert(
        bridge=bridge,
        project_id=project_id,
        table=table,
        row=body.row,
        session=session,
    )


@router.get("/projects/{project_id}/infra/userdb/tables/{table}/rows")
async def userdb_select(
    project_id: str,
    table: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    limit: int = 50,
) -> dict[str, Any]:
    return await TenantUserDbService().select(
        bridge=bridge,
        project_id=project_id,
        table=table,
        limit=limit,
        session=session,
    )


@router.delete("/projects/{project_id}/infra/userdb/tables/{table}")
async def userdb_drop_table(
    project_id: str,
    table: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantUserDbService().drop_table(
        bridge=bridge, project_id=project_id, table=table, session=session
    )


@router.post("/projects/{project_id}/infra/events")
async def events_publish(
    project_id: str,
    body: EventPublishBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantEventsService().publish(
        bridge=bridge,
        project_id=project_id,
        typ=body.type,
        payload=body.payload,
        session=session,
    )


@router.get("/projects/{project_id}/infra/events")
async def events_poll(
    project_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    consumer: str = "default",
    limit: int = 50,
    commit: bool = True,
) -> dict[str, Any]:
    return await TenantEventsService().poll(
        bridge=bridge,
        project_id=project_id,
        consumer=consumer,
        limit=limit,
        commit=commit,
        session=session,
    )


@router.put("/projects/{project_id}/infra/objects")
async def objects_put(
    project_id: str,
    body: ObjectPutBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        content = base64.b64decode(body.content_b64, validate=True)
    except Exception as exc:
        from prodavan.domain.errors import AppError

        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="invalid content_b64",
        ) from exc
    return await TenantObjectsService(session).put(
        bridge=bridge,
        project_id=project_id,
        name=body.name,
        content=content,
        mime=body.mime,
    )


@router.get("/projects/{project_id}/infra/objects")
async def objects_list(
    project_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantObjectsService(session).list(bridge=bridge, project_id=project_id)


@router.get("/projects/{project_id}/infra/objects/{asset_id}")
async def objects_get(
    project_id: str,
    asset_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> Response:
    data, mime, title = await TenantObjectsService(session).get_bytes(
        bridge=bridge, project_id=project_id, asset_id=asset_id
    )
    headers = {"Cache-Control": "no-store"}
    if title:
        headers["Content-Disposition"] = f'attachment; filename="{title}"'
    return Response(content=data, media_type=mime or "application/octet-stream", headers=headers)


@router.delete("/projects/{project_id}/infra/objects/{asset_id}")
async def objects_delete(
    project_id: str,
    asset_id: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantObjectsService(session).delete(
        bridge=bridge, project_id=project_id, asset_id=asset_id
    )


@router.get("/projects/{project_id}/infra/search/health")
async def search_health(
    project_id: str,
    bridge: PodBridgeDep,
) -> dict[str, Any]:
    return await TenantSearchService().health(bridge=bridge, project_id=project_id)


@router.post("/projects/{project_id}/infra/search/query")
async def search_query(
    project_id: str,
    body: SearchQueryBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantSearchService().query(
        bridge=bridge,
        project_id=project_id,
        namespace=body.namespace,
        index=body.index,
        query=body.query,
        from_=body.from_,
        size=body.size,
        session=session,
    )
