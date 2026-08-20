"""Current user introspection and self-service profile."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import CurrentUser, get_current_user, get_session
from prodavan.application.dto.auth import (
    ChangePasswordRequest,
    MeResponse,
    TenantResponse,
    UpdateProfileRequest,
    UserResponse,
)
from prodavan.application.services.auth_service import AuthError, change_password, update_profile
from prodavan.infrastructure.persistence.models.tenants import Tenant, User
from prodavan.infrastructure.persistence.rls import apply_rls

router = APIRouter(tags=["auth"])


def _auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


def _orm_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        login_id=user.login_id,
        company_name=user.company_name,
        contact_person=user.contact_person,
        phone=user.phone,
        email=user.email,
        role=user.role,
        status=user.status,
        display_name=user.display_name,
    )


@router.get("/me", response_model=MeResponse)
async def get_me(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    await apply_rls(
        session,
        user_id=current.user_id,
        tenant_id=current.tenant_id,
    )
    user_result = await session.execute(select(User).where(User.id == current.user_id))
    user = user_result.scalar_one()
    tenant_result = await session.execute(select(Tenant).where(Tenant.id == current.tenant_id))
    tenant = tenant_result.scalar_one()
    return MeResponse(
        user=_orm_user_response(user),
        tenant=TenantResponse(id=tenant.id, slug=tenant.slug, display_name=tenant.display_name),
        cabinet_ids=current.cabinet_ids,
    )


@router.patch("/me/profile", response_model=UserResponse)
async def patch_me_profile(
    body: UpdateProfileRequest,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    await apply_rls(session, user_id=current.user_id, tenant_id=current.tenant_id)
    try:
        user = await update_profile(
            session,
            user_id=current.user_id,
            contact_person=body.contact_person,
            phone=body.phone,
            email=str(body.email) if body.email else None,
            fields_set=body.model_fields_set,
        )
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return _orm_user_response(user)


@router.post("/me/password", status_code=204)
async def post_me_password(
    body: ChangePasswordRequest,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await apply_rls(session, user_id=current.user_id, tenant_id=current.tenant_id)
    try:
        await change_password(
            session,
            user_id=current.user_id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except AuthError as exc:
        raise _auth_error(exc) from exc
