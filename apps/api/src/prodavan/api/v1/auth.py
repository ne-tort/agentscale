"""Public auth discovery (L01). Password login is Keycloak ROPC via API proxy."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response

from prodavan.config.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _issuer_base() -> str | None:
    if settings.keycloak_issuer_url:
        return settings.keycloak_issuer_url.rstrip("/")
    if settings.keycloak_url:
        return f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
    return None


def _token_url_in_cluster() -> str | None:
    """Keycloak token URL reachable from the API pod (not hostPort)."""
    if settings.keycloak_url:
        base = settings.keycloak_url.rstrip("/")
        return f"{base}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"
    issuer = _issuer_base()
    if issuer:
        return f"{issuer}/protocol/openid-connect/token"
    return None


@router.get("/config")
async def auth_config() -> dict:
    """Flutter / clients discover auth_mode and public OIDC endpoints."""
    mode = settings.auth_mode.strip().lower()
    issuer = _issuer_base()
    oidc: dict | None = None
    if issuer:
        oidc = {
            "issuer": issuer,
            "audience": settings.oidc_audience,
            "client_id": settings.oidc_flutter_client_id,
            "realm": settings.keycloak_realm,
            "discovery_url": f"{issuer}/.well-known/openid-configuration",
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            # Browser must not hit hostPort Keycloak for ROPC — use POST /auth/oidc/token.
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "end_session_endpoint": f"{issuer}/protocol/openid-connect/logout",
            "revocation_endpoint": f"{issuer}/protocol/openid-connect/revoke",
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
            "redirect_uri": settings.oidc_flutter_redirect_uri,
            "redirect_uri_desktop": settings.oidc_flutter_redirect_uri_desktop,
        }
    return {
        "auth_mode": mode,
        "oidc": oidc if (mode == "oidc" and oidc is not None) else None,
    }


@router.post("/oidc/token")
async def oidc_token_proxy(request: Request) -> Response:
    """Proxy OIDC token grants to in-cluster Keycloak (ROPC / refresh).

    Flutter must call this instead of public hostPort issuer — hostPort is
    unreliable for web and may be down while API↔Keycloak Service works.
    """
    mode = settings.auth_mode.strip().lower()
    if mode != "oidc":
        return Response(
            content='{"code":"AUTH_MISCONFIGURED","detail":"AUTH_MODE is not oidc"}',
            status_code=503,
            media_type="application/json",
        )
    token_url = _token_url_in_cluster()
    if not token_url:
        return Response(
            content='{"code":"AUTH_MISCONFIGURED","detail":"Keycloak URL not configured"}',
            status_code=503,
            media_type="application/json",
        )

    body = await request.body()
    content_type = request.headers.get("content-type", "application/x-www-form-urlencoded")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.post(
                token_url,
                content=body,
                headers={"Content-Type": content_type},
            )
    except httpx.TimeoutException:
        return Response(
            content='{"code":"IDENTITY_PROVIDER","detail":"Keycloak token request timed out"}',
            status_code=504,
            media_type="application/json",
        )
    except httpx.HTTPError as exc:
        return Response(
            content=(
                '{"code":"IDENTITY_PROVIDER","detail":'
                f'"Keycloak unreachable: {type(exc).__name__}"}}'
            ),
            status_code=502,
            media_type="application/json",
        )

    # Pass through Keycloak status + body (JSON error_description etc.).
    media = upstream.headers.get("content-type", "application/json")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media)
