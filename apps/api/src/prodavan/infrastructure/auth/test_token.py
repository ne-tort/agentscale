"""Mint HS256 test JWTs (AUTH_MODE=test only) — never for production."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from prodavan.config.settings import settings
from prodavan.domain.identity import ROLE_PLATFORM_ADMIN

# Stable demo identity for local / CI one-click login.
DEMO_COMPANY_NAME = "Prodavan Demo"
DEMO_EMPLOYEE_EMAIL = "demo@prodavan.local"
DEMO_EMPLOYEE_SUB = "test-demo-employee"
DEMO_EMPLOYEE_DISPLAY_NAME = "Demo Employee"

PLATFORM_ADMIN_SUB = "test-platform-admin"

DEFAULT_TTL_SECONDS = 3600


def mint_test_access_token(
    *,
    sub: str,
    email: str | None = None,
    platform_admin: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, int]:
    """Return (jwt, expires_in) signed with AUTH_TEST_SECRET."""
    now = datetime.now(UTC)
    payload: dict = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": [ROLE_PLATFORM_ADMIN] if platform_admin else [],
    }
    if email:
        payload["email"] = email
    token = jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")
    return token, ttl_seconds
