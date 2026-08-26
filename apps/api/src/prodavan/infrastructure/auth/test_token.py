"""Mint HS256 test JWTs (AUTH_MODE=test only) — never for production."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from prodavan.config.settings import settings
from prodavan.domain.identity import ROLE_COMPANY, ROLE_PLATFORM_ADMIN

# Stable demo identity for local / CI one-click login.
DEMO_COMPANY_NAME = "Prodavan Demo"
DEMO_COMPANY_SUB = "test-demo-company"
DEMO_COMPANY_PASSWORD = "demo-company-pass"
DEMO_EMPLOYEE_EMAIL = "demo@prodavan.local"
DEMO_EMPLOYEE_SUB = "test-demo-employee"
DEMO_EMPLOYEE_DISPLAY_NAME = "Demo Employee"

PLATFORM_ADMIN_SUB = "test-platform-admin"

DEFAULT_TTL_SECONDS = 28800  # 8h — AUTH_MODE=test only; OIDC TTL is Keycloak realm


def mint_test_access_token(
    *,
    sub: str,
    email: str | None = None,
    username: str | None = None,
    platform_admin: bool = False,
    roles: list[str] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, int]:
    """Return (jwt, expires_in) signed with AUTH_TEST_SECRET."""
    now = datetime.now(UTC)
    role_list: list[str] = list(roles or [])
    if platform_admin and ROLE_PLATFORM_ADMIN not in role_list:
        role_list.append(ROLE_PLATFORM_ADMIN)
    payload: dict = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": role_list,
    }
    if email:
        payload["email"] = email
    if username:
        payload["preferred_username"] = username
    token = jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")
    return token, ttl_seconds


def mint_company_principal_token(
    *,
    sub: str = DEMO_COMPANY_SUB,
    company_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, int]:
    return mint_test_access_token(
        sub=sub,
        username=company_id,
        roles=[ROLE_COMPANY],
        ttl_seconds=ttl_seconds,
    )
