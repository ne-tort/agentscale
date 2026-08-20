"""Platform host helpers: resolve cabinet module without importing domain packages."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.cabinets.registry import get_module_for_profile
from prodavan.cabinets.spi import CabinetModule, SpiContext
from prodavan.infrastructure.persistence.models.tenants import Cabinet


async def load_cabinet(session: AsyncSession, cabinet_id: uuid.UUID) -> Cabinet:
    result = await session.execute(select(Cabinet).where(Cabinet.id == cabinet_id))
    cabinet = result.scalar_one_or_none()
    if cabinet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cabinet not found")
    return cabinet


def module_for_cabinet(cabinet: Cabinet) -> CabinetModule:
    return get_module_for_profile(cabinet.profile_id)


def spi_ctx_from(
    *,
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    project_id: str | None = None,
) -> SpiContext:
    return SpiContext(
        tenant_id=tenant_id,
        cabinet_id=cabinet_id,
        user_id=user_id,
        project_id=project_id,
    )


def require_raw_capability(cabinet: Cabinet, capability: str) -> None:
    caps = cabinet.capabilities or {}
    raw = caps.get("raw") or []
    modules = caps.get("modules") or {}
    # Prefer snapshot modules for known gates
    if capability == "procurement.pipeline":
        if not (modules.get("pipeline") or {}).get("enabled"):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "CABINET_MODULE_UNSUPPORTED",
                    "message": "This cabinet has no procurement pipeline module",
                },
            )
        return
    if capability == "procurement.s4b":
        if not (caps.get("integrations") or {}).get("s4b", {}).get("enabled"):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "CABINET_MODULE_UNSUPPORTED",
                    "message": "This cabinet has no S4B integration",
                },
            )
        return
    if capability not in raw:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CABINET_MODULE_UNSUPPORTED",
                "message": f"Capability {capability} not enabled for this cabinet",
            },
        )
