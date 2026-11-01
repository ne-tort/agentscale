"""Acceptance contracts for generic Search Index access through the Pod Bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.pod_identity.bridge import (
    SCOPE_INFRA_SEARCH,
    PodBridgeClaims,
)
from prodavan.application.search_index.adapters.memory_store import InMemorySearchIndexStore
from prodavan.application.search_index.service import SearchIndexService
from prodavan.application.tenant_infra.equipment_catalog_search_service import TenantSearchService
from prodavan.domain.errors import AppError


def _bridge() -> PodBridgeClaims:
    return PodBridgeClaims(
        project_id="project-1",
        cabinet_id="cabinet-1",
        company_id="company-1",
        pod_id="pod-1",
        gen=0,
        scopes=(SCOPE_INFRA_SEARCH,),
        jti="jti",
        exp=2_000_000_000,
    )


@pytest.mark.asyncio
async def test_bridge_search_uses_company_and_project_acl_and_cursor() -> None:
    store = InMemorySearchIndexStore()
    svc = SearchIndexService(store)
    await svc.ensure_index(
        namespace="tenant_docs",
        index="records",
        mappings={"properties": {"title": {"type": "keyword"}}},
        company_id="company-1",
    )
    await svc.bulk_index(
        namespace="tenant_docs",
        index="records",
        company_id="company-1",
        project_id="project-1",
        documents=[
            {"doc_id": "a", "document": {"title": "A"}},
            {"doc_id": "b", "document": {"title": "B"}},
        ],
    )

    with patch(
        "prodavan.application.tenant_infra.equipment_catalog_search_service.get_search_index_service",
        return_value=svc,
    ):
        result = await TenantSearchService().query(
            bridge=_bridge(),
            project_id="project-1",
            namespace="tenant_docs",
            index="records",
            sort=[{"title": "asc"}],
            size=1,
        )
        assert result["total"] == 2
        assert result["hits"][0]["id"] == "a"
        assert result["next_search_after"] == ["A"]

        next_page = await TenantSearchService().query(
            bridge=_bridge(),
            project_id="project-1",
            namespace="tenant_docs",
            index="records",
            sort=[{"title": "asc"}],
            search_after=result["next_search_after"],
            size=1,
        )
        assert [item["id"] for item in next_page["hits"]] == ["b"]


@pytest.mark.asyncio
async def test_bridge_search_rejects_platform_namespace() -> None:
    with pytest.raises(AppError) as exc:
        await TenantSearchService().query(
            bridge=_bridge(),
            project_id="project-1",
            namespace="platform",
            index="flags",
            session=AsyncMock(),
        )
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_bridge_search_cannot_override_tenant_filter() -> None:
    store = InMemorySearchIndexStore()
    svc = SearchIndexService(store)
    await svc.ensure_index(
        namespace="tenant_docs",
        index="records",
        company_id="company-1",
    )
    await svc.index_document(
        namespace="tenant_docs",
        index="records",
        doc_id="owned",
        document={"title": "owned"},
        company_id="company-1",
        project_id="project-1",
    )
    await svc.index_document(
        namespace="tenant_docs",
        index="records",
        doc_id="other",
        document={"title": "other"},
        company_id="company-1",
        project_id="project-2",
    )
    result = await svc.search(
        namespace="tenant_docs",
        index="records",
        company_id="company-1",
        project_id="project-1",
        filter={"project_id": "project-2"},
    )
    assert result.total == 0
