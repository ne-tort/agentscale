"""Acceptance contract for the employee-authenticated generic search facade.

These checks document the route boundary during migration and are intentionally
not executed in this implementation round.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.api.v1.employee_search import (
    EmployeeSearchQueryBody,
    employee_search_query,
    router,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


@pytest.mark.asyncio
async def test_employee_facade_forwards_query_filter_sort_and_cursor_with_employee_acl() -> None:
    employee = SimpleNamespace(id="employee-1")
    principal = Principal(sub="employee-1", username="employee@example.test")
    session = SimpleNamespace()
    body = EmployeeSearchQueryBody(
        namespace="tenant_docs",
        index="records",
        query={"match": {"title": "quarterly"}},
        filter={"status": "active"},
        sort=[{"title": "asc"}],
        search_after=["Quarterly report"],
        **{"from": 0},
        size=25,
    )
    expected = {"hits": [], "total": 0, "took_ms": 1, "next_search_after": None}

    with patch(
        "prodavan.api.v1.employee_search.EmployeeSearchQueryService.query",
        new=AsyncMock(return_value=expected),
    ) as query:
        response = await employee_search_query(
            project_id="project-1",
            body=body,
            principal=principal,
            session=session,
            employee=employee,
        )

    assert response == expected
    query.assert_awaited_once_with(
        project_id="project-1",
        principal=principal,
        employee=employee,
        namespace="tenant_docs",
        index="records",
        query={"match": {"title": "quarterly"}},
        filter={"status": "active"},
        sort=[{"title": "asc"}],
        search_after=["Quarterly report"],
        from_=0,
        size=25,
    )


@pytest.mark.asyncio
async def test_employee_facade_rejects_non_employee_bearer_principal() -> None:
    with patch(
        "prodavan.api.v1.employee_search.EmployeeSearchQueryService.query",
        new=AsyncMock(),
    ) as query:
        with pytest.raises(AppError) as exc:
            await employee_search_query(
                project_id="project-1",
                body=EmployeeSearchQueryBody(namespace="tenant_docs", index="records"),
                principal=Principal(sub="bridge-pod", username="bridge-pod"),
                session=SimpleNamespace(),
                employee=None,
            )

    assert exc.value.status == 403
    query.assert_not_awaited()


def test_employee_search_route_is_separate_from_bridge_infra_endpoint() -> None:
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/search/query" in paths
    assert "/projects/{project_id}/infra/search/query" not in paths
