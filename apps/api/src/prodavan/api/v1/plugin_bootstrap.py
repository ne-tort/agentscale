"""Company-facing plugin runtime bootstrap endpoints.

Private bootstrap requires an authenticated employee with active company membership.
Public bootstrap serves only pages explicitly declared ``public`` in the manifest and
never exposes grant tokens, DSN or admin receipts.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from prodavan.api.deps import get_current_employee
from prodavan.application.plugins.bootstrap.contracts import PluginBootstrapResponse
from prodavan.application.plugins.bootstrap.service import PluginBootstrapService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.database import get_session_factory
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter()


def _service() -> PluginBootstrapService:
    return PluginBootstrapService(get_session_factory())


@router.get("/companies/{company_id}/plugins/{plugin_id}/bootstrap", response_model=PluginBootstrapResponse)
async def private_bootstrap(
    company_id: str,
    plugin_id: str,
    route_path: str = Query(default="/"),
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
):
    if employee is None:
        raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="Authentication required")
    return await _service().private(
        employee=employee, company_id=company_id, plugin_id=plugin_id, route_path=route_path
    )


@router.get("/public/plugins/{company_id}/{plugin_id}/bootstrap", response_model=PluginBootstrapResponse)
async def public_bootstrap(company_id: str, plugin_id: str, route_path: str = Query(default="/")):
    return await _service().public(company_id=company_id, plugin_id=plugin_id, route_path=route_path)
