"""Acceptance contract for Bridge search of owner-indexed shared content.

These checks document the owner-indexing/search integration during the
migration verification freeze and are intentionally not executed here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_SEARCH, PodBridgeClaims
from prodavan.application.search_index.adapters.memory_store import InMemorySearchIndexStore
from prodavan.application.search_index.service import SearchIndexService
from prodavan.application.tenant_infra.equipment_catalog_search_service import TenantSearchService


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
async def test_bridge_search_includes_company_owner_documents_but_not_other_tenants() -> None:
    """Owner indexing omits project_id; current-project search must include it."""
    svc = SearchIndexService(InMemorySearchIndexStore())
    await svc.ensure_index(namespace="company_content", index="catalog", company_id="company-1")
    await svc.index_document(
        namespace="company_content",
        index="catalog",
        company_id="company-1",
        doc_id="owner-row",
        document={"title": "company-owned catalog row"},
    )
    await svc.index_document(
        namespace="company_content",
        index="catalog",
        company_id="company-1",
        project_id="project-1",
        doc_id="current-project-row",
        document={"title": "project catalog row"},
    )
    await svc.index_document(
        namespace="company_content",
        index="catalog",
        doc_id="sibling-project-row",
        document={"title": "sibling project row"},
        company_id="company-1",
        project_id="project-2",
    )
    await svc.index_document(
        namespace="company_content",
        index="catalog",
        doc_id="other-company-row",
        document={"title": "other company row"},
        company_id="company-2",
    )

    with patch(
        "prodavan.application.tenant_infra.equipment_catalog_search_service.get_search_index_service",
        return_value=svc,
    ):
        result = await TenantSearchService().query(
            bridge=_bridge(),
            project_id="project-1",
            namespace="company_content",
            index="catalog",
            session=None,
        )

    assert {hit["id"] for hit in result["hits"]} == {
        "owner-row",
        "current-project-row",
    }
    assert result["total"] == 2
