"""In-memory SearchIndex adapter — unit tests / disabled OpenSearch."""

from __future__ import annotations

import copy
from typing import Any

from prodavan.domain.search_index.types import (
    BulkIndexItem,
    BulkIndexResult,
    IndexDocumentResult,
    IndexMappingSpec,
    IndexResult,
    SearchHit,
    SearchResult,
    physical_index,
)


def _match_filter(doc: dict[str, Any], filt: dict[str, Any] | None) -> bool:
    if not filt:
        return True
    for key, want in filt.items():
        if doc.get(key) != want:
            return False
    return True


class InMemorySearchIndexStore:
    """Equality-filter store for tests."""

    def __init__(self) -> None:
        self._indexes: dict[str, IndexMappingSpec] = {}
        self._docs: dict[str, dict[str, dict[str, Any]]] = {}

    async def ping(self) -> bool:
        return True

    def _bucket(self, namespace: str, index: str) -> dict[str, dict[str, Any]]:
        key = physical_index(namespace, index)
        return self._docs.setdefault(key, {})

    async def ensure_index(
        self,
        *,
        namespace: str,
        index: str,
        spec: IndexMappingSpec,
    ) -> IndexResult:
        key = physical_index(namespace, index)
        created = key not in self._indexes
        self._indexes[key] = IndexMappingSpec(
            mappings=copy.deepcopy(spec.mappings),
            settings=copy.deepcopy(spec.settings),
        )
        self._docs.setdefault(key, {})
        return IndexResult(index=key, created=created, acknowledged=True)

    async def delete_index(self, *, namespace: str, index: str) -> bool:
        key = physical_index(namespace, index)
        existed = key in self._indexes or key in self._docs
        self._indexes.pop(key, None)
        self._docs.pop(key, None)
        return existed

    async def list_indexes(
        self,
        *,
        namespace: str | None = None,
        company_id: str | None = None,
    ) -> list[str]:
        keys = sorted(self._indexes.keys())
        if namespace:
            prefix = f"{namespace}__"
            keys = [k for k in keys if k.startswith(prefix)]
        if company_id:
            filtered: list[str] = []
            for key in keys:
                docs = self._docs.get(key, {})
                if any(d.get("company_id") == company_id for d in docs.values()):
                    filtered.append(key)
                    continue
                # Empty indexes still count toward company quota via mappings._meta.
                meta = self._indexes.get(key)
                owned = (meta.mappings.get("_meta") or {}).get("company_id") if meta else None
                if owned == company_id:
                    filtered.append(key)
            return filtered
        return keys

    async def index_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        document: dict[str, Any],
        refresh: bool = False,
    ) -> IndexDocumentResult:
        _ = refresh
        bucket = self._bucket(namespace, index)
        existed = doc_id in bucket
        bucket[doc_id] = copy.deepcopy(document)
        return IndexDocumentResult(
            doc_id=doc_id,
            result="updated" if existed else "created",
            version=1,
        )

    async def bulk_index(
        self,
        *,
        namespace: str,
        index: str,
        documents: list[tuple[str, dict[str, Any]]],
        refresh: bool = False,
    ) -> BulkIndexResult:
        items: list[BulkIndexItem] = []
        indexed = 0
        for doc_id, document in documents:
            result = await self.index_document(
                namespace=namespace,
                index=index,
                doc_id=doc_id,
                document=document,
                refresh=refresh,
            )
            items.append(BulkIndexItem(doc_id=doc_id, document=document, result=result.result))
            indexed += 1
        return BulkIndexResult(items=items, indexed=indexed, errors=0)

    async def get_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
    ) -> dict[str, Any] | None:
        doc = self._bucket(namespace, index).get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    async def delete_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        refresh: bool = False,
    ) -> bool:
        _ = refresh
        bucket = self._bucket(namespace, index)
        if doc_id not in bucket:
            return False
        del bucket[doc_id]
        return True

    async def search(
        self,
        *,
        namespace: str,
        index: str,
        query: dict[str, Any],
        from_: int,
        size: int,
        filter: dict[str, Any] | None = None,
    ) -> SearchResult:
        _ = query
        matched = [
            (doc_id, doc)
            for doc_id, doc in self._bucket(namespace, index).items()
            if _match_filter(doc, filter)
        ]
        total = len(matched)
        sliced = matched[from_ : from_ + size]
        hits = [
            SearchHit(doc_id=doc_id, score=1.0, source=copy.deepcopy(doc))
            for doc_id, doc in sliced
        ]
        return SearchResult(hits=hits, total=total, took_ms=0)

    async def count(
        self,
        *,
        namespace: str,
        index: str,
        filter: dict[str, Any] | None = None,
    ) -> int:
        return sum(
            1 for doc in self._bucket(namespace, index).values() if _match_filter(doc, filter)
        )
