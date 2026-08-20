"""HTTP facade for Cabinet SPI (health/manifest/migrate/commands/queries)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.cabinets.events import spi_context
from prodavan.cabinets.registry import get_module_for_profile
from prodavan.cabinets.spi import CabinetDomainError
from prodavan.infrastructure.persistence.models.tenants import Cabinet

router = APIRouter(tags=["cabinet-spi"])

# Commands that require binary upload stay on domain facades (specs/catalogs).
_HTTP_COMMAND_BLOCKLIST = frozenset({"upload_inbox", "upload_catalog"})


async def _profile_id(session: AsyncSession, cabinet_id: uuid.UUID) -> str | None:
    result = await session.execute(select(Cabinet.profile_id).where(Cabinet.id == cabinet_id))
    return result.scalar_one_or_none()


def _domain_http(exc: CabinetDomainError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


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


@router.post("/cabinets/{cabinet_id}/spi/commands/{name}")
async def spi_command(
    cabinet_id: uuid.UUID,
    name: str,
    body: dict[str, Any] | None = None,
    project_id: str | None = Query(default=None),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    """In-process SPI command bridge (session injected by platform host)."""
    if cs.ctx.cabinet_id != cabinet_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    if name in _HTTP_COMMAND_BLOCKLIST:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "USE_DOMAIN_FACADE",
                "message": f"Command {name} requires multipart upload; use /projects/... or /catalogs",
            },
        )
    profile_id = await _profile_id(cs.session, cabinet_id)
    module = get_module_for_profile(profile_id)
    ctx = spi_context(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    payload = dict(body or {})
    payload["session"] = cs.session
    if project_id:
        payload.setdefault("project_id", project_id)
    payload.setdefault("active_cabinet_id", cabinet_id)
    try:
        return await module.execute_command(ctx, name, payload)
    except CabinetDomainError as exc:
        raise _domain_http(exc) from exc
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_COMMAND", "message": str(exc)},
        ) from exc


@router.get("/cabinets/{cabinet_id}/spi/queries/{name}")
async def spi_query(
    cabinet_id: uuid.UUID,
    name: str,
    project_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    filename: str | None = Query(default=None),
    catalog_id: str | None = Query(default=None),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    """In-process SPI query bridge."""
    if cs.ctx.cabinet_id != cabinet_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    profile_id = await _profile_id(cs.session, cabinet_id)
    module = get_module_for_profile(profile_id)
    ctx = spi_context(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    params: dict[str, Any] = {
        "session": cs.session,
        "active_cabinet_id": cabinet_id,
    }
    if project_id:
        params["project_id"] = project_id
    if run_id:
        params["run_id"] = run_id
    if filename:
        params["filename"] = filename
    if catalog_id:
        params["catalog_id"] = catalog_id
    try:
        return await module.execute_query(ctx, name, params)
    except CabinetDomainError as exc:
        raise _domain_http(exc) from exc
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_QUERY", "message": str(exc)},
        ) from exc
