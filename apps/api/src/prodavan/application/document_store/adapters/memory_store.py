"""In-memory DocumentStore adapter — unit tests / disabled Mongo."""

from __future__ import annotations

import copy
from typing import Any

from prodavan.domain.document_store.types import (
    FindResult,
    IndexSpec,
    WriteResult,
    physical_collection,
)


def _match(doc: dict[str, Any], filt: dict[str, Any]) -> bool:
    for key, want in filt.items():
        if doc.get(key) != want:
            return False
    return True


class InMemoryDocumentStore:
    """Simple equality-filter store for tests."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._indexes: dict[str, list[IndexSpec]] = {}
        self._seq = 0

    async def ping(self) -> bool:
        return True

    def _bucket(self, namespace: str, collection: str) -> list[dict[str, Any]]:
        key = physical_collection(namespace, collection)
        return self._data.setdefault(key, [])

    async def upsert(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        document: dict[str, Any],
    ) -> WriteResult:
        bucket = self._bucket(namespace, collection)
        for i, row in enumerate(bucket):
            if _match(row, filter):
                merged = {**row, **copy.deepcopy(document)}
                bucket[i] = merged
                return WriteResult(matched=1, modified=1, upserted_id=None)
        self._seq += 1
        new_id = f"mem_{self._seq}"
        row = {"_id": new_id, **copy.deepcopy(filter), **copy.deepcopy(document)}
        bucket.append(row)
        return WriteResult(matched=0, modified=0, upserted_id=new_id)

    async def get(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
    ) -> dict[str, Any] | None:
        for row in self._bucket(namespace, collection):
            if _match(row, filter):
                return copy.deepcopy(row)
        return None

    async def find(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        limit: int,
        skip: int,
    ) -> FindResult:
        matched = [copy.deepcopy(r) for r in self._bucket(namespace, collection) if _match(r, filter)]
        sliced = matched[skip : skip + limit]
        return FindResult(items=sliced, count=len(matched))

    async def delete(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
    ) -> int:
        bucket = self._bucket(namespace, collection)
        keep = [r for r in bucket if not _match(r, filter)]
        deleted = len(bucket) - len(keep)
        self._data[physical_collection(namespace, collection)] = keep
        return deleted

    async def ensure_indexes(
        self,
        *,
        namespace: str,
        collection: str,
        specs: list[IndexSpec],
    ) -> list[str]:
        key = physical_collection(namespace, collection)
        self._indexes[key] = list(specs)
        return [s.name or f"idx_{i}" for i, s in enumerate(specs)]
