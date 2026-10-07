"""«Поиск товаров» — виртуальная таблица: ручной поиск по всем готовым
каталогам OpenSearch (без фильтра поставщиков мастер-прайса).

Строки не хранятся в БД модуля: серверная пагинация/поиск/фильтр наличия;
используется и для сопоставления найденного товара с позицией заказчика
(fetch_by_src_hash)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_catalog_opensearch import (
    OS_NAMESPACE,
    catalog_os_index_name,
)
from prodavan.application.modules.equipment_master_price import (
    doc_to_master_row,
)
from prodavan.domain.search_index.types import MAX_SEARCH_SIZE

PAGE_SIZE_MAX = 200
FROM_MAX = 10000 - PAGE_SIZE_MAX

# Ключ-курсор: src_hash уникален в каталоге (supplier|title).
SEARCH_SORT = [{"src_hash": {"order": "asc"}}]


def build_equipment_search_query(search: str, in_stock_only: bool) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = []
    if in_stock_only:
        clauses.append({"term": {"in_stock": True}})
    term = (search or "").strip()
    if term:
        clauses.append(
            {
                "bool": {
                    "should": [
                        {"match": {"title": {"query": term, "operator": "and"}}},
                        {"term": {"part_number": term}},
                        {"term": {"part_number": term.upper()}},
                        {"term": {"part_number": term.lower()}},
                        {"term": {"brand": term}},
                        {"wildcard": {"supplier": {"value": f"*{term}*"}}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    if not clauses:
        return {"match_all": {}}
    return {"bool": {"filter": clauses}}


class EquipmentSearchService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        principal: Any = None,
        employee: Any = None,
    ) -> None:
        self._session = session
        self._principal = principal
        self._employee = employee

    async def _ready_indexes(self, cabinet_id: str) -> list[str]:
        from prodavan.application.cabinets.cabinet_module_service import (
            CabinetModuleService,
        )

        rows = await CabinetModuleService(self._session).list_data_rows(
            cabinet_id=cabinet_id,
            module_id="mod_equipment",
            table_slug="catalogs",
            principal=self._principal,
            employee=self._employee,
        )
        out: list[str] = []
        for row in rows:
            raw_body = row.get("body")
            body: dict[str, Any] = raw_body if isinstance(raw_body, dict) else {}
            if str(body.get("status") or "").strip().lower() != "ready":
                continue
            out.append(catalog_os_index_name(str(row.get("row_id") or "")))
        return out

    async def list_page(
        self,
        *,
        cabinet_id: str,
        search: str = "",
        in_stock_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        size = max(1, min(int(page_size or 50), PAGE_SIZE_MAX))
        page = max(1, int(page or 1))
        from_ = (page - 1) * size
        if from_ > FROM_MAX:
            return {"items": [], "total": 0, "page": page, "page_size": size}
        indexes = await self._ready_indexes(cabinet_id)
        if not indexes:
            return {"items": [], "total": 0, "page": page, "page_size": size}
        svc = get_search_index_service()
        query = build_equipment_search_query(search, in_stock_only)
        items: list[dict[str, Any]] = []
        total = 0
        for index in indexes:
            result = await svc.search(
                namespace=OS_NAMESPACE,
                index=index,
                query=query,
                from_=from_,
                size=size,
                company_id="platform",
                cabinet_id=None,
                project_id=None,
                apply_tenant_filter=False,
                session=self._session,
                sort=SEARCH_SORT,
            )
            total += result.total
            items.extend(doc_to_master_row(h.source) for h in result.hits)
        items.sort(key=lambda r: r["src_hash"])
        return {"items": items, "total": total, "page": page, "page_size": size}

    async def fetch_by_src_hash(
        self, *, cabinet_id: str, src_hash: str
    ) -> dict[str, Any] | None:
        """OS-док позиции каталога по стабильному src_hash (для сопоставления)."""
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        key = (src_hash or "").strip()
        if not key:
            return None
        svc = get_search_index_service()
        for index in await self._ready_indexes(cabinet_id):
            result = await svc.search(
                namespace=OS_NAMESPACE,
                index=index,
                query={"term": {"src_hash": key}},
                from_=0,
                size=min(1, MAX_SEARCH_SIZE),
                company_id="platform",
                cabinet_id=None,
                project_id=None,
                apply_tenant_filter=False,
                session=self._session,
            )
            if result.hits:
                return dict(result.hits[0].source)
        return None