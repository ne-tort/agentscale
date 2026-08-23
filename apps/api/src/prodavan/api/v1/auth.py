"""Public auth discovery (L01) — no secrets."""

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
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "jwks_uri": settings.oidc_jwks_url or f"{issuer}/protocol/openid-connect/certs",
        }
    return {
        "auth_mode": mode,
        "oidc": oidc if mode == "oidc" else None,
    }
