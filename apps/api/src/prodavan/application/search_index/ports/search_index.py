"""SearchIndexPort — async index/search behind Search Index BC."""

from __future__ import annotations

from typing import Any, Protocol

from prodavan.domain.search_index.types import (
    BulkIndexResult,
    IndexDocumentResult,
    IndexMappingSpec,
    IndexResult,
    SearchResult,
)


class SearchIndexPort(Protocol):
    async def ping(self) -> bool: ...

    async def ensure_index(
        self,
        *,
        namespace: str,
        index: str,
        spec: IndexMappingSpec,
    ) -> IndexResult: ...

    async def delete_index(
        self,
        *,
        namespace: str,
        index: str,
    ) -> bool: ...

    async def list_indexes(
        self,
        *,
        namespace: str | None = None,
        company_id: str | None = None,
    ) -> list[str]: ...

    async def index_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        document: dict[str, Any],
        refresh: bool = False,
    ) -> IndexDocumentResult: ...

    async def bulk_index(
        self,
        *,
        namespace: str,
        index: str,
        documents: list[tuple[str, dict[str, Any]]],
        refresh: bool = False,
    ) -> BulkIndexResult: ...

    async def get_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
    ) -> dict[str, Any] | None: ...

    async def delete_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        refresh: bool = False,
    ) -> bool: ...

    async def search(
        self,
        *,
        namespace: str,
        index: str,
        query: dict[str, Any],
        from_: int,
        size: int,
        filter: dict[str, Any] | None = None,
    ) -> SearchResult: ...

    async def count(
        self,
        *,
        namespace: str,
        index: str,
        filter: dict[str, Any] | None = None,
    ) -> int: ...
