"""Unit tests for JWT validator modes."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest

os.environ.setdefault("AUTH_MODE", "test")

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.auth.jwt import JwtValidator, reset_jwt_validator


def test_prod_forbids_auth_mode_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "test")
    monkeypatch.setattr(settings, "app_env", "prod")
    reset_jwt_validator()
    with pytest.raises(AppError) as ei:
        JwtValidator().validate("x")
    assert ei.value.code == "AUTH_MISCONFIGURED"


def test_oidc_mode_requires_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "keycloak_issuer_url", None)
    monkeypatch.setattr(settings, "oidc_jwks_url", None)
    reset_jwt_validator()
    with pytest.raises(AppError) as ei:
        JwtValidator().validate("x.y.z")
    assert ei.value.code in {"AUTH_MISCONFIGURED", "UNAUTHORIZED"}


def test_test_mode_accepts_hs256(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "test")
    monkeypatch.setattr(settings, "app_env", "dev")
    reset_jwt_validator()
    now = datetime.now(UTC)
    tok = jwt.encode(
        {
            "sub": "s1",
            "aud": settings.oidc_audience,
            "exp": now + timedelta(hours=1),
            "platform_admin": True,
            "roles": ["platform.admin"],
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )
    p = JwtValidator().validate(tok)
    assert p.sub == "s1"
    assert p.is_platform_admin
