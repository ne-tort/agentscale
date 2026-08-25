"""Integration tests — AUTH_MODE=test login mint (dev bridge)."""

from __future__ import annotations

import os

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.auth.test_token import (
    DEMO_EMPLOYEE_EMAIL,
    DEMO_EMPLOYEE_SUB,
    PLATFORM_ADMIN_SUB,
)
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


def test_test_login_platform_admin(client: TestClient) -> None:
    r = client.post("/api/v1/auth/test/login", json={"persona": "platform_admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["persona"] == "platform_admin"
    assert body["expires_in"] >= 60
    claims = jwt.decode(
        body["access_token"],
        settings.auth_test_secret,
        algorithms=["HS256"],
        audience=settings.oidc_audience,
    )
    assert claims["sub"] == PLATFORM_ADMIN_SUB
    assert claims["platform_admin"] is True


@requires_postgres
def test_test_login_platform_admin_me(client: TestClient) -> None:
    r = client.post("/api/v1/auth/test/login", json={"persona": "platform_admin"})
    assert r.status_code == 200, r.text
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert "platform.admin" in me.json()["roles"]


def test_test_login_unavailable_when_not_test_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    r = client.post("/api/v1/auth/test/login", json={"persona": "platform_admin"})
    assert r.status_code == 404


def test_test_login_unavailable_when_prod(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "test")
    monkeypatch.setattr(settings, "app_env", "prod")
    r = client.post("/api/v1/auth/test/login", json={"persona": "platform_admin"})
    assert r.status_code == 404


@requires_postgres
def test_test_login_demo_employee_seeds_and_me(client: TestClient) -> None:
    first = client.post("/api/v1/auth/test/login", json={"persona": "demo_employee"})
    assert first.status_code == 200, first.text
    token = first.json()["access_token"]
    claims = jwt.decode(
        token,
        settings.auth_test_secret,
        algorithms=["HS256"],
        audience=settings.oidc_audience,
    )
    assert claims["sub"] == DEMO_EMPLOYEE_SUB
    assert claims["email"] == DEMO_EMPLOYEE_EMAIL

    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["employee"] is not None
    assert body["employee"]["email"] == DEMO_EMPLOYEE_EMAIL
    assert body["employee"]["status"] == "active"
    assert len(body["employee"]["memberships"]) >= 1

    second = client.post("/api/v1/auth/test/login", json={"persona": "demo_employee"})
    assert second.status_code == 200, second.text
    me2 = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {second.json()['access_token']}"},
    )
    assert me2.status_code == 200, me2.text
    assert me2.json()["employee"]["id"] == body["employee"]["id"]
