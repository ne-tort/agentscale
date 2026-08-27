"""Public Auth Service HTTP API — Flutter never talks to Keycloak."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from prodavan.application.auth.service import (
    broker_app_redirect,
    get_auth_service,
    session_result_to_dict,
)
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutBody(BaseModel):
    refresh_token: str | None = None
    access_token: str | None = None
    id_token: str | None = None


def _callback_uri(request: Request) -> str:
    # Same-origin callback owned by API (Traefik / Flutter host).
    return str(request.url_for("auth_broker_callback"))


@router.get("/config")
async def auth_config() -> dict:
    """Client discovery — no Keycloak issuer/token/revoke URLs."""
    return get_auth_service().public_config().to_dict()


@router.get("/health")
async def auth_health() -> dict:
    return await get_auth_service().health()


@router.post("/login")
async def auth_login(body: LoginBody) -> dict:
    result = await get_auth_service().login(username=body.username, password=body.password)
    return session_result_to_dict(result)


@router.post("/refresh")
async def auth_refresh(body: RefreshBody) -> dict:
    result = await get_auth_service().refresh(refresh_token=body.refresh_token)
    return session_result_to_dict(result)


@router.post("/logout")
async def auth_logout(body: LogoutBody) -> Response:
    await get_auth_service().logout(
        refresh_token=body.refresh_token,
        access_token=body.access_token,
        id_token=body.id_token,
    )
    return Response(status_code=204)


@router.get("/broker/{idp}/start")
async def auth_broker_start(idp: str, request: Request, format: str = "redirect"):
    """Start Identity Broker (vk|yandex). Keycloak authorize is server-side only."""
    callback = _callback_uri(request)
    started = get_auth_service().start_broker(
        idp=idp,
        callback_uri=callback,
        response_mode=format,
    )
    if format == "json":
        return {"idp": started.idp, "redirect_url": started.redirect_url}
    return RedirectResponse(url=started.redirect_url, status_code=302)


@router.get("/broker/callback", name="auth_broker_callback")
async def auth_broker_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        raise AppError(
            code="INVALID_CREDENTIALS",
            title="Broker authentication failed",
            status=401,
            detail=error_description or error,
        )
    if not code or not state:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="code and state required",
        )
    callback = _callback_uri(request)
    result = await get_auth_service().complete_broker(
        code=code,
        state=state,
        callback_uri=callback,
    )
    app_redirect = settings.oidc_flutter_redirect_uri_desktop or settings.oidc_flutter_redirect_uri
    return RedirectResponse(
        url=broker_app_redirect(result, app_redirect=app_redirect),
        status_code=302,
    )
