"""Catalog and S4B endpoints — platform facade over Cabinet SPI where applicable."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from prodavan.api.deps import CabinetSession, CurrentUser, get_cabinet_session, get_current_user
from prodavan.cabinets.electronics_procurement.services.catalog_service import (
    CatalogError,
    refuse_system_delete,
)
from prodavan.cabinets.host import (
    load_cabinet,
    module_for_cabinet,
    require_raw_capability,
    spi_ctx_from,
)
from prodavan.cabinets.spi import CabinetDomainError

router = APIRouter(tags=["catalogs"])


class S4BCredentialsRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _catalog_error(exc: CabinetDomainError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


async def _module(cs: CabinetSession, *, need_s4b: bool = False):
    cabinet = await load_cabinet(cs.session, cs.ctx.cabinet_id)
    # Catalogs user upload is electronics pack today; gate via pipeline or specs_kp
    caps = cabinet.capabilities or {}
    modules = caps.get("modules") or {}
    if not (
        (modules.get("pipeline") or {}).get("enabled")
        or (modules.get("specs_kp") or {}).get("enabled")
        or (modules.get("catalogs_user") or {}).get("enabled")
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CABINET_MODULE_UNSUPPORTED",
                "message": "This cabinet has no catalogs module",
            },
        )
    if need_s4b:
        require_raw_capability(cabinet, "procurement.s4b")
    return module_for_cabinet(cabinet)


@router.get("/cabinets/{cabinet_id}/catalogs")
async def get_catalogs(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
    )
    try:
        return await module.execute_query(
            ctx,
            "list_catalogs",
            {
                "session": cs.session,
                "active_cabinet_id": cs.ctx.cabinet_id,
            },
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "UNKNOWN_QUERY", "message": str(exc)}) from exc


@router.post("/cabinets/{cabinet_id}/catalogs/upload", status_code=status.HTTP_202_ACCEPTED)
async def post_catalog_upload(
    cabinet_id: uuid.UUID,
    file: UploadFile = File(...),
    slug: str = Form(...),
    display_name: str = Form(...),
    trusted_seller: bool = Form(default=True),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module = await _module(cs)
    data = await file.read()
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "upload_catalog",
            {
                "session": cs.session,
                "active_cabinet_id": cs.ctx.cabinet_id,
                "filename": file.filename or "catalog.csv",
                "data": data,
                "slug": slug,
                "display_name": display_name,
                "trusted_seller": trusted_seller,
            },
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.delete("/cabinets/{cabinet_id}/catalogs/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog(
    cabinet_id: uuid.UUID,
    catalog_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> None:
    module = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
    )
    try:
        await module.execute_command(
            ctx,
            "archive_catalog",
            {
                "session": cs.session,
                "active_cabinet_id": cs.ctx.cabinet_id,
                "catalog_id": catalog_id,
            },
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.get("/cabinets/{cabinet_id}/system-databases")
async def get_system_databases(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cabinet_id,
        user_id=cs.ctx.user.user_id,
    )
    try:
        return await module.execute_query(
            ctx,
            "list_system_databases",
            {
                "session": cs.session,
                "active_cabinet_id": cs.ctx.cabinet_id,
            },
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.delete("/system-databases/{system_id}")
async def delete_system_database(
    system_id: str,
    _current: CurrentUser = Depends(get_current_user),
) -> None:
    try:
        refuse_system_delete(system_id)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.get("/tenant/s4b-credentials/status")
async def get_s4b_status(
    current: CurrentUser = Depends(get_current_user),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module = await _module(cs, need_s4b=True)
    ctx = spi_ctx_from(
        tenant_id=current.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=current.user_id,
    )
    try:
        return await module.execute_query(ctx, "s4b_status", {"session": cs.session})
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    except KeyError:
        # Tenant-level vault without cabinet pack support
        from prodavan.cabinets.electronics_procurement.services.catalog_service import (
            s4b_status,
        )

        return s4b_status(current.tenant_id, cs.ctx.cabinet_id)


@router.put("/tenant/s4b-credentials")
async def put_s4b(
    body: S4BCredentialsRequest,
    current: CurrentUser = Depends(get_current_user),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module = await _module(cs, need_s4b=True)
    ctx = spi_ctx_from(
        tenant_id=current.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=current.user_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "put_s4b_credentials",
            {
                "session": cs.session,
                "username": body.username,
                "password": body.password,
            },
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.delete("/tenant/s4b-credentials")
async def delete_s4b(
    current: CurrentUser = Depends(get_current_user),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module = await _module(cs, need_s4b=True)
    ctx = spi_ctx_from(
        tenant_id=current.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=current.user_id,
    )
    try:
        return await module.execute_command(
            ctx, "delete_s4b_credentials", {"session": cs.session}
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
