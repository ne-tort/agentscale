"""Employee-authenticated tenant Search Index query surface."""

from __future__ import annotations

from typing import Any, Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.search_index.employee_query import EmployeeSearchQueryService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(tags=["employee-search"])


class EmployeeSearchQueryBody(BaseModel):
    namespace: str = Field(min_length=1, max_length=32)
    index: str = Field(min_length=1, max_length=64)
    query: dict[str, Any] | None = None
    filter: dict[str, Any] = Field(default_factory=dict)
    sort: list[dict[str, Any]] | None = None
    search_after: list[Any] | None = None
    from_: int = Field(default=0, ge=0, alias="from")
    size: int = Field(default=20, ge=1, le=200)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/projects/{project_id}/search/query")
async def employee_search_query(
    project_id: str,
    body: EmployeeSearchQueryBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict[str, Any]:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await EmployeeSearchQueryService(session).query(
        project_id=project_id,
        principal=principal,
        employee=employee,
        namespace=body.namespace,
        index=body.index,
        query=body.query,
        filter=body.filter,
        sort=body.sort,
        search_after=body.search_after,
        from_=body.from_,
        size=body.size,
    )
