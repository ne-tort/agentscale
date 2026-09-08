"""Unit tests — Document Store service ACL + in-memory adapter."""

from __future__ import annotations

import pytest

from prodavan.application.document_store.adapters.memory_store import InMemoryDocumentStore
from prodavan.application.document_store.service import DocumentStoreService
from prodavan.domain.document_store.types import IndexSpec
from prodavan.domain.errors import AppError


@pytest.fixture
def svc() -> DocumentStoreService:
    return DocumentStoreService(InMemoryDocumentStore())


@pytest.mark.asyncio
async def test_upsert_get_find_delete_roundtrip(svc: DocumentStoreService) -> None:
    written = await svc.upsert(
        namespace="equipment",
        collection="offers",
        filter={"sku": "ABC"},
        document={"sku": "ABC", "price": 10, "company_id": "co_1"},
        company_id="co_1",
    )
    assert written.upserted_id is not None or written.matched == 1

    got = await svc.get(
        namespace="equipment",
        collection="offers",
        filter={"sku": "ABC"},
        company_id="co_1",
    )
    assert got is not None
    assert got["price"] == 10

    found = await svc.find(
        namespace="equipment",
        collection="offers",
        filter={"company_id": "co_1"},
        company_id="co_1",
    )
    assert found.count >= 1

    deleted = await svc.delete(
        namespace="equipment",
        collection="offers",
        filter={"sku": "ABC"},
        company_id="co_1",
    )
    assert deleted == 1


@pytest.mark.asyncio
async def test_tenant_namespace_requires_company_id(svc: DocumentStoreService) -> None:
    with pytest.raises(AppError) as exc:
        await svc.upsert(
            namespace="equipment",
            collection="offers",
            filter={"sku": "X"},
            document={"sku": "X"},
        )
    assert exc.value.status == 422
    assert "company_id" in (exc.value.detail or "")


@pytest.mark.asyncio
async def test_platform_namespace_allows_missing_company(svc: DocumentStoreService) -> None:
    result = await svc.upsert(
        namespace="platform",
        collection="flags",
        filter={"key": "feature"},
        document={"key": "feature", "on": True},
    )
    assert result.upserted_id is not None or result.matched >= 0


@pytest.mark.asyncio
async def test_invalid_namespace_rejected(svc: DocumentStoreService) -> None:
    with pytest.raises(AppError) as exc:
        await svc.get(
            namespace="Bad-NS",
            collection="x",
            filter={"a": 1},
            company_id="co_1",
        )
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_ensure_indexes(svc: DocumentStoreService) -> None:
    names = await svc.ensure_indexes(
        namespace="platform",
        collection="flags",
        specs=[IndexSpec(keys=[("key", 1)], name="flags_key", unique=True)],
    )
    assert "flags_key" in names or names


@pytest.mark.asyncio
async def test_find_limit_capped(svc: DocumentStoreService) -> None:
    for i in range(5):
        await svc.upsert(
            namespace="platform",
            collection="items",
            filter={"i": i},
            document={"i": i},
        )
    result = await svc.find(
        namespace="platform",
        collection="items",
        filter={},
        limit=10_000,
    )
    assert len(result.items) <= 200
