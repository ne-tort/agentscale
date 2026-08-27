"""Integration tests — auth config + HS256 mint fixtures (no HTTP test login)."""

from __future__ import annotations

import os

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.auth.test_token import PLATFORM_ADMIN_SUB, mint_test_access_token
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


def test_test_login_http_removed(client: TestClient) -> None:
    r = client.post("/api/v1/auth/test/login", json={"persona": "platform_admin"})
    assert r.status_code == 404


def test_auth_config_test_mode_hides_oidc(client: TestClient) -> None:
    r = client.get("/api/v1/auth/config")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_mode"] == "test"
    assert body["oidc"] is None


def test_mint_platform_admin_token() -> None:
    token, expires_in = mint_test_access_token(sub=PLATFORM_ADMIN_SUB, platform_admin=True)
    assert expires_in >= 60
    claims = jwt.decode(
        token,
        settings.auth_test_secret,
        algorithms=["HS256"],
        audience=settings.oidc_audience,
    )
    assert claims["sub"] == PLATFORM_ADMIN_SUB
    assert claims["platform_admin"] is True
