"""Contracts for employee-authenticated Search Index access."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.search_index.adapters.memory_store import InMemorySearchIndexStore
from prodavan.application.search_index.employee_query import EmployeeSearchQueryService
from prodavan.application.search_index.service import SearchIndexService


@pytest.mark.asyncio
async def test_employee_search_includes_company_owner_docs_without_widening_project_acl() -> None:
    search = SearchIndexService(InMemorySearchIndexStore())
    await search.ensure_index(
        namespace="tenant_docs", index="records", company_id="company-1"
    )
    await search.index_document(
        namespace="tenant_docs",
        index="records",
        company_id="company-1",
        cabinet_id="cabinet-1",
        doc_id="owner",
        document={"title": "shared owner row"},
    )
    await search.index_document(
        namespace="tenant_docs",
        index="records",
        company_id="company-1",
        cabinet_id="cabinet-1",
        project_id="project-1",
        doc_id="current-project",
        document={"title": "current project row"},
    )
    await search.index_document(
        namespace="tenant_docs",
        index="records",
        company_id="company-1",
        cabinet_id="cabinet-1",
        project_id="project-2",
        doc_id="sibling-project",
        document={"title": "sibling project row"},
    )
    await search.index_document(
        namespace="tenant_docs",
        index="records",
        company_id="company-2",
        doc_id="other-company",
        document={"title": "other company row"},
    )

    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    with (
        patch(
            "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "prodavan.application.search_index.employee_query.get_search_index_service",
            return_value=search,
        ),
    ):
        result = await EmployeeSearchQueryService(None).query(
            project_id="project-1",
            principal=SimpleNamespace(),
            employee=SimpleNamespace(),
            namespace="tenant_docs",
            index="records",
        )

    assert {hit["id"] for hit in result["hits"]} == {"owner", "current-project"}
    assert result["total"] == 2
