"""OpenSearch HTTP adapter for Search Index BC (httpx)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

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

logger = logging.getLogger(__name__)


class OpenSearchStore:
    """Thin REST client — no ACL (enforced in SearchIndexService)."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._owns_client = client is None
        auth = None
        if username and password:
            auth = (username, password)
        self._client = client or httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(30.0, connect=5.0),
            auth=auth,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ping(self) -> bool:
        try:
            resp = await self._client.get("/_cluster/health")
            return resp.status_code < 500
        except Exception:
            logger.exception("opensearch ping failed")
            return False

    async def ensure_index(
        self,
        *,
        namespace: str,
        index: str,
        spec: IndexMappingSpec,
    ) -> IndexResult:
        name = physical_index(namespace, index)
        exists = await self._client.head(f"/{name}")
        body: dict[str, Any] = {}
        if spec.settings:
            body["settings"] = spec.settings
        if spec.mappings:
            body["mappings"] = spec.mappings
        if exists.status_code == 200:
            if spec.mappings.get("properties"):
                await self._client.put(
                    f"/{name}/_mapping",
                    json={"properties": spec.mappings["properties"]},
                )
            return IndexResult(index=name, created=False, acknowledged=True)
        resp = await self._client.put(f"/{name}", json=body if body else {})
        resp.raise_for_status()
        data = resp.json()
        return IndexResult(
            index=name,
            created=bool(data.get("acknowledged", True)),
            acknowledged=bool(data.get("acknowledged", True)),
        )

    async def delete_index(self, *, namespace: str, index: str) -> bool:
        name = physical_index(namespace, index)
        resp = await self._client.delete(f"/{name}")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    async def list_indexes(
        self,
        *,
        namespace: str | None = None,
        company_id: str | None = None,
    ) -> list[str]:
        _ = company_id
        pattern = f"{namespace}__*" if namespace else "*,-.*"
        resp = await self._client.get(f"/_cat/indices/{pattern}", params={"format": "json", "h": "index"})
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            return []
        return sorted(str(r.get("index")) for r in rows if r.get("index"))

    async def index_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        document: dict[str, Any],
        refresh: bool = False,
    ) -> IndexDocumentResult:
        name = physical_index(namespace, index)
        params = {"refresh": "true"} if refresh else None
        resp = await self._client.put(f"/{name}/_doc/{doc_id}", json=document, params=params)
        resp.raise_for_status()
        data = resp.json()
        return IndexDocumentResult(
            doc_id=str(data.get("_id") or doc_id),
            result=str(data.get("result") or "created"),
            version=data.get("_version"),
        )

    async def bulk_index(
        self,
        *,
        namespace: str,
        index: str,
        documents: list[tuple[str, dict[str, Any]]],
        refresh: bool = False,
    ) -> BulkIndexResult:
        name = physical_index(namespace, index)
        lines: list[str] = []
        for doc_id, document in documents:
            lines.append(json.dumps({"index": {"_index": name, "_id": doc_id}}, separators=(",", ":")))
            lines.append(json.dumps(document, separators=(",", ":")))
        payload = "\n".join(lines) + "\n"
        params = {"refresh": "true"} if refresh else None
        resp = await self._client.post(
            "/_bulk",
            content=payload,
            headers={"Content-Type": "application/x-ndjson"},
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        items_out: list[BulkIndexItem] = []
        indexed = 0
        errors = 0
        for entry in data.get("items") or []:
            action = entry.get("index") or entry.get("create") or {}
            doc_id = str(action.get("_id") or "")
            err = action.get("error")
            if err:
                errors += 1
                items_out.append(
                    BulkIndexItem(
                        doc_id=doc_id,
                        document={},
                        result="error",
                        error=str(err),
                    )
                )
            else:
                indexed += 1
                items_out.append(
                    BulkIndexItem(
                        doc_id=doc_id,
                        document={},
                        result=str(action.get("result") or "indexed"),
                    )
                )
        return BulkIndexResult(items=items_out, indexed=indexed, errors=errors)

    async def get_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
    ) -> dict[str, Any] | None:
        name = physical_index(namespace, index)
        resp = await self._client.get(f"/{name}/_doc/{doc_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if not data.get("found"):
            return None
        source = data.get("_source")
        return dict(source) if isinstance(source, dict) else None

    async def delete_document(
        self,
        *,
        namespace: str,
        index: str,
        doc_id: str,
        refresh: bool = False,
    ) -> bool:
        name = physical_index(namespace, index)
        params = {"refresh": "true"} if refresh else None
        resp = await self._client.delete(f"/{name}/_doc/{doc_id}", params=params)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
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
        name = physical_index(namespace, index)
        must: list[dict[str, Any]] = []
        if query:
            must.append(query if "query" not in query else query["query"])
        else:
            must.append({"match_all": {}})
        filter_clauses: list[dict[str, Any]] = []
        for key, value in (filter or {}).items():
            filter_clauses.append({"term": {key: value}})
        body: dict[str, Any] = {
            "from": from_,
            "size": size,
            "query": {
                "bool": {
                    "must": must,
                    "filter": filter_clauses,
                }
            },
        }
        resp = await self._client.post(f"/{name}/_search", json=body)
        resp.raise_for_status()
        data = resp.json()
        hits_raw = ((data.get("hits") or {}).get("hits")) or []
        total_raw = (data.get("hits") or {}).get("total")
        if isinstance(total_raw, dict):
            total = int(total_raw.get("value") or 0)
        else:
            total = int(total_raw or 0)
        hits = [
            SearchHit(
                doc_id=str(h.get("_id") or ""),
                score=h.get("_score"),
                source=dict(h.get("_source") or {}),
            )
            for h in hits_raw
        ]
        return SearchResult(hits=hits, total=total, took_ms=data.get("took"))

    async def count(
        self,
        *,
        namespace: str,
        index: str,
        filter: dict[str, Any] | None = None,
    ) -> int:
        name = physical_index(namespace, index)
        filter_clauses = [{"term": {k: v}} for k, v in (filter or {}).items()]
        body: dict[str, Any] = {
            "query": {"bool": {"filter": filter_clauses}} if filter_clauses else {"match_all": {}}
        }
        resp = await self._client.post(f"/{name}/_count", json=body)
        if resp.status_code == 404:
            return 0
        resp.raise_for_status()
        return int((resp.json() or {}).get("count") or 0)
