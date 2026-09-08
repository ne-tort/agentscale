"""MongoDB DocumentStore adapter (motor)."""

from __future__ import annotations

from typing import Any

from prodavan.domain.document_store.types import (
    FindResult,
    IndexSpec,
    WriteResult,
    physical_collection,
)


class MongoDocumentStore:
    def __init__(self, *, db: Any) -> None:
        self._db = db

    def _coll(self, namespace: str, collection: str) -> Any:
        return self._db[physical_collection(namespace, collection)]

    async def ping(self) -> bool:
        await self._db.command("ping")
        return True

    async def upsert(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        document: dict[str, Any],
    ) -> WriteResult:
        coll = self._coll(namespace, collection)
        # `$set` body without clobbering filter identity fields unintentionally —
        # callers pass the full desired document fields in `document`.
        payload = {k: v for k, v in document.items() if k != "_id"}
        result = await coll.update_one(filter, {"$set": payload}, upsert=True)
        upserted = str(result.upserted_id) if result.upserted_id is not None else None
        return WriteResult(
            matched=int(result.matched_count),
            modified=int(result.modified_count),
            upserted_id=upserted,
        )

    async def get(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
    ) -> dict[str, Any] | None:
        doc = await self._coll(namespace, collection).find_one(filter)
        if doc is None:
            return None
        return _serialize(doc)

    async def find(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        limit: int,
        skip: int,
    ) -> FindResult:
        coll = self._coll(namespace, collection)
        cursor = coll.find(filter).skip(skip).limit(limit)
        items = [_serialize(doc) async for doc in cursor]
        total = await coll.count_documents(filter)
        return FindResult(items=items, count=int(total))

    async def delete(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
    ) -> int:
        result = await self._coll(namespace, collection).delete_many(filter)
        return int(result.deleted_count)

    async def ensure_indexes(
        self,
        *,
        namespace: str,
        collection: str,
        specs: list[IndexSpec],
    ) -> list[str]:
        coll = self._coll(namespace, collection)
        names: list[str] = []
        for spec in specs:
            name = await coll.create_index(
                spec.keys,
                name=spec.name,
                unique=spec.unique,
            )
            names.append(str(name))
        return names


def _serialize(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    return out
