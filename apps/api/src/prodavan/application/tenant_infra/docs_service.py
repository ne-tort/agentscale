"""Tenant Infra Documents — Mongo namespace tenant_infra via Gateway."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_DOCS, PodBridgeClaims
from prodavan.application.tenant_infra.keys import cache_index_key
from prodavan.application.tenant_infra.publish import emit_tenant_infra_event
from prodavan.application.tenant_infra.quota import TenantInfraQuotaService, enforce_ops_rate, quota_exceeded
from prodavan.core.infra.mongo_manager import get_document_store_service
from prodavan.domain.document_store.types import validate_collection

TENANT_DOCS_NS = "tenant_infra"
_COL_INDEX_SUFFIX = "__docs_cols"


def _physical_collection(project_id: str, user_collection: str) -> str:
    user = validate_collection(user_collection)
    pid = re.sub(r"[^a-z0-9]", "", (project_id or "").lower())[:20] or "x"
    name = f"p{pid}_{user}"
    return name[:64]


def _cols_index_key(company_id: str, project_id: str) -> str:
    return cache_index_key(company_id=company_id, project_id=project_id) + _COL_INDEX_SUFFIX


class TenantDocsService:
    def _require(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_DOCS)

    async def _quota(self, bridge: PodBridgeClaims, session: AsyncSession | None):
        return await TenantInfraQuotaService(session).get_quota(bridge.company_id)

    async def upsert(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        collection: str,
        doc_id: str,
        document: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="docs",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.docs_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        raw = json.dumps(document, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > quota.docs_max_doc_bytes:
            raise quota_exceeded(f"document exceeds {quota.docs_max_doc_bytes} bytes")
        col = _physical_collection(bridge.project_id, collection)
        from prodavan.application.tenant_infra.adapters.memory_cache import get_shared_memory_tenant_cache
        from prodavan.application.tenant_infra.adapters.redis_cache import RedisTenantCache
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        index_cache = RedisTenantCache() if mgr is not None and mgr.enabled else get_shared_memory_tenant_cache()
        idx = _cols_index_key(bridge.company_id, bridge.project_id)
        members = await index_cache.index_members(idx)
        if col not in members and len(members) >= quota.docs_max_collections:
            raise quota_exceeded(f"docs max collections {quota.docs_max_collections} exceeded")

        store = get_document_store_service()
        filt = {
            "company_id": bridge.company_id,
            "project_id": bridge.project_id,
            "id": doc_id,
        }
        counted = await store.find(
            namespace=TENANT_DOCS_NS,
            collection=col,
            filter={"company_id": bridge.company_id, "project_id": bridge.project_id},
            limit=1,
            skip=0,
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            session=session,
        )
        existing = await store.get(
            namespace=TENANT_DOCS_NS,
            collection=col,
            filter=filt,
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            session=session,
        )
        if existing is None and counted.count >= quota.docs_max_docs_per_collection:
            raise quota_exceeded(
                f"docs max docs per collection {quota.docs_max_docs_per_collection} exceeded"
            )
        body = {
            **{k: v for k, v in document.items() if k not in {"company_id", "project_id", "id", "_id"}},
            "company_id": bridge.company_id,
            "project_id": bridge.project_id,
            "id": doc_id,
        }
        result = await store.upsert(
            namespace=TENANT_DOCS_NS,
            collection=col,
            filter=filt,
            document=body,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            session=session,
        )
        await index_cache.index_add(idx, col)
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "docs.upsert", "collection": collection},
        )
        return {
            "collection": collection,
            "id": doc_id,
            "matched": result.matched,
            "modified": result.modified,
            "upserted_id": result.upserted_id,
        }

    async def get(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        collection: str,
        doc_id: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="docs",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.docs_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        col = _physical_collection(bridge.project_id, collection)
        store = get_document_store_service()
        doc = await store.get(
            namespace=TENANT_DOCS_NS,
            collection=col,
            filter={
                "company_id": bridge.company_id,
                "project_id": bridge.project_id,
                "id": doc_id,
            },
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            session=session,
        )
        return {"collection": collection, "id": doc_id, "document": doc, "found": doc is not None}

    async def find(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        collection: str,
        limit: int = 50,
        skip: int = 0,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="docs",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.docs_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        col = _physical_collection(bridge.project_id, collection)
        store = get_document_store_service()
        result = await store.find(
            namespace=TENANT_DOCS_NS,
            collection=col,
            filter={"company_id": bridge.company_id, "project_id": bridge.project_id},
            limit=min(max(1, limit), 200),
            skip=max(0, skip),
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            session=session,
        )
        return {"collection": collection, "items": result.items, "count": result.count}

    async def delete(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        collection: str,
        doc_id: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="docs",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.docs_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        col = _physical_collection(bridge.project_id, collection)
        store = get_document_store_service()
        deleted = await store.delete(
            namespace=TENANT_DOCS_NS,
            collection=col,
            filter={
                "company_id": bridge.company_id,
                "project_id": bridge.project_id,
                "id": doc_id,
            },
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            session=session,
        )
        return {"collection": collection, "id": doc_id, "deleted": deleted}

    async def purge_project(self, *, company_id: str, project_id: str) -> int:
        from prodavan.application.tenant_infra.adapters.memory_cache import get_shared_memory_tenant_cache
        from prodavan.application.tenant_infra.adapters.redis_cache import RedisTenantCache
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        index_cache = RedisTenantCache() if mgr is not None and mgr.enabled else get_shared_memory_tenant_cache()
        idx = _cols_index_key(company_id, project_id)
        cols = await index_cache.index_members(idx)
        store = get_document_store_service()
        total = 0
        for col in cols:
            total += await store.delete(
                namespace=TENANT_DOCS_NS,
                collection=col,
                filter={"company_id": company_id, "project_id": project_id},
                company_id=company_id,
                project_id=project_id,
                session=None,
            )
        await index_cache.purge_keys([idx])
        return total
