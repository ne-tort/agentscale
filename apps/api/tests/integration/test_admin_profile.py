"""Integration — platform admin profile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.main import create_app
from tests.conftest import requires_postgres


def _token(*, sub: str, username: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "preferred_username": username or sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "platform_admin": platform_admin,
        "roles": [] if not platform_admin else ["platform.admin"],
    }
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    with TestClient(create_app()) as client:
        yield client


@requires_postgres
def test_admin_profile_get_and_password(client: TestClient) -> None:
    admin = _token(sub="padmin-prof", username="platform.admin", platform_admin=True)
    h = {"Authorization": f"Bearer {admin}"}

    profile = client.get("/api/v1/admin/profile", headers=h)
    assert profile.status_code == 200, profile.text
    body = profile.json()
    assert body["username"] == "platform.admin"
    assert body["password_set"] is True

    bad = client.put(
        "/api/v1/admin/profile/password",
        headers=h,
        json={"password": "short"},
    )
    assert bad.status_code == 422

    ok = client.put(
        "/api/v1/admin/profile/password",
        headers=h,
        json={"password": "new-admin-pass"},
    )
    assert ok.status_code == 200, ok.text

    login = client.put(
        "/api/v1/admin/profile/login",
        headers=h,
        json={"login": "platform.admin"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["username"] == "platform.admin"
