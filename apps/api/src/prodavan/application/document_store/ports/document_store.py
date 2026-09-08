"""DocumentStorePort — async document CRUD behind Document Store BC."""

from __future__ import annotations

from typing import Any, Protocol

from prodavan.domain.document_store.types import FindResult, IndexSpec, WriteResult


class DocumentStorePort(Protocol):
    async def ping(self) -> bool: ...

    async def upsert(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        document: dict[str, Any],
    ) -> WriteResult: ...

    async def get(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def find(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
        limit: int,
        skip: int,
    ) -> FindResult: ...

    async def delete(
        self,
        *,
        namespace: str,
        collection: str,
        filter: dict[str, Any],
    ) -> int: ...

    async def ensure_indexes(
        self,
        *,
        namespace: str,
        collection: str,
        specs: list[IndexSpec],
    ) -> list[str]: ...
