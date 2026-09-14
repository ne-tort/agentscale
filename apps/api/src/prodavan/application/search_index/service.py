"""Search Index service — namespace ACL, tenancy, quotas, events."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.search_index.ports.search_index import SearchIndexPort
from prodavan.application.search_index.publish import (
    emit_op_metric,
    emit_search_document_deleted,
    emit_search_document_indexed,
    emit_search_index_deleted,
    emit_search_index_ensured,
)
from prodavan.domain.errors import AppError
from prodavan.domain.search_index.types import (
    DEFAULT_SEARCH_SIZE,
    MAX_BULK_BATCH,
    MAX_INDEXES_PER_COMPANY,
    MAX_MAPPING_FIELDS,
    MAX_SEARCH_SIZE,
    METRIC_SEARCH_DELETES,
    METRIC_SEARCH_INDEXES_ENSURED,
    METRIC_SEARCH_QUERIES,
    METRIC_SEARCH_WRITES,
    PLATFORM_NAMESPACES,
    BulkIndexResult,
    IndexDocumentResult,
    IndexMappingSpec,
    IndexResult,
    SearchResult,
    count_mapping_fields,
    validate_doc_id,
    validate_index,
    validate_namespace,
)


class SearchIndexService:
    """In-proc façade for other BCs and admin HTTP."""

    def __init__(self, store: SearchIndexPort) -> None:
        self._store = store

    async def ping(self) -> bool:
        return await self._store.ping()

    def _resolve_ns_idx(self, namespace: str, index: str) -> tuple[str, str]:
        try:
            return validate_namespace(namespace), validate_index(index)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=str(exc),
            ) from exc

    def _require_tenancy(
        self,
        *,
        namespace: str,
        company_id: str | None,
        document: dict[str, Any] | None = None,
    ) -> str | None:
        if namespace in PLATFORM_NAMESPACES:
            return (company_id or "").strip() or None
        cid = (company_id or "").strip() or None
        if not cid and document and document.get("company_id"):
            cid = str(document["company_id"]).strip() or None
        if not cid:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="company_id required for tenant search_index namespace",
            )
        return cid

    def _inject_tenancy(
        self,
        document: dict[str, Any],
        *,
        company_id: str | None,
        cabinet_id: str | None,
        project_id: str | None,
    ) -> dict[str, Any]:
        out = dict(document)
        if company_id:
            out["company_id"] = company_id
        if cabinet_id:
            out["cabinet_id"] = cabinet_id
        if project_id:
            out["project_id"] = project_id
        return out

    def _tenancy_filter(
        self,
        *,
        namespace: str,
        company_id: str | None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        filt = dict(extra or {})
        if namespace not in PLATFORM_NAMESPACES and company_id:
            filt["company_id"] = company_id
        if cabinet_id:
            filt.setdefault("cabinet_id", cabinet_id)
        if project_id:
            filt.setdefault("project_id", project_id)
        return filt

    async def ensure_index(
        self,
        *,
        namespace: str,
        index: str,
        mappings: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> IndexResult:
        ns, idx = self._resolve_ns_idx(namespace, index)
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        mappings = dict(mappings or {})
        settings = dict(settings or {})
        field_count = count_mapping_fields(mappings)
        if field_count > MAX_MAPPING_FIELDS:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"mapping properties exceed max {MAX_MAPPING_FIELDS}",
            )
        if cid:
            existing = await self._store.list_indexes(namespace=ns, company_id=cid)
            physical = f"{ns}__{idx}"
            if physical not in existing and len(existing) >= MAX_INDEXES_PER_COMPANY:
                raise AppError(
                    code="SEARCH_INDEX_QUOTA",
                    title="Search Index Quota",
                    status=429,
                    detail=f"company index quota exceeded ({MAX_INDEXES_PER_COMPANY})",
                )
            # Ownership lives in mappings._meta (OpenSearch-safe), never in settings.
            meta = dict(mappings.get("_meta") or {})
            meta["company_id"] = cid
            mappings["_meta"] = meta
            props = dict(mappings.get("properties") or {})
            if "company_id" not in props:
                props["company_id"] = {"type": "keyword"}
                mappings["properties"] = props
        # Drop internal/underscore settings keys — OpenSearch rejects unknown settings.
        settings = {k: v for k, v in settings.items() if not str(k).startswith("_")}
        spec = IndexMappingSpec(mappings=mappings, settings=settings)
        try:
            result = await self._store.ensure_index(namespace=ns, index=idx, spec=spec)
        except Exception as exc:
            raise AppError(
                code="SEARCH_INDEX_BACKEND",
                title="Search Index Backend Error",
                status=502,
                detail=str(exc)[:300],
            ) from exc
        await emit_search_index_ensured(
            session=session,
            namespace=ns,
            index=idx,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            created=result.created,
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_SEARCH_INDEXES_ENSURED,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return result

    async def delete_index(
        self,
        *,
        namespace: str,
        index: str,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> bool:
        ns, idx = self._resolve_ns_idx(namespace, index)
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        deleted = await self._store.delete_index(namespace=ns, index=idx)
        await emit_search_index_deleted(
            session=session,
            namespace=ns,
            index=idx,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            deleted=deleted,
        )
        return deleted

    async def list_indexes(
        self,
        *,
        namespace: str | None = None,
        company_id: str | None = None,
    ) -> list[str]:
        return await self._store.list_indexes(namespace=namespace, company_id=company_id)

    async def index_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        document: dict[str, Any],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        refresh: bool = False,
        session: AsyncSession | None = None,
    ) -> IndexDocumentResult:
        ns, idx = self._resolve_ns_idx(namespace, index)
        try:
            did = validate_doc_id(doc_id)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=str(exc),
            ) from exc
        if not document:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="document required",
            )
        cid = self._require_tenancy(namespace=ns, company_id=company_id, document=document)
        body = self._inject_tenancy(
            document, company_id=cid, cabinet_id=cabinet_id, project_id=project_id
        )
        result = await self._store.index_document(
            namespace=ns, index=idx, doc_id=did, document=body, refresh=refresh
        )
        await emit_search_document_indexed(
            session=session,
            namespace=ns,
            index=idx,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            doc_id=did,
            result=result.result,
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_SEARCH_WRITES,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return result

    async def bulk_index(
        self,
        *,
        namespace: str,
        index: str,
        documents: list[dict[str, Any]],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        refresh: bool = False,
        session: AsyncSession | None = None,
    ) -> BulkIndexResult:
        ns, idx = self._resolve_ns_idx(namespace, index)
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        if not documents:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="documents required",
            )
        if len(documents) > MAX_BULK_BATCH:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"bulk batch exceeds max {MAX_BULK_BATCH}",
            )
        prepared: list[tuple[str, dict[str, Any]]] = []
        for item in documents:
            doc_id = item.get("doc_id") or item.get("id")
            document = item.get("document") or item.get("source")
            if not isinstance(doc_id, str) or not isinstance(document, dict):
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="each bulk item needs doc_id and document",
                )
            try:
                did = validate_doc_id(doc_id)
            except ValueError as exc:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=str(exc),
                ) from exc
            prepared.append(
                (
                    did,
                    self._inject_tenancy(
                        document, company_id=cid, cabinet_id=cabinet_id, project_id=project_id
                    ),
                )
            )
        result = await self._store.bulk_index(
            namespace=ns, index=idx, documents=prepared, refresh=refresh
        )
        await emit_search_document_indexed(
            session=session,
            namespace=ns,
            index=idx,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            doc_id="*",
            result="bulk",
            count=result.indexed,
        )
        if result.indexed:
            await emit_op_metric(
                session=session,
                metric=METRIC_SEARCH_WRITES,
                company_id=cid,
                cabinet_id=cabinet_id,
                project_id=project_id,
                delta=result.indexed,
            )
        return result

    async def get_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        _ = session
        ns, idx = self._resolve_ns_idx(namespace, index)
        try:
            did = validate_doc_id(doc_id)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=str(exc),
            ) from exc
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        doc = await self._store.get_document(namespace=ns, index=idx, doc_id=did)
        if doc is None:
            return None
        if cid and doc.get("company_id") not in (None, cid):
            return None
        if project_id and doc.get("project_id") not in (None, project_id):
            return None
        if cabinet_id and doc.get("cabinet_id") not in (None, cabinet_id):
            return None
        return doc

    async def delete_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        refresh: bool = False,
        session: AsyncSession | None = None,
    ) -> bool:
        ns, idx = self._resolve_ns_idx(namespace, index)
        try:
            did = validate_doc_id(doc_id)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=str(exc),
            ) from exc
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        existing = await self.get_document(
            namespace=ns,
            index=idx,
            doc_id=did,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        if existing is None:
            return False
        deleted = await self._store.delete_document(
            namespace=ns, index=idx, doc_id=did, refresh=refresh
        )
        await emit_search_document_deleted(
            session=session,
            namespace=ns,
            index=idx,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            doc_id=did,
            deleted=deleted,
        )
        if deleted:
            await emit_op_metric(
                session=session,
                metric=METRIC_SEARCH_DELETES,
                company_id=cid,
                cabinet_id=cabinet_id,
                project_id=project_id,
            )
        return deleted

    async def search(
        self,
        *,
        namespace: str,
        index: str,
        query: dict[str, Any] | None = None,
        from_: int = 0,
        size: int | None = None,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        filter: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
        apply_tenant_filter: bool = True,
    ) -> SearchResult:
        ns, idx = self._resolve_ns_idx(namespace, index)
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        lim = DEFAULT_SEARCH_SIZE if size is None else int(size)
        lim = max(1, min(lim, MAX_SEARCH_SIZE))
        skip = max(0, int(from_))
        filt = self._tenancy_filter(
            namespace=ns,
            company_id=cid if apply_tenant_filter else None,
            cabinet_id=cabinet_id if apply_tenant_filter else None,
            project_id=project_id if apply_tenant_filter else None,
            extra=filter,
        )
        result = await self._store.search(
            namespace=ns,
            index=idx,
            query=dict(query or {}),
            from_=skip,
            size=lim,
            filter=filt,
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_SEARCH_QUERIES,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return result

    async def count(
        self,
        *,
        namespace: str,
        index: str,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> int:
        ns, idx = self._resolve_ns_idx(namespace, index)
        cid = self._require_tenancy(namespace=ns, company_id=company_id)
        filt = self._tenancy_filter(
            namespace=ns,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            extra=filter,
        )
        return await self._store.count(namespace=ns, index=idx, filter=filt)
