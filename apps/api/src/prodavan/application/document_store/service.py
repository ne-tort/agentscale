"""Document Store service — namespace ACL, tenancy, events."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.document_store.ports.document_store import DocumentStorePort
from prodavan.application.document_store.publish import (
    emit_document_deleted,
    emit_document_index_ensured,
    emit_document_written,
    emit_op_metric,
)
from prodavan.domain.document_store.types import (
    DEFAULT_FIND_LIMIT,
    MAX_FIND_LIMIT,
    METRIC_DOCUMENT_DELETES,
    METRIC_DOCUMENT_READS,
    METRIC_DOCUMENT_WRITES,
    PLATFORM_NAMESPACES,
    FindResult,
    IndexSpec,
    WriteResult,
    validate_collection,
    validate_namespace,
)
from prodavan.domain.errors import AppError


class DocumentStoreService:
    """In-proc façade for other BCs and admin HTTP."""

    def __init__(self, store: DocumentStorePort) -> None:
        self._store = store

    async def ping(self) -> bool:
        return await self._store.ping()

    def _resolve_ns_col(self, namespace: str, collection: str) -> tuple[str, str]:
        try:
            return validate_namespace(namespace), validate_collection(collection)
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
        filter: dict[str, Any] | None = None,
        document: dict[str, Any] | None = None,
    ) -> None:
        if namespace in PLATFORM_NAMESPACES:
            return
        cid = (company_id or "").strip() or None
        if not cid:
            # Allow company_id carried in filter/document for BC callers.
            for blob in (filter, document):
                if blob and blob.get("company_id"):
                    cid = str(blob["company_id"]).strip() or None
                    break
        if not cid:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="company_id required for tenant document_store namespace",
            )

    async def upsert(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        document: dict[str, Any],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> WriteResult:
        ns, col = self._resolve_ns_col(namespace, collection)
        if not filter:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="upsert filter required",
            )
        self._require_tenancy(namespace=ns, company_id=company_id, filter=filter, document=document)
        result = await self._store.upsert(
            namespace=ns, collection=col, filter=filter, document=document
        )
        await emit_document_written(
            session=session,
            namespace=ns,
            collection=col,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            matched=result.matched,
            modified=result.modified,
            upserted_id=result.upserted_id,
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENT_WRITES,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return result

    async def get(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        ns, col = self._resolve_ns_col(namespace, collection)
        self._require_tenancy(namespace=ns, company_id=company_id, filter=filter)
        doc = await self._store.get(namespace=ns, collection=col, filter=filter)
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENT_READS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return doc

    async def find(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any] | None = None,
        limit: int | None = None,
        skip: int = 0,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> FindResult:
        ns, col = self._resolve_ns_col(namespace, collection)
        filt = dict(filter or {})
        self._require_tenancy(namespace=ns, company_id=company_id, filter=filt)
        lim = DEFAULT_FIND_LIMIT if limit is None else int(limit)
        lim = max(1, min(lim, MAX_FIND_LIMIT))
        skip_i = max(0, int(skip))
        result = await self._store.find(
            namespace=ns, collection=col, filter=filt, limit=lim, skip=skip_i
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENT_READS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            delta=max(1, len(result.items)),
        )
        return result

    async def delete(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        ns, col = self._resolve_ns_col(namespace, collection)
        if not filter:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="delete filter required",
            )
        self._require_tenancy(namespace=ns, company_id=company_id, filter=filter)
        deleted = await self._store.delete(namespace=ns, collection=col, filter=filter)
        await emit_document_deleted(
            session=session,
            namespace=ns,
            collection=col,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            deleted=deleted,
        )
        if deleted:
            await emit_op_metric(
                session=session,
                metric=METRIC_DOCUMENT_DELETES,
                company_id=company_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                delta=deleted,
            )
        return deleted

    async def ensure_indexes(
        self,
        *,
        namespace: str,
        collection: str,
        specs: list[IndexSpec],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[str]:
        ns, col = self._resolve_ns_col(namespace, collection)
        if not specs:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="index specs required",
            )
        names = await self._store.ensure_indexes(namespace=ns, collection=col, specs=specs)
        await emit_document_index_ensured(
            session=session,
            namespace=ns,
            collection=col,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            index_names=names,
        )
        return names
