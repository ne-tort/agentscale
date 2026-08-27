"""Public auth discovery (L01). Password login is Keycloak ROPC (Flutter)."""

from __future__ import annotations

from fastapi import APIRouter

from prodavan.config.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _issuer_base() -> str | None:
    if settings.keycloak_issuer_url:
        return settings.keycloak_issuer_url.rstrip("/")
    if settings.keycloak_url:
        return f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
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
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "end_session_endpoint": f"{issuer}/protocol/openid-connect/logout",
            "revocation_endpoint": f"{issuer}/protocol/openid-connect/revoke",
            # Public JWKS for clients; API validates via OIDC_JWKS_URL (in-cluster).
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
            "redirect_uri": settings.oidc_flutter_redirect_uri,
            "redirect_uri_desktop": settings.oidc_flutter_redirect_uri_desktop,
        }
    return {
        "auth_mode": mode,
        # OIDC block always present when issuer configured (Flutter needs endpoints).
        "oidc": oidc if (mode == "oidc" and oidc is not None) else None,
    }
