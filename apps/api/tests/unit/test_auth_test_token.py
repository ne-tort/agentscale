"""Unit tests for test JWT mint helper."""

from __future__ import annotations

import os

import jwt

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.test_token import mint_test_access_token


def test_mint_test_access_token_platform_admin() -> None:
    token, ttl = mint_test_access_token(sub="u-admin", platform_admin=True)
    assert ttl == 3600
    claims = jwt.decode(
        token,
        settings.auth_test_secret,
        algorithms=["HS256"],
        audience=settings.oidc_audience,
    )
    assert claims["sub"] == "u-admin"
    assert claims["platform_admin"] is True
    assert "platform.admin" in claims["roles"]


def test_mint_test_access_token_employee_email() -> None:
    token, _ = mint_test_access_token(sub="u-emp", email="a@b.c", platform_admin=False)
    claims = jwt.decode(
        token,
        settings.auth_test_secret,
        algorithms=["HS256"],
        audience=settings.oidc_audience,
    )
    assert claims["email"] == "a@b.c"
    assert claims["roles"] == []
