"""Acceptance coverage for employee generic-search cursor contracts.

These checks are authored during the migration verification freeze and are
intentionally not executed in this implementation round.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.search_index.employee_query import EmployeeSearchQueryService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sort", "search_after", "from_", "expected_detail"),
    [
        (None, ["title-value"], 0, "sort is required"),
        ([{"title": "asc"}, {"created_at": "desc"}], ["title-value"], 0, "one value for each sort field"),
        ([{"title": "asc"}], ["title-value"], 1, "from must be zero"),
    ],
)
async def test_employee_search_rejects_invalid_cursor_before_backend(
    sort: list[dict[str, str]] | None,
    search_after: list[str],
    from_: int,
    expected_detail: str,
) -> None:
    project = SimpleNamespace(id="project-1", company_id="company-1", cabinet_id="cabinet-1")
    with (
        patch(
            "prodavan.application.search_index.employee_query.ProjectAccessPolicy.require_access",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "prodavan.application.search_index.employee_query.get_search_index_service",
            return_value=SimpleNamespace(search=AsyncMock()),
        ) as get_search,
    ):
        with pytest.raises(AppError) as exc:
            await EmployeeSearchQueryService(SimpleNamespace()).query(
                project_id="project-1",
                principal=Principal(sub="employee-1", username="employee@example.test"),
                employee=SimpleNamespace(id="employee-1"),
                namespace="company_content",
                index="catalog",
                sort=sort,
                search_after=search_after,
                from_=from_,
            )

    assert exc.value.status == 422
    assert expected_detail in (exc.value.detail or "")
    get_search.assert_not_called()
