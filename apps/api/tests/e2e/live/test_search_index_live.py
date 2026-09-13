"""Live Search Index BC — admin ensure/index/search/delete against OpenSearch."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import requires_live_api
from tests.e2e.live.keycloak_auth import auth_header, fetch_platform_admin_token

pytestmark = [pytest.mark.live, requires_live_api]


def test_live_search_index_roundtrip(live_client, live_api_prefix: str) -> None:
    ready = live_client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    resources = body.get("resources") or {}
    if "opensearch" in resources:
        assert resources["opensearch"] == "ok", body

    api = live_api_prefix
    admin_tok = fetch_platform_admin_token(live_client, api)
    headers = auth_header(admin_tok)

    health = live_client.get(f"{api}/admin/search-index/health", headers=headers)
    assert health.status_code == 200, health.text
    assert health.json().get("status") == "ok"

    suffix = uuid.uuid4().hex[:8]
    namespace = "platform"
    index = f"e2e_{suffix}"
    doc_id = f"doc-{suffix}"

    ensure = live_client.post(
        f"{api}/admin/search-index/indexes/ensure",
        headers=headers,
        json={
            "namespace": namespace,
            "index": index,
            "mappings": {
                "properties": {
                    "title": {"type": "text"},
                    "sku": {"type": "keyword"},
                }
            },
        },
    )
    assert ensure.status_code == 200, ensure.text
    assert ensure.json().get("index") == f"{namespace}__{index}"

    indexed = live_client.post(
        f"{api}/admin/search-index/documents/index",
        headers=headers,
        json={
            "namespace": namespace,
            "index": index,
            "doc_id": doc_id,
            "document": {"title": "Search Index E2E", "sku": "E2E-1"},
            "refresh": True,
        },
    )
    assert indexed.status_code == 200, indexed.text

    searched = live_client.post(
        f"{api}/admin/search-index/search",
        headers=headers,
        json={
            "namespace": namespace,
            "index": index,
            "query": {"match": {"title": "E2E"}},
            "size": 10,
        },
    )
    assert searched.status_code == 200, searched.text
    hits = searched.json().get("hits") or []
    assert any(h.get("doc_id") == doc_id for h in hits), searched.text

    deleted_doc = live_client.post(
        f"{api}/admin/search-index/documents/delete",
        headers=headers,
        json={
            "namespace": namespace,
            "index": index,
            "doc_id": doc_id,
            "refresh": True,
        },
    )
    assert deleted_doc.status_code == 200, deleted_doc.text
    assert deleted_doc.json().get("deleted") is True

    deleted_idx = live_client.post(
        f"{api}/admin/search-index/indexes/delete",
        headers=headers,
        json={"namespace": namespace, "index": index},
    )
    assert deleted_idx.status_code == 200, deleted_idx.text
    assert deleted_idx.json().get("deleted") is True
