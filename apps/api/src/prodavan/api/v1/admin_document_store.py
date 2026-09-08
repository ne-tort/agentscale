"""Admin Document Store HTTP surface (platform.admin ops / diagnostics)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key
from prodavan.core.infra.mongo_manager import get_document_store_service, get_mongo_manager
from prodavan.domain.document_store.types import IndexSpec
from prodavan.domain.errors import AppError

router = APIRouter(prefix="/admin/document-store", tags=["admin-document-store"])


class UpsertBody(BaseModel):
    namespace: str
    collection: str
    filter: dict[str, Any]
    document: dict[str, Any]
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None


class FilterBody(BaseModel):
    namespace: str
    collection: str
    filter: dict[str, Any] = Field(default_factory=dict)
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None
    limit: int | None = None
    skip: int = 0


class IndexBody(BaseModel):
    namespace: str
    collection: str
    indexes: list[dict[str, Any]]
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None


async def _rate_limit() -> None:
    await enforce_rate_limit(
        cache_key("rl", "admin", "document-store"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin document-store rate limit exceeded",
    )


@router.get("/health")
async def document_store_health(_: PlatformAdminDep) -> dict[str, Any]:
    mgr = get_mongo_manager()
    if mgr is None:
        return {"status": "unavailable", "enabled": False}
    ok = await mgr.ping()
    return {
        "status": "ok" if ok else "fail",
        "enabled": mgr.enabled,
        "backend": "mongodb" if mgr.enabled else "memory",
    }


@router.post("/upsert")
async def document_store_upsert(
    _: PlatformAdminDep,
    session: SessionDep,
    body: UpsertBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_document_store_service()
    result = await svc.upsert(
        namespace=body.namespace,
        collection=body.collection,
        filter=body.filter,
        document=body.document,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {
        "matched": result.matched,
        "modified": result.modified,
        "upserted_id": result.upserted_id,
    }


@router.post("/get")
async def document_store_get(
    _: PlatformAdminDep,
    session: SessionDep,
    body: FilterBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_document_store_service()
    doc = await svc.get(
        namespace=body.namespace,
        collection=body.collection,
        filter=body.filter,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {"document": doc}


@router.post("/find")
async def document_store_find(
    _: PlatformAdminDep,
    session: SessionDep,
    body: FilterBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_document_store_service()
    result = await svc.find(
        namespace=body.namespace,
        collection=body.collection,
        filter=body.filter,
        limit=body.limit,
        skip=body.skip,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {"items": result.items, "count": result.count}


@router.post("/delete")
async def document_store_delete(
    _: PlatformAdminDep,
    session: SessionDep,
    body: FilterBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_document_store_service()
    deleted = await svc.delete(
        namespace=body.namespace,
        collection=body.collection,
        filter=body.filter,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {"deleted": deleted}


@router.post("/ensure-indexes")
async def document_store_ensure_indexes(
    _: PlatformAdminDep,
    session: SessionDep,
    body: IndexBody,
) -> dict[str, Any]:
    await _rate_limit()
    specs: list[IndexSpec] = []
    for raw in body.indexes:
        keys_raw = raw.get("keys") or []
        keys: list[tuple[str, int]] = []
        for item in keys_raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                keys.append((str(item[0]), int(item[1])))
            elif isinstance(item, dict) and "field" in item:
                keys.append((str(item["field"]), int(item.get("order", 1))))
            else:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"invalid index keys entry: {item!r}",
                )
        if not keys:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="index keys required",
            )
        specs.append(
            IndexSpec(
                keys=keys,
                name=raw.get("name"),
                unique=bool(raw.get("unique", False)),
            )
        )
    svc = get_document_store_service()
    names = await svc.ensure_indexes(
        namespace=body.namespace,
        collection=body.collection,
        specs=specs,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {"indexes": names}
