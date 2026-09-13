"""Unit tests — Search Index service ACL + in-memory adapter."""

from __future__ import annotations

import pytest

from prodavan.application.search_index.adapters.memory_store import InMemorySearchIndexStore
from prodavan.application.search_index.service import SearchIndexService
from prodavan.domain.errors import AppError
from prodavan.domain.search_index.types import MAX_SEARCH_SIZE


@pytest.fixture
def svc() -> SearchIndexService:
    return SearchIndexService(InMemorySearchIndexStore())


@pytest.mark.asyncio
async def test_ensure_index_document_search_delete_roundtrip(svc: SearchIndexService) -> None:
    ensured = await svc.ensure_index(
        namespace="equipment",
        index="offers",
        mappings={"properties": {"sku": {"type": "keyword"}, "title": {"type": "text"}}},
        company_id="co_1",
    )
    assert ensured.index == "equipment__offers"
    assert ensured.created is True

    written = await svc.index_document(
        namespace="equipment",
        index="offers",
        doc_id="sku-1",
        document={"sku": "ABC", "title": "Widget"},
        company_id="co_1",
        refresh=True,
    )
    assert written.result in {"created", "updated"}

    got = await svc.get_document(
        namespace="equipment",
        index="offers",
        doc_id="sku-1",
        company_id="co_1",
    )
    assert got is not None
    assert got["sku"] == "ABC"
    assert got["company_id"] == "co_1"

    found = await svc.search(
        namespace="equipment",
        index="offers",
        query={},
        company_id="co_1",
    )
    assert found.total >= 1
    assert any(h.doc_id == "sku-1" for h in found.hits)

    deleted = await svc.delete_document(
        namespace="equipment",
        index="offers",
        doc_id="sku-1",
        company_id="co_1",
    )
    assert deleted is True


@pytest.mark.asyncio
async def test_tenant_namespace_requires_company_id(svc: SearchIndexService) -> None:
    with pytest.raises(AppError) as exc:
        await svc.index_document(
            namespace="equipment",
            index="offers",
            doc_id="x",
            document={"sku": "X"},
        )
    assert exc.value.status == 422
    assert "company_id" in (exc.value.detail or "")


@pytest.mark.asyncio
async def test_platform_namespace_allows_missing_company(svc: SearchIndexService) -> None:
    result = await svc.ensure_index(
        namespace="platform",
        index="flags",
        mappings={"properties": {"key": {"type": "keyword"}}},
    )
    assert result.created is True
    written = await svc.index_document(
        namespace="platform",
        index="flags",
        doc_id="feature",
        document={"key": "feature", "on": True},
    )
    assert written.doc_id == "feature"


@pytest.mark.asyncio
async def test_invalid_namespace_rejected(svc: SearchIndexService) -> None:
    with pytest.raises(AppError) as exc:
        await svc.get_document(
            namespace="Bad-NS",
            index="x",
            doc_id="a",
            company_id="co_1",
        )
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_bulk_index_and_search_size_cap(svc: SearchIndexService) -> None:
    await svc.ensure_index(namespace="platform", index="items")
    docs = [{"doc_id": f"i-{i}", "document": {"i": i}} for i in range(5)]
    bulk = await svc.bulk_index(namespace="platform", index="items", documents=docs)
    assert bulk.indexed == 5

    result = await svc.search(
        namespace="platform",
        index="items",
        query={},
        size=10_000,
    )
    assert len(result.hits) <= MAX_SEARCH_SIZE


@pytest.mark.asyncio
async def test_tenant_ensure_stores_company_meta(svc: SearchIndexService) -> None:
    result = await svc.ensure_index(
        namespace="equipment",
        index="offers",
        mappings={"properties": {"sku": {"type": "keyword"}}},
        company_id="co_1",
    )
    assert result.created is True
    store = svc._store  # type: ignore[attr-defined]
    assert isinstance(store, InMemorySearchIndexStore)
    meta = store._indexes["equipment__offers"].mappings.get("_meta") or {}
    assert meta.get("company_id") == "co_1"
    listed = await store.list_indexes(namespace="equipment", company_id="co_1")
    assert "equipment__offers" in listed


@pytest.mark.asyncio
async def test_tenant_index_quota(svc: SearchIndexService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.application.search_index.service.MAX_INDEXES_PER_COMPANY",
        2,
    )
    await svc.ensure_index(namespace="equipment", index="a", company_id="co_q")
    await svc.ensure_index(namespace="equipment", index="b", company_id="co_q")
    with pytest.raises(AppError) as exc:
        await svc.ensure_index(namespace="equipment", index="c", company_id="co_q")
    assert exc.value.status == 429


@pytest.mark.asyncio
async def test_delete_index(svc: SearchIndexService) -> None:
    await svc.ensure_index(namespace="platform", index="tmp")
    assert await svc.delete_index(namespace="platform", index="tmp") is True
    assert await svc.delete_index(namespace="platform", index="tmp") is False
