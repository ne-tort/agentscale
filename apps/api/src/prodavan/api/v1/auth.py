"""Authentication endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import get_session
from prodavan.application.dto.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TenantResponse,
    TokenResponse,
    UserResponse,
)
from prodavan.application.services.auth_service import AuthError, login, refresh, register

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_error(exc: AuthError) -> HTTPException:
    status = 401
    if exc.code in {"EMAIL_OR_SLUG_TAKEN"}:
        status = 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


@router.post("/register", response_model=TokenResponse)
async def register_user(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        result = await register(
            session,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            tenant_slug=body.tenant_slug,
            tenant_display_name=body.tenant_display_name,
        )
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return _to_response(result)


@router.post("/login", response_model=TokenResponse)
async def login_user(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        result = await login(session, email=body.email, password=body.password)
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


def _to_response(result) -> TokenResponse:
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        user=UserResponse(
            id=result.user.id,
            email=result.user.email,
            display_name=result.user.display_name,
        ),
        tenants=[
            TenantResponse(id=t.id, slug=t.slug, display_name=t.display_name)
            for t in result.tenants
        ],
    )
