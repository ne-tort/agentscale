"""Pod equipment catalog search via OpenSearch (Search Index BC)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_catalog_opensearch import (
    OS_NAMESPACE,
    catalog_os_index_name,
)
from prodavan.application.modules.equipment_catalog_search import (
    MATCH_EXACT_PN,
    MATCH_OTHER,
    MATCH_PN_PREFIX,
    MATCH_TITLE,
    CatalogHit,
    parse_price,
)
from prodavan.application.modules.module_instance_service import (
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.application.pod_identity.bridge import (
    SCOPE_INFRA_SEARCH,
    PodBridgeClaims,
    module_rows_scope,
)
from prodavan.core.infra.opensearch_manager import get_search_index_service
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_MODULE_ID = "mod_equipment"
_CATALOGS = "catalogs"

_MATCH_LABELS = {
    MATCH_EXACT_PN: "exact_pn",
    MATCH_PN_PREFIX: "pn_prefix",
    MATCH_TITLE: "title",
    MATCH_OTHER: "other",
}


def _project_allowed(body: dict[str, Any], project_id: str) -> bool:
    raw = body.get("project_ids")
    if raw is None:
        return True
    if isinstance(raw, list):
        if not raw:
            return True
        return project_id in {str(x) for x in raw}
    return True


def _match_rank(*, part_number: str, title: str, want_pn: str, want_q: str) -> int:
    pn = (part_number or "").strip().casefold()
    want_pn_cf = (want_pn or "").strip().casefold()
    title_cf = (title or "").casefold()
    want_q_cf = (want_q or "").strip().casefold()
    if want_pn_cf and pn == want_pn_cf:
        return MATCH_EXACT_PN
    if want_pn_cf and pn.startswith(want_pn_cf):
        return MATCH_PN_PREFIX
    if want_q_cf and want_q_cf in title_cf:
        return MATCH_TITLE
    if want_pn_cf and want_pn_cf in pn:
        return MATCH_PN_PREFIX
    return MATCH_OTHER


def _build_os_query(
    *,
    query: str | None,
    part_number: str | None,
    brand: str | None,
    price_min: float | None,
    price_max: float | None,
    in_stock_only: bool,
    catalog_ids: list[str] | None,
) -> dict[str, Any]:
    must: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    should: list[dict[str, Any]] = []

    pn = (part_number or "").strip()
    q = (query or "").strip()
    if pn:
        should.append({"term": {"part_number": {"value": pn, "boost": 10}}})
        should.append({"prefix": {"part_number": {"value": pn, "boost": 5}}})
        should.append({"match": {"part_number.text": {"query": pn, "boost": 3}}})
        # P/N often appears only inside title for sparse catalogs.
        should.append({"match_phrase": {"title": {"query": pn, "boost": 2}}})
    if q:
        should.append(
            {
                "multi_match": {
                    "query": q,
                    "fields": [
                        "title^3",
                        "part_number.text^2",
                        "brand^2",
                        "supplier",
                        "lead_time",
                        "price",
                    ],
                    "type": "best_fields",
                    "operator": "and",
                }
            }
        )
        # Soft fallback when operator:and is too strict on long queries.
        should.append(
            {
                "multi_match": {
                    "query": q,
                    "fields": [
                        "title^2",
                        "part_number.text",
                        "brand",
                        "supplier",
                        "lead_time",
                    ],
                    "type": "best_fields",
                }
            }
        )
    if should:
        must.append({"bool": {"should": should, "minimum_should_match": 1}})
    else:
        must.append({"match_all": {}})

    brand_q = (brand or "").strip()
    if brand_q:
        # Many catalogs leave brand empty and put the manufacturer only in title
        # (supplier part-number lines). Match keyword brand OR title text.
        filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"brand": brand_q}},
                        {
                            "wildcard": {
                                "brand": {
                                    "value": f"*{brand_q}*",
                                    "case_insensitive": True,
                                }
                            }
                        },
                        {"match_phrase": {"title": brand_q}},
                        {"match": {"title": {"query": brand_q, "operator": "and"}}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    if catalog_ids:
        filters.append({"terms": {"catalog_id": [str(x) for x in catalog_ids]}})
    if in_stock_only:
        filters.append({"term": {"in_stock": True}})

    range_body: dict[str, Any] = {}
    if price_min is not None:
        range_body["gte"] = float(price_min)
    if price_max is not None:
        range_body["lte"] = float(price_max)
    if range_body:
        filters.append({"range": {"price_num": range_body}})

    return {"bool": {"must": must, "filter": filters}}


class EquipmentCatalogPodSearchService:
    """Resolve ready catalogs for a project and search OpenSearch indexes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _require(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_SEARCH)
        bridge.require_scope(module_rows_scope(_MODULE_ID))

    async def _ready_catalogs(
        self, *, project_id: str, catalog_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        # Global MP → cabinet SoT; local MP → project leaf. Never ensure_project_instance
        # (that creates a leaf and fails for global binds).
        inst = await ModuleInstanceService(self._session).resolve_sot_instance(
            module_id=_MODULE_ID,
            owner_kind=OWNER_PROJECT,
            owner_id=project_id,
        )
        if inst is None:
            return []
        from prodavan.application.modules.equipment_catalog_opensearch import (
            resolve_equipment_catalog_tenancy,
        )

        os_company_id, _, _ = await resolve_equipment_catalog_tenancy(
            self._session, inst=inst
        )
        rows = await ModuleInstanceService(self._session).list_data_rows(
            instance_id=inst.id, table_slug=_CATALOGS
        )
        out: list[dict[str, Any]] = []
        allow = {str(x) for x in catalog_ids} if catalog_ids else None
        for row in rows:
            body = row.get("body") if isinstance(row.get("body"), dict) else {}
            rid = str(row.get("row_id") or "").strip()
            if not rid:
                continue
            if allow is not None and rid not in allow:
                continue
            if str(body.get("status") or "").strip().lower() != "ready":
                continue
            if bool(body.get("paused")):
                continue
            if not _project_allowed(body, project_id):
                continue
            out.append(
                {
                    "id": rid,
                    "name": str(body.get("name") or rid),
                    "kind": str(body.get("source_kind") or "local"),
                    "row_count": body.get("row_count"),
                    "index_name": body.get("index_name")
                    or f"{OS_NAMESPACE}__{catalog_os_index_name(rid)}",
                    "last_indexed_at": body.get("last_indexed_at"),
                    "column_map": body.get("column_map")
                    if isinstance(body.get("column_map"), dict)
                    else {},
                    "os_company_id": os_company_id,
                }
            )
        return out

    async def catalog_sources(
        self, *, bridge: PodBridgeClaims, project_id: str
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        items = await self._ready_catalogs(project_id=project_id)
        return {
            "items": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "kind": c["kind"],
                    "row_count": c.get("row_count"),
                    "index_name": c.get("index_name"),
                    "last_indexed_at": c.get("last_indexed_at"),
                }
                for c in items
            ]
        }

    async def catalog_search(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        query: str | None = None,
        part_number: str | None = None,
        brand: str | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        in_stock_only: bool = True,
        catalog_ids: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        limit = max(1, min(int(limit or 20), 100))
        offset = max(0, int(offset or 0))
        catalogs = await self._ready_catalogs(
            project_id=project_id, catalog_ids=catalog_ids
        )
        if not catalogs:
            return {
                "items": [],
                "hits": [],
                "total": 0,
                "total_returned": 0,
                "limit": limit,
                "offset": offset,
                "sort": ["match_rank", "price_asc"],
                "in_stock_only": in_stock_only,
                "catalogs": [],
            }

        os_query = _build_os_query(
            query=query,
            part_number=part_number,
            brand=brand,
            price_min=price_min,
            price_max=price_max,
            in_stock_only=in_stock_only,
            catalog_ids=None,  # already filtered by which indexes we hit
        )
        svc = get_search_index_service()
        # Over-fetch per catalog then merge (simple MVP fan-out).
        per = min(max(limit + offset, limit), 200)
        hits: list[CatalogHit] = []
        want_pn = (part_number or "").strip()
        want_q = (query or "").strip()
        for cat in catalogs:
            index = catalog_os_index_name(str(cat["id"]))
            try:
                result = await svc.search(
                    namespace=OS_NAMESPACE,
                    index=index,
                    query=os_query,
                    from_=0,
                    size=per,
                    # Tenancy for metrics/auth only. Document company_id may be
                    # "platform" (admin seed) while SoT is cabinet/company — ACL is
                    # already enforced by ready-catalog resolution + bridge scopes.
                    company_id=str(
                        cat.get("os_company_id") or bridge.company_id or "platform"
                    ),
                    cabinet_id=None,
                    project_id=None,
                    apply_tenant_filter=False,
                    session=self._session,
                )
            except AppError:
                logger.exception("equipment catalog search failed index=%s", index)
                continue
            except Exception:
                logger.exception("equipment catalog search failed index=%s", index)
                continue
            for hit in result.hits:
                doc = hit.source if isinstance(hit.source, dict) else {}
                values = {
                    "part_number": str(doc.get("part_number") or ""),
                    "title": str(doc.get("title") or ""),
                    "brand": str(doc.get("brand") or ""),
                    "price": str(doc.get("price") or ""),
                    "supplier": str(doc.get("supplier") or ""),
                    "lead_time": str(doc.get("lead_time") or ""),
                }
                price_num = doc.get("price_num")
                if not isinstance(price_num, (int, float)):
                    price_num = parse_price(values.get("price"))
                in_stock = bool(doc.get("in_stock"))
                rank = _match_rank(
                    part_number=values["part_number"],
                    title=values["title"],
                    want_pn=want_pn,
                    want_q=want_q,
                )
                hits.append(
                    CatalogHit(
                        catalog_id=str(doc.get("catalog_id") or cat["id"]),
                        source_catalog=str(doc.get("source_catalog") or cat["name"]),
                        values=values,
                        match_rank=rank,
                        in_stock=in_stock,
                        price_num=float(price_num) if price_num is not None else None,
                    )
                )

        def _sort_key(h: CatalogHit) -> tuple:
            price_key = h.price_num if h.price_num is not None else float("inf")
            return (h.match_rank, price_key, h.values.get("title") or "")

        hits.sort(key=_sort_key)
        total = len(hits)
        page = hits[offset : offset + limit]
        items = [h.as_dict() for h in page]
        return {
            "items": items,
            "hits": items,
            "total": total,
            "total_returned": len(items),
            "limit": limit,
            "offset": offset,
            "sort": ["match_rank", "price_asc"],
            "in_stock_only": in_stock_only,
            "catalogs": [c["id"] for c in catalogs],
        }


class TenantSearchService:
    """Generic Pod OpenSearch query surface (namespace/index ACL via SearchIndexService)."""

    def _require(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_SEARCH)

    async def query(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        namespace: str,
        index: str,
        query: dict[str, Any] | None = None,
        from_: int = 0,
        size: int = 20,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        ns = (namespace or "").strip()
        idx = (index or "").strip()
        if not ns or not idx:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="namespace and index are required",
            )
        # Equipment catalogs only for MVP allowlist (project-bound indexes).
        if ns != OS_NAMESPACE:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail=f"namespace not allowed for pod search: {ns}",
            )
        if not idx.startswith("c_"):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="index not allowed for pod search",
            )
        if session is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="session required for equipment catalog ACL",
            )
        # Same ACL as catalog-search: index must map to a ready catalog visible to project.
        pod = EquipmentCatalogPodSearchService(session)
        ready = await pod._ready_catalogs(project_id=project_id)
        allowed = {str(c["id"]) for c in ready}
        allowed_indexes = {catalog_os_index_name(cid) for cid in allowed}
        if idx not in allowed_indexes:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="catalog index not visible for this project",
            )
        size = max(1, min(int(size or 20), 100))
        from_ = max(0, int(from_ or 0))
        svc = get_search_index_service()
        result = await svc.search(
            namespace=ns,
            index=idx,
            query=query,
            from_=from_,
            size=size,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=None,
            session=session,
        )
        return {
            "hits": [
                {"id": h.doc_id, "score": h.score, "document": h.source} for h in result.hits
            ],
            "total": result.total,
            "took_ms": result.took_ms,
        }

    async def health(
        self, *, bridge: PodBridgeClaims, project_id: str
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        from prodavan.core.infra.opensearch_manager import get_opensearch_manager

        mgr = get_opensearch_manager()
        if mgr is None:
            return {"ok": False, "enabled": False, "reason": "opensearch_manager_missing"}
        return {
            "ok": bool(mgr.enabled),
            "enabled": bool(mgr.enabled),
            "url": getattr(mgr, "url", None) or getattr(mgr, "_url", None),
        }
