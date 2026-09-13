"""Live Search Index BC — admin ensure/index/search/delete against OpenSearch."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import requires_live_api
from tests.e2e.live.keycloak_auth import auth_header, fetch_platform_admin_token

pytestmark = [pytest.mark.live, requires_live_api]


def _roundtrip(
    live_client,
    *,
    api: str,
    headers: dict[str, str],
    namespace: str,
    company_id: str | None,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    index = f"e2e_{suffix}"
    doc_id = f"doc-{suffix}"
    body_base = {"namespace": namespace, "index": index}
    if company_id:
        body_base["company_id"] = company_id

    ensure = live_client.post(
        f"{api}/admin/search-index/indexes/ensure",
        headers=headers,
        json={
            **body_base,
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
            **body_base,
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
            **body_base,
            "query": {"match": {"title": "E2E"}},
            "size": 10,
        },
    )
    assert searched.status_code == 200, searched.text
    hits = searched.json().get("hits") or []
    assert any(h.get("doc_id") == doc_id for h in hits), searched.text

    got = live_client.post(
        f"{api}/admin/search-index/documents/get",
        headers=headers,
        json={**body_base, "doc_id": doc_id},
    )
    assert got.status_code == 200, got.text
    doc = got.json().get("document") or {}
    assert doc.get("sku") == "E2E-1"
    if company_id:
        assert doc.get("company_id") == company_id

    deleted_doc = live_client.post(
        f"{api}/admin/search-index/documents/delete",
        headers=headers,
        json={**body_base, "doc_id": doc_id, "refresh": True},
    )
    assert deleted_doc.status_code == 200, deleted_doc.text
    assert deleted_doc.json().get("deleted") is True

    deleted_idx = live_client.post(
        f"{api}/admin/search-index/indexes/delete",
        headers=headers,
        json=body_base,
    )
    assert deleted_idx.status_code == 200, deleted_idx.text
    assert deleted_idx.json().get("deleted") is True


def test_live_search_index_platform_roundtrip(live_client, live_api_prefix: str) -> None:
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

    _roundtrip(
        live_client,
        api=api,
        headers=headers,
        namespace="platform",
        company_id=None,
    )


def test_live_search_index_tenant_roundtrip(live_client, live_api_prefix: str) -> None:
    ready = live_client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    resources = (ready.json().get("resources") or {})
    if "opensearch" in resources:
        assert resources["opensearch"] == "ok", ready.text

    api = live_api_prefix
    admin_tok = fetch_platform_admin_token(live_client, api)
    headers = auth_header(admin_tok)

    _roundtrip(
        live_client,
        api=api,
        headers=headers,
        namespace="equipment",
        company_id=f"co_e2e_{uuid.uuid4().hex[:8]}",
    )
