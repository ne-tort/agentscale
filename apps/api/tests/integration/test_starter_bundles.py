"""Integration tests — starter bundle catalog API (L04)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.main import create_app
from tests.conftest import requires_postgres


def _token(*, sub: str, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": [] if not platform_admin else ["platform.admin"],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    return TestClient(create_app())


def test_admin_list_starter_bundles(client: TestClient) -> None:
    admin = _token(sub="bundle-catalog-admin", platform_admin=True)
    res = client.get(
        "/api/v1/admin/starter-bundles",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    match = next(i for i in items if i["id"] == "equipment-procurement")
    assert match["bundle_available"] is True


def test_admin_download_starter_bundle(client: TestClient) -> None:
    admin = _token(sub="bundle-catalog-admin", platform_admin=True)
    res = client.get(
        "/api/v1/admin/starter-bundles/equipment-procurement/bundle",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["format"] == "cabinet.bundle"
    assert body["zip_base64"]


@requires_postgres
def test_employee_list_starter_bundles(client: TestClient) -> None:
    emp = _token(sub="bundle-emp", email="emp@starter.test")
    res = client.get(
        "/api/v1/starter-bundles",
        headers={"Authorization": f"Bearer {emp}"},
    )
    assert res.status_code == 403
