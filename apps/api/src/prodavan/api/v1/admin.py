"""Admin endpoints (platform.admin only)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import CurrentUser, get_session, require_platform_admin
from prodavan.application.dto.admin import (
    AdminCreateUserRequest,
    AdminStatsResponse,
    AdminUpdateUserRequest,
    AdminUserResponse,
)
from prodavan.application.services import admin_service
from prodavan.application.services.auth_service import AuthError

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin_error(exc: AuthError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


def _to_user(row: admin_service.AdminUserRow) -> AdminUserResponse:
    u = row.user
    return AdminUserResponse(
        id=u.id,
        login_id=u.login_id,
        company_name=u.company_name,
        contact_person=u.contact_person,
        phone=u.phone,
        email=u.email,
        role=u.role,
        status=u.status,
        tenant_id=row.tenant_id,
        tenant_slug=row.tenant_slug,
        created_at=u.created_at,
        deleted_at=u.deleted_at,
    )


@router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    _: CurrentUser = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUserResponse]:
    rows = await admin_service.list_users(session)
    return [_to_user(r) for r in rows]


@router.post("/users", response_model=AdminUserResponse, status_code=201)
async def create_user(
    body: AdminCreateUserRequest,
    _: CurrentUser = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserResponse:
    try:
        row = await admin_service.create_user(
            session,
            login_id=body.login_id,
            company_name=body.company_name,
            password=body.password,
            contact_person=body.contact_person,
            phone=body.phone,
            email=str(body.email) if body.email else None,
        )
    except AuthError as exc:
        raise _admin_error(exc) from exc
    return _to_user(row)


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: UUID,
    _: CurrentUser = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserResponse:
    try:
        row = await admin_service.get_user(session, user_id)
    except AuthError as exc:
        raise _admin_error(exc) from exc
    return _to_user(row)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def patch_user(
    user_id: UUID,
    body: AdminUpdateUserRequest,
    _: CurrentUser = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserResponse:
    fields = body.model_fields_set
    try:
        row = await admin_service.update_user(
            session,
            user_id=user_id,
            status=body.status,
            contact_person=body.contact_person,
            phone=body.phone,
            email=str(body.email) if body.email else None,
            password=body.password,
            fields_set=fields,
        )
    except AuthError as exc:
        raise _admin_error(exc) from exc
    return _to_user(row)


@router.delete("/users/{user_id}", response_model=AdminUserResponse)
async def delete_user(
    user_id: UUID,
    _: CurrentUser = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserResponse:
    try:
        row = await admin_service.soft_delete_user(session, user_id=user_id)
    except AuthError as exc:
        raise _admin_error(exc) from exc
    return _to_user(row)


@router.get("/stats", response_model=AdminStatsResponse)
async def stats(
    _: CurrentUser = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminStatsResponse:
    data = await admin_service.get_stats(session)
    return AdminStatsResponse(
        users_total=data.users_total,
        users_by_status=data.users_by_status,
        tenants_total=data.tenants_total,
        cabinets_total=data.cabinets_total,
        projects_total=data.projects_total,
        runs_total=data.runs_total,
    )
