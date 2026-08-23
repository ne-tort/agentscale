"""Platform Admin metrics HTTP (L04 / L09)."""

from __future__ import annotations

from fastapi import APIRouter

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.admin.company_service import AdminCompanyService

router = APIRouter(prefix="/admin/metrics", tags=["admin-metrics"])


@router.get("/companies")
async def list_companies_metrics(_: PlatformAdminDep, session: SessionDep) -> dict:
    items = await AdminCompanyService(session).list_companies_metrics()
    return {"items": items}
