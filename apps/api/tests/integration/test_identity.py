"""Identity / authz contract tests (L01)."""

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
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres


def _token(
    *,
    sub: str,
    email: str | None = None,
    platform_admin: bool = False,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": roles or ([] if not platform_admin else ["platform.admin"]),
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    return TestClient(create_app())


def test_me_requires_bearer(client: TestClient) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_auth_config_public(client: TestClient) -> None:
    r = client.get("/api/v1/auth/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth_mode"] in {"test", "oidc"}
    if body["auth_mode"] == "test":
        assert body["oidc"] is None
    assert "audience" not in body or body.get("oidc") is None or "audience" in body["oidc"]
    if body.get("oidc"):
        assert "redirect_uri" in body["oidc"]
        assert "discovery_url" in body["oidc"]


def test_invalid_token_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_expired_token_rejected(client: TestClient) -> None:
    now = datetime.now(UTC)
    tok = jwt.encode(
        {
            "sub": "u1",
            "aud": settings.oidc_audience,
            "exp": now - timedelta(minutes=1),
            "iat": now - timedelta(hours=1),
            "platform_admin": True,
            "roles": ["platform.admin"],
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_work_context_headers_not_from_jwt(client: TestClient) -> None:
    tok = _token(sub="admin-1", platform_admin=True)
    r = client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {tok}",
            "X-Cabinet-Id": "cab_abc",
            "X-Project-Id": "proj_1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["work_context"]["cabinet_id"] == "cab_abc"
    assert body["work_context"]["project_id"] == "proj_1"


@requires_postgres
def test_create_company_invite_no_password_and_disable(client: TestClient) -> None:
    admin = _token(sub="padmin", email="padmin@example.com", platform_admin=True)
    bad = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Acme", "admin_email": "boss@acme.test", "password": "secret"},
    )
    assert bad.status_code == 422

    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Acme", "admin_email": "boss@acme.test", "admin_display_name": "Boss"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    emp_id = created.json()["admin_employee"]["id"]

    boss_tok = _token(sub="boss-sub", email="boss@acme.test")
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {boss_tok}"})
    assert me.status_code == 200
    assert me.json()["employee"]["status"] == "active"

    switch = client.post(
        f"/api/v1/session/switch-company?company_id={company_id}",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert switch.status_code == 200
    assert switch.json()["jwt_reissued"] is False

    stranger = _token(sub="stranger", email="x@y.z")
    denied = client.post(
        f"/api/v1/session/switch-company?company_id={company_id}",
        headers={"Authorization": f"Bearer {stranger}"},
    )
    assert denied.status_code == 403

    disabled = client.post(
        f"/api/v1/employees/{emp_id}/disable",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    blocked = client.get("/api/v1/me", headers={"Authorization": f"Bearer {boss_tok}"})
    assert blocked.status_code == 403
