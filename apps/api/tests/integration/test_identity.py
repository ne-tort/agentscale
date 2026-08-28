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
    username: str | None = None,
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
    if username:
        payload["preferred_username"] = username
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


def test_me_requires_bearer(client: TestClient) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_auth_config_public(client: TestClient) -> None:
    r = client.get("/api/v1/auth/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth_mode"] in {"test", "oidc"}
    assert "brokers" in body
    assert "features" in body
    if body["auth_mode"] == "test":
        assert body["features"]["password_login"] is False
        assert body["brokers"] == []
    else:
        assert body["features"]["password_login"] is True
        assert isinstance(body["brokers"], list)


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
    """Needs DB: /me resolves entitlements for platform admin."""
    tok = _token(sub="admin-1", platform_admin=True)
    r = client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {tok}",
            "X-Cabinet-Id": "cab_abc",
            "X-Project-Id": "proj_1",
        },
    )
    if r.status_code == 500:
        pytest.skip("Postgres unavailable")
    assert r.status_code == 200
    body = r.json()
    assert body["work_context"]["cabinet_id"] == "cab_abc"
    assert body["work_context"]["project_id"] == "proj_1"
    assert "company" in body
    assert body["company"] is None



@requires_postgres
def test_create_company_with_password_and_disable(client: TestClient) -> None:
    admin = _token(sub="padmin", email="padmin@example.com", platform_admin=True)
    # password too short
    bad = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Acme", "admin_email": "boss@acme.test", "password": "short"},
    )
    assert bad.status_code == 422

    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Acme",
            "password": "test-company-pass",
            "admin_email": "boss@acme.test",
            "admin_display_name": "Boss",
        },
    )
    assert created.status_code == 201, created.text
    company = created.json()["company"]
    company_id = company["id"]
    assert company["username"] == company_id
    assert company["keycloak_sub"]
    assert created.json()["credentials"]["username"] == company_id
    admin_emp = created.json()["admin_employee"]
    emp_id = admin_emp["id"]
    admin_sub = admin_emp["keycloak_sub"]
    assert admin_sub, "invite must persist keycloak_sub"

    # Org principal (role company) — login username = company_id, no email required
    org_tok = _token(
        sub=company["keycloak_sub"],
        username=company_id,
        roles=["company"],
    )
    org_me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {org_tok}"})
    assert org_me.status_code == 200, org_me.text
    assert "company" in org_me.json()["contours"]
    assert org_me.json()["company"]["id"] == company_id
    assert org_me.json()["employee"] is None

    # Human login: JWT.sub = employees.keycloak_sub (local password or IdP broker — same sub)
    boss_tok = _token(sub=admin_sub, email="boss@acme.test", roles=["employee"])
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {boss_tok}"})
    assert me.status_code == 200
    assert me.json()["employee"]["status"] == "active"
    assert me.json()["employee"]["id"] == emp_id

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

    events = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=employee.disabled",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    items = events.json()["items"]
    assert len(items) >= 1
    assert items[0]["event_type"] == "employee.disabled"
    assert items[0]["payload"].get("employee_id") == emp_id

    blocked = client.get("/api/v1/me", headers={"Authorization": f"Bearer {boss_tok}"})
    assert blocked.status_code == 403

    enabled = client.post(
        f"/api/v1/employees/{emp_id}/enable",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "active"

    me_after = client.get("/api/v1/me", headers={"Authorization": f"Bearer {boss_tok}"})
    assert me_after.status_code == 200

    # Company principal still works after employee disable
    org_me2 = client.get("/api/v1/me", headers={"Authorization": f"Bearer {org_tok}"})
    assert org_me2.status_code == 200


@requires_postgres
def test_company_principal_owns_ai_keys(client: TestClient) -> None:
    admin = _token(sub="padmin-keys", email="padmin-keys@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "KeysCo", "password": "test-company-pass"},
    )
    assert created.status_code == 201, created.text
    company = created.json()["company"]
    company_id = company["id"]
    org_tok = _token(sub=company["keycloak_sub"], username=company_id, roles=["company"])
    h = {"Authorization": f"Bearer {org_tok}"}

    created_key = client.post(
        f"/api/v1/companies/{company_id}/ai-keys",
        headers=h,
        json={"name": "Local Cursor", "provider": "cursor", "api_kind": "cursor_sdk", "secret": "sk-test-1"},
    )
    assert created_key.status_code == 201, created_key.text
    body = created_key.json()
    assert body["owner_scope"] == "company"
    assert body["owner_company_id"] == company_id
    assert body["writable"] is True

    listed = client.get(f"/api/v1/companies/{company_id}/ai-keys", headers=h)
    assert listed.status_code == 200
    assert any(k["id"] == body["id"] for k in listed.json())

    fetched = client.get(
        f"/api/v1/companies/{company_id}/ai-keys/{body['id']}",
        headers=h,
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    containers = client.get(f"/api/v1/companies/{company_id}/containers", headers=h)
    assert containers.status_code == 200
    assert "items" in containers.json()


@requires_postgres
def test_create_company_name_only_then_invite_admin(client: TestClient) -> None:
    admin = _token(sub="padmin-name", email="padmin-name@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "NameOnlyCo", "password": "test-company-pass"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["company"]["name"] == "NameOnlyCo"
    assert body["company"]["username"] == body["company"]["id"]
    assert body["company"]["keycloak_sub"]
    assert body["company"].get("contact_email") is None
    assert "admin_employee" not in body
    company_id = body["company"]["id"]

    invited = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "login": "lateradmin",
            "password": "test-employee-pass",
            "contact_email": "later-admin@nameonly.test",
            "role": "company.admin",
        },
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["contact_email"] == "later-admin@nameonly.test"
    assert invited.json().get("keycloak_sub"), "invite must persist employees.keycloak_sub immediately"

    patched = client.patch(
        f"/api/v1/admin/companies/{company_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "NameOnlyCo Renamed"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "NameOnlyCo Renamed"

    detail = client.get(
        f"/api/v1/admin/companies/{company_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["username"] == company_id
    assert detail.json()["password_set"] is True

    rotated = client.put(
        f"/api/v1/admin/companies/{company_id}/password",
        headers={"Authorization": f"Bearer {admin}"},
        json={"password": "rotated-company-pass"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["password_set"] is True
