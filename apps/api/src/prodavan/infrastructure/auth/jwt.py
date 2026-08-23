"""JWKS-backed JWT validation (L01)."""

from __future__ import annotations

import time
from typing import Any

import jwt
from jwt import PyJWKClient

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_PLATFORM_ADMIN, Principal


class JwtValidator:
    """Validates access tokens via JWKS. Caches JWK client."""

    def __init__(self) -> None:
        self._jwk_client: PyJWKClient | None = None
        self._jwk_fetched_at: float = 0.0

    def _jwks_url(self) -> str:
        if settings.oidc_jwks_url:
            return settings.oidc_jwks_url
        if not settings.keycloak_issuer_url:
            raise AppError(
                code="AUTH_MISCONFIGURED",
                title="Auth misconfigured",
                status=500,
                detail="KEYCLOAK_ISSUER_URL or OIDC_JWKS_URL required",
            )
        return f"{settings.keycloak_issuer_url.rstrip('/')}/protocol/openid-connect/certs"

    def _client(self) -> PyJWKClient:
        ttl = settings.oidc_jwks_cache_seconds
        now = time.monotonic()
        if self._jwk_client is None or (now - self._jwk_fetched_at) > ttl:
            self._jwk_client = PyJWKClient(self._jwks_url(), cache_keys=True, lifespan=ttl)
            self._jwk_fetched_at = now
        return self._jwk_client

    def validate(self, token: str) -> Principal:
        if settings.auth_mode == "test":
            if settings.app_env == "prod":
                raise AppError(
                    code="AUTH_MISCONFIGURED",
                    title="Auth misconfigured",
                    status=500,
                    detail="AUTH_MODE=test forbidden when APP_ENV=prod",
                )
            return self._validate_test(token)
        try:
            signing_key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.oidc_audience,
                issuer=settings.keycloak_issuer_url,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AppError(
                code="UNAUTHORIZED",
                title="Unauthorized",
                status=401,
                detail="Invalid or expired token",
            ) from exc
        return _principal_from_claims(claims)

    def _validate_test(self, token: str) -> Principal:
        """Test-only HS256 with shared secret — never for production (AUTH_MODE=test)."""
        try:
            claims = jwt.decode(
                token,
                settings.auth_test_secret,
                algorithms=["HS256"],
                audience=settings.oidc_audience,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AppError(
                code="UNAUTHORIZED",
                title="Unauthorized",
                status=401,
                detail="Invalid or expired token",
            ) from exc
        return _principal_from_claims(claims)


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    roles: set[str] = set()
    realm = claims.get("realm_access") or {}
    if isinstance(realm, dict):
        for r in realm.get("roles") or []:
            roles.add(str(r))
    for r in claims.get("roles") or []:
        roles.add(str(r))
    # Convenience claim used only in tests; still re-checked against DB for company ops
    if claims.get("platform_admin") is True:
        roles.add(ROLE_PLATFORM_ADMIN)
    email = claims.get("email")
    return Principal(sub=str(claims["sub"]), roles=frozenset(roles), email=str(email) if email else None)


_validator: JwtValidator | None = None


def get_jwt_validator() -> JwtValidator:
    global _validator
    if _validator is None:
        _validator = JwtValidator()
    return _validator


def reset_jwt_validator() -> None:
    global _validator
    _validator = None
