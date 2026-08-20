"""Authentication endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import get_session
from prodavan.application.dto.auth import (
    LoginRequest,
    RefreshRequest,
    TenantResponse,
    TokenResponse,
    UserResponse,
)
from prodavan.application.services.auth_service import AuthError, AuthResult, login, refresh

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post("/register", status_code=410)
async def register_closed() -> dict:
    """Public self-registration is disabled; accounts are created by platform.admin."""
    raise HTTPException(
        status_code=410,
        detail={
            "code": "REGISTER_DISABLED",
            "message": "Public registration is closed. Contact platform administrator.",
        },
    )


@router.post("/login", response_model=TokenResponse)
async def login_user(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        result = await login(session, login_id=body.login_id, password=body.password)
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return _to_response(result)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        result = await refresh(session, refresh_token=body.refresh_token)
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return _to_response(result)


def user_response_from_info(user) -> UserResponse:
    return UserResponse(
        id=user.id,
        login_id=user.login_id,
        company_name=user.company_name,
        contact_person=user.contact_person,
        phone=user.phone,
        email=user.email,
        role=user.role,
        status=user.status,
        display_name=user.display_name
        if hasattr(user, "display_name")
        else (user.contact_person or user.company_name),
    )


def _to_response(result: AuthResult) -> TokenResponse:
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        user=user_response_from_info(result.user),
        tenants=[
            TenantResponse(id=t.id, slug=t.slug, display_name=t.display_name)
            for t in result.tenants
        ],
    )
