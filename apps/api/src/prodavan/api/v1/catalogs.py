"""Catalog and S4B credential endpoints (M04)."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from prodavan.api.deps import CabinetSession, CurrentUser, get_cabinet_session, get_current_user
from prodavan.application.services.catalog_service import (
    CatalogError,
    archive_catalog,
    delete_s4b_credentials,
    list_catalogs,
    list_system_databases,
    put_s4b_credentials,
    refuse_system_delete,
    s4b_status,
    upload_catalog,
)

router = APIRouter(tags=["catalogs"])


class S4BCredentialsRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _catalog_error(exc: CatalogError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/cabinets/{cabinet_id}/catalogs")
async def get_catalogs(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return await list_catalogs(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.post("/cabinets/{cabinet_id}/catalogs/upload", status_code=status.HTTP_202_ACCEPTED)
async def post_catalog_upload(
    cabinet_id: uuid.UUID,
    file: UploadFile = File(...),
    slug: str = Form(...),
    display_name: str = Form(...),
    trusted_seller: bool = Form(default=True),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    data = await file.read()
    try:
        return await upload_catalog(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
            filename=file.filename or "catalog.csv",
            data=data,
            slug=slug,
            display_name=display_name,
            trusted_seller=trusted_seller,
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.delete("/cabinets/{cabinet_id}/catalogs/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog(
    cabinet_id: uuid.UUID,
    catalog_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> None:
    try:
        await archive_catalog(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
            catalog_id=catalog_id,
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.get("/cabinets/{cabinet_id}/system-databases")
async def get_system_databases(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return await list_system_databases(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
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
async def get_s4b_status(current: CurrentUser = Depends(get_current_user)) -> dict:
    return s4b_status(current.tenant_id)


@router.put("/tenant/s4b-credentials")
async def put_s4b(
    body: S4BCredentialsRequest,
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    return put_s4b_credentials(current.tenant_id, body.username, body.password)


@router.delete("/tenant/s4b-credentials")
async def delete_s4b(current: CurrentUser = Depends(get_current_user)) -> dict:
    return delete_s4b_credentials(current.tenant_id)
