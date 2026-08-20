"""HTTP facade for Cabinet SPI (health/manifest/migrate)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.cabinets.events import spi_context
from prodavan.cabinets.registry import get_module_for_profile
from prodavan.infrastructure.persistence.models.tenants import Cabinet

router = APIRouter(tags=["cabinet-spi"])


async def _profile_id(session: AsyncSession, cabinet_id: uuid.UUID) -> str | None:
    result = await session.execute(select(Cabinet.profile_id).where(Cabinet.id == cabinet_id))
    return result.scalar_one_or_none()


@router.get("/cabinets/{cabinet_id}/spi/health")
async def spi_health(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    if cs.ctx.cabinet_id != cabinet_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    profile_id = await _profile_id(cs.session, cabinet_id)
    module = get_module_for_profile(profile_id)
    h = module.health()
    return {"status": h.status, "pack_id": h.pack_id, "pack_version": h.pack_version}


@router.get("/cabinets/{cabinet_id}/spi/manifest")
async def spi_manifest(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    if cs.ctx.cabinet_id != cabinet_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    profile_id = await _profile_id(cs.session, cabinet_id)
    module = get_module_for_profile(profile_id)
    ctx = spi_context(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
    )
    return await module.manifest(ctx)


@router.post("/cabinets/{cabinet_id}/spi/migrate")
async def spi_migrate(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    if cs.ctx.cabinet_id != cabinet_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    profile_id = await _profile_id(cs.session, cabinet_id)
    module = get_module_for_profile(profile_id)
    ctx = spi_context(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
    )
    return await module.migrate(ctx)
