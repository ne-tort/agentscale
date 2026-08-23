"""Starter bundle catalog HTTP (L04) — read-only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from prodavan.api.deps import PlatformAdminDep, PrincipalDep, get_current_employee
from prodavan.application.admin.starter_bundle_service import StarterBundleCatalogService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/starter-bundles", tags=["starter-bundles"])
admin_router = APIRouter(prefix="/admin/starter-bundles", tags=["admin-starter-bundles"])

EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


@router.get("")
async def list_starter_bundles(
    principal: PrincipalDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None and not principal.is_platform_admin:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return {"items": StarterBundleCatalogService().list_entries()}


@router.get("/{bundle_id}/bundle")
async def download_starter_bundle(
    bundle_id: str,
    principal: PrincipalDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None and not principal.is_platform_admin:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return StarterBundleCatalogService().export_base64(bundle_id)


@admin_router.get("")
async def admin_list_starter_bundles(_: PlatformAdminDep) -> dict:
    return {"items": StarterBundleCatalogService().list_entries()}


@admin_router.get("/{bundle_id}/bundle")
async def admin_download_starter_bundle(bundle_id: str, _: PlatformAdminDep) -> dict:
    return StarterBundleCatalogService().export_base64(bundle_id)
