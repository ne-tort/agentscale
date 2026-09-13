"""Admin Search Index HTTP surface (platform.admin ops / diagnostics)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key
from prodavan.core.infra.opensearch_manager import get_opensearch_manager, get_search_index_service

router = APIRouter(prefix="/admin/search-index", tags=["admin-search-index"])


class EnsureIndexBody(BaseModel):
    namespace: str
    index: str
    mappings: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None


class IndexRefBody(BaseModel):
    namespace: str
    index: str
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None


class IndexDocumentBody(BaseModel):
    namespace: str
    index: str
    doc_id: str
    document: dict[str, Any]
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None
    refresh: bool = False


class BulkDocumentBody(BaseModel):
    namespace: str
    index: str
    documents: list[dict[str, Any]]
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None
    refresh: bool = False


class GetDocumentBody(BaseModel):
    namespace: str
    index: str
    doc_id: str
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None


class DeleteDocumentBody(BaseModel):
    namespace: str
    index: str
    doc_id: str
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None
    refresh: bool = False


class SearchBody(BaseModel):
    namespace: str
    index: str
    query: dict[str, Any] = Field(default_factory=dict)
    filter: dict[str, Any] = Field(default_factory=dict)
    from_: int = Field(default=0, alias="from")
    size: int | None = None
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None

    model_config = {"populate_by_name": True}


async def _rate_limit() -> None:
    await enforce_rate_limit(
        cache_key("rl", "admin", "search-index"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin search-index rate limit exceeded",
    )


@router.get("/health")
async def search_index_health(_: PlatformAdminDep) -> dict[str, Any]:
    mgr = get_opensearch_manager()
    if mgr is None:
        return {"status": "unavailable", "enabled": False}
    ok = await mgr.ping()
    return {
        "status": "ok" if ok else "fail",
        "enabled": mgr.enabled,
        "backend": "opensearch" if mgr.enabled else "memory",
    }


@router.post("/indexes/ensure")
async def search_index_ensure(
    _: PlatformAdminDep,
    session: SessionDep,
    body: EnsureIndexBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    result = await svc.ensure_index(
        namespace=body.namespace,
        index=body.index,
        mappings=body.mappings,
        settings=body.settings,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {
        "index": result.index,
        "created": result.created,
        "acknowledged": result.acknowledged,
    }


@router.post("/indexes/delete")
async def search_index_delete(
    _: PlatformAdminDep,
    session: SessionDep,
    body: IndexRefBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    deleted = await svc.delete_index(
        namespace=body.namespace,
        index=body.index,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {"deleted": deleted}


@router.post("/documents/index")
async def search_documents_index(
    _: PlatformAdminDep,
    session: SessionDep,
    body: IndexDocumentBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    result = await svc.index_document(
        namespace=body.namespace,
        index=body.index,
        doc_id=body.doc_id,
        document=body.document,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        refresh=body.refresh,
        session=session,
    )
    return {"doc_id": result.doc_id, "result": result.result, "version": result.version}


@router.post("/documents/bulk")
async def search_documents_bulk(
    _: PlatformAdminDep,
    session: SessionDep,
    body: BulkDocumentBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    result = await svc.bulk_index(
        namespace=body.namespace,
        index=body.index,
        documents=body.documents,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        refresh=body.refresh,
        session=session,
    )
    return {
        "indexed": result.indexed,
        "errors": result.errors,
        "items": [
            {"doc_id": i.doc_id, "result": i.result, "error": i.error} for i in result.items
        ],
    }


@router.post("/documents/get")
async def search_documents_get(
    _: PlatformAdminDep,
    session: SessionDep,
    body: GetDocumentBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    doc = await svc.get_document(
        namespace=body.namespace,
        index=body.index,
        doc_id=body.doc_id,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        session=session,
    )
    return {"document": doc}


@router.post("/documents/delete")
async def search_documents_delete(
    _: PlatformAdminDep,
    session: SessionDep,
    body: DeleteDocumentBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    deleted = await svc.delete_document(
        namespace=body.namespace,
        index=body.index,
        doc_id=body.doc_id,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        refresh=body.refresh,
        session=session,
    )
    return {"deleted": deleted}


@router.post("/search")
async def search_index_search(
    _: PlatformAdminDep,
    session: SessionDep,
    body: SearchBody,
) -> dict[str, Any]:
    await _rate_limit()
    svc = get_search_index_service()
    result = await svc.search(
        namespace=body.namespace,
        index=body.index,
        query=body.query,
        from_=body.from_,
        size=body.size,
        company_id=body.company_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        filter=body.filter,
        session=session,
    )
    return {
        "hits": [
            {"doc_id": h.doc_id, "score": h.score, "source": h.source} for h in result.hits
        ],
        "total": result.total,
        "took_ms": result.took_ms,
    }
