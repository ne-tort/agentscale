"""B2B tender API: CRUD, server-side sort/filter, take-to-work, import trigger."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, PrincipalDep, SessionDep, get_current_employee
from prodavan.application.b2b.importer import B2BImportService
from prodavan.application.projects.project_runtime_module_service import (
    ProjectRuntimeModuleService,
)
from prodavan.application.tenders.workflow import TenderWorkflowService
from prodavan.domain.errors import AppError
from prodavan.domain.tenders import LIFECYCLE_STATUSES, WORK_STATUSES
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/b2b", tags=["b2b-tenders"])
MODULE_ID = "mod_equipment"
TENDERS_TABLE = "tenders"
EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


class TenderPatchBody(BaseModel):
    model_config = {"extra": "forbid"}

    work_status: str | None = Field(default=None, max_length=32)
    comment: str | None = Field(default=None, max_length=8000)
    relevance_note: str | None = Field(default=None, max_length=2000)
    project_ids: list[str] | None = None


class TakeToWorkBody(BaseModel):
    model_config = {"extra": "forbid"}

    project_name: str | None = Field(default=None, max_length=200)


@router.get("/projects/{project_id}/tenders")
async def list_tenders(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    lifecycle_status: str | None = Query(default=None),
    work_status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str = Query(default="", max_length=200),
    sort: str = Query(default="published_at"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    if lifecycle_status and lifecycle_status not in LIFECYCLE_STATUSES:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad lifecycle_status")
    if work_status and work_status not in WORK_STATUSES:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad work_status")
    rows = await ProjectRuntimeModuleService(session).list_data_rows(
        project_id=project_id,
        module_id=MODULE_ID,
        table_slug=TENDERS_TABLE,
        principal=principal,
        employee=employee,
    )
    items: list[dict[str, Any]] = []
    needle = q.strip().lower()
    for row in rows:
        body = row.get("body") if isinstance(row.get("body"), dict) else {}
        if lifecycle_status and body.get("lifecycle_status") != lifecycle_status:
            continue
        if work_status and body.get("work_status") != work_status:
            continue
        if category and body.get("category") != category:
            continue
        if needle:
            haystack = " ".join(
                str(body.get(k) or "") for k in ("title", "customer", "description", "number", "source_ref")
            ).lower()
            if needle not in haystack:
                continue
        items.append({"row_id": row.get("row_id"), **body, "updated_at": row.get("updated_at")})
    reverse = order == "desc"
    if sort in {"price_total", "positions_count", "found_pct", "sum_rub", "sum_margin_rub"}:
        items.sort(key=lambda x: float(x.get(sort) or 0), reverse=reverse)
    else:
        items.sort(key=lambda x: str(x.get(sort) or ""), reverse=reverse)
    total = len(items)
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": total, "page": page, "page_size": page_size}


@router.get("/projects/{project_id}/tenders/{tender_id}")
async def get_tender(
    project_id: str,
    tender_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict[str, Any]:
    rows = await ProjectRuntimeModuleService(session).list_data_rows(
        project_id=project_id,
        module_id=MODULE_ID,
        table_slug=TENDERS_TABLE,
        principal=principal,
        employee=employee,
    )
    row = next((r for r in rows if str(r.get("row_id")) == str(tender_id)), None)
    if row is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="tender not found")
    return {"row_id": row.get("row_id"), **(row.get("body") or {}), "updated_at": row.get("updated_at")}


@router.patch("/projects/{project_id}/tenders/{tender_id}")
async def patch_tender(
    project_id: str,
    tender_id: str,
    body: TenderPatchBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "work_status" in patch and patch["work_status"] not in WORK_STATUSES:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad work_status")
    return await ProjectRuntimeModuleService(session).update_data_row(
        project_id=project_id,
        module_id=MODULE_ID,
        table_slug=TENDERS_TABLE,
        row_id=tender_id,
        body=patch,
        principal=principal,
        employee=employee,
    )


@router.delete("/projects/{project_id}/tenders/{tender_id}")
async def delete_tender(
    project_id: str,
    tender_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict[str, Any]:
    return await ProjectRuntimeModuleService(session).delete_data_row(
        project_id=project_id,
        module_id=MODULE_ID,
        table_slug=TENDERS_TABLE,
        row_id=tender_id,
        principal=principal,
        employee=employee,
    )


@router.post("/projects/{project_id}/tenders/{tender_id}/take-to-work")
async def take_tender_to_work(
    project_id: str,
    tender_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    body: TakeToWorkBody | None = None,
) -> dict[str, Any]:
    return await TenderWorkflowService(session).take_to_work(
        project_id=project_id,
        tender_id=tender_id,
        principal=principal,
        employee=employee,
    )


@router.post("/import")
async def trigger_import(
    _: PlatformAdminDep,
    session: SessionDep,
    project_id: str | None = Query(default=None),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Admin fallback for environments without Celery beat."""
    return await B2BImportService(session).run(project_id=project_id, force=force)
