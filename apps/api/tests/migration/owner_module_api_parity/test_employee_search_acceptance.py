"""Acceptance contract for employee-authenticated tenant search.

These checks document the employee facade during the migration verification
freeze and are intentionally not executed here.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.search_index.adapters.memory_store import InMemorySearchIndexStore
from prodavan.application.search_index.employee_query import EmployeeSearchQueryService
from prodavan.application.search_index.service import SearchIndexService
from prodavan.domain.identity import Principal


@pytest.mark.asyncio
async def test_employee_search_scopes_owner_and_project_rows_and_returns_cursor() -> None:
    store = InMemorySearchIndexStore()
    search = SearchIndexService(store)
    await search.ensure_index(namespace="company_content", index="catalog", company_id="company-1")
    await search.index_document(
        namespace="company_content",
        index="catalog",
        doc_id="owner-row",
        document={"title": "shared", "company_id": "company-1"},
        company_id="company-1",
    )
    await search.index_document(
        namespace="company_content",
        index="catalog",
        doc_id="project-row",
        document={"title": "project", "company_id": "company-1", "project_id": "project-1"},
        company_id="company-1",
        project_id="project-1",
    )
    await search.index_document(
        namespace="company_content",
        index="catalog",
        doc_id="sibling-row",
        document={"title": "sibling", "company_id": "company-1", "project_id": "project-2"},
        company_id="company-1",
        project_id="project-2",
    )
    await search.index_document(
        namespace="company_content",
        index="catalog",
        doc_id="other-company-row",
        document={"title": "foreign", "company_id": "company-2"},
        company_id="company-2",
    )
    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    employee = SimpleNamespace(id="employee-1")
    principal = Principal(sub="employee-1", username="employee@example.test")

    with (
        patch(
            "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
            new=AsyncMock(return_value=project),
        ),
        patch("prodavan.application.search_index.employee_query.get_search_index_service", return_value=search),
    ):
        query_service = EmployeeSearchQueryService(SimpleNamespace())
        first_page = await query_service.query(
            project_id="project-1",
            principal=principal,
            employee=employee,
            namespace="company_content",
            index="catalog",
            sort=[{"title": "asc"}],
            size=1,
        )
        second_page = await query_service.query(
            project_id="project-1",
            principal=principal,
            employee=employee,
            namespace="company_content",
            index="catalog",
            sort=[{"title": "asc"}],
            search_after=first_page["next_search_after"],
            size=1,
        )

    assert [hit["id"] for hit in first_page["hits"]] == ["owner-row"]
    assert first_page["next_search_after"] == ["shared"]
    assert [hit["id"] for hit in second_page["hits"]] == ["project-row"]
    assert second_page["next_search_after"] == ["project"]


@pytest.mark.asyncio
async def test_employee_search_allows_only_bound_platform_owned_equipment_catalogs() -> None:
    """A project may read a platform SoT catalog only through its module binding.

    Setup contract: project-1 has mod_equipment bound to cabinet-1, and the
    ready catalogs row `catalog-1` in that bound SoT maps to equipment/c_catalog_1.
    Employee access to that one index must use platform ownership, while an
    arbitrary/unbound catalog index must be rejected. The generic `platform`
    namespace remains forbidden; this is a narrowly scoped module capability,
    not a general platform namespace exception.
    """
    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    employee = SimpleNamespace(id="employee-1")
    principal = Principal(sub="employee-1", username="employee@example.test")
    search = AsyncMock(return_value=SimpleNamespace(hits=[], total=0, took_ms=1, next_search_after=None))

    with (
        patch(
            "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "prodavan.application.search_index.employee_query.ModuleInstanceService.resolve_sot_instance",
            new=AsyncMock(return_value=SimpleNamespace(id="equipment-sot")),
        ),
        patch(
            "prodavan.application.search_index.employee_query.ModuleInstanceService.list_data_rows",
            new=AsyncMock(
                return_value=[
                    {
                        "row_id": "catalog-1",
                        "body": {"status": "ready", "paused": False},
                    }
                ]
            ),
        ),
        patch("prodavan.application.search_index.employee_query.get_search_index_service", return_value=SimpleNamespace(search=search)),
    ):
        service = EmployeeSearchQueryService(SimpleNamespace())
        await service.query(
            project_id="project-1",
            principal=principal,
            employee=employee,
            namespace="equipment",
            index="c_catalog_1",
            size=10,
        )

    # This accepted module-bound index is platform-owned (company_id=platform),
    # unlike ordinary tenant indexes, while project ACL remains authoritative.
    assert search.await_args.kwargs["company_id"] == "platform"
    assert search.await_args.kwargs["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_employee_search_rejects_unbound_equipment_catalog() -> None:
    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    search = AsyncMock()
    with (
        patch(
            "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "prodavan.application.search_index.employee_query.ModuleInstanceService.resolve_sot_instance",
            new=AsyncMock(return_value=SimpleNamespace(id="equipment-sot")),
        ),
        patch(
            "prodavan.application.search_index.employee_query.ModuleInstanceService.list_data_rows",
            new=AsyncMock(
                return_value=[
                    {
                        "row_id": "catalog-1",
                        "body": {"status": "ready", "paused": False},
                    }
                ]
            ),
        ),
        patch("prodavan.application.search_index.employee_query.get_search_index_service", return_value=SimpleNamespace(search=search)),
    ):
        with pytest.raises(Exception, match="equipment catalog index not visible"):
            await EmployeeSearchQueryService(SimpleNamespace()).query(
                project_id="project-1",
                principal=Principal(sub="employee-1", username="employee@example.test"),
                employee=SimpleNamespace(id="employee-1"),
                namespace="equipment",
                index="c_catalog_2",
            )
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_employee_search_rejects_ambiguous_equipment_index_alias() -> None:
    """Distinct ready catalog rows may not share one normalized index name."""
    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    search = AsyncMock()
    shared_prefix = "a" * 63
    aliased_index = "c_" + "a" * 62
    with (
        patch(
            "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "prodavan.application.search_index.employee_query.ModuleInstanceService.resolve_sot_instance",
            new=AsyncMock(return_value=SimpleNamespace(id="equipment-sot")),
        ),
        patch(
            "prodavan.application.search_index.employee_query.ModuleInstanceService.list_data_rows",
            new=AsyncMock(
                return_value=[
                    {"row_id": f"{shared_prefix}-one", "body": {"status": "ready"}},
                    {"row_id": f"{shared_prefix}-two", "body": {"status": "ready"}},
                ]
            ),
        ),
        patch("prodavan.application.search_index.employee_query.get_search_index_service", return_value=SimpleNamespace(search=search)),
    ):
        with pytest.raises(Exception, match="equipment catalog index not visible"):
            await EmployeeSearchQueryService(SimpleNamespace()).query(
                project_id="project-1",
                principal=Principal(sub="employee-1", username="employee@example.test"),
                employee=SimpleNamespace(id="employee-1"),
                namespace="equipment",
                index=aliased_index,
            )
    search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["platform", "system", "search_index"])
async def test_employee_search_rejects_platform_namespaces(namespace: str) -> None:
    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    with patch(
        "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
        new=AsyncMock(return_value=project),
    ):
        with pytest.raises(Exception, match="namespace not allowed"):
            await EmployeeSearchQueryService(SimpleNamespace()).query(
                project_id="project-1",
                principal=Principal(sub="employee-1", username="employee@example.test"),
                employee=SimpleNamespace(id="employee-1"),
                namespace=namespace,
                index="catalog",
            )
