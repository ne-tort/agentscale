"""E2E login flows — local password + simulated IdP broker (vk/yandex).

Broker is transparent for API: JWT.sub == employees.keycloak_sub whether the user
signed in via KC local credentials or Identity Broker. We mint AUTH_MODE=test JWTs
with the same shape as Keycloak access tokens.
"""

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
    roles: list[str] | None = None,
    platform_admin: bool = False,
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


@requires_postgres
def test_e2e_company_and_employee_local_login(client: TestClient) -> None:
    admin = _token(sub="padmin-e2e", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "E2E Local Co",
            "password": "test-company-pass",
            "admin_email": "e2e-local@prodavan.test",
            "admin_display_name": "E2E Local",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    company_id = body["company"]["id"]
    company_sub = body["company"]["keycloak_sub"]
    emp = body["admin_employee"]
    emp_sub = emp["keycloak_sub"]
    assert company_sub and emp_sub
    assert emp["status"] == "invited"

    # Company org principal — KC username/password path
    org = client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {_token(sub=company_sub, username=company_id, roles=['company'])}"
        },
    )
    assert org.status_code == 200, org.text
    assert org.json()["company"]["id"] == company_id
    assert "company" in org.json()["contours"]
    assert org.json()["employee"] is None

    # Employee first login activates INVITED → ACTIVE
    me = client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {_token(sub=emp_sub, email=emp['email'], roles=['employee'])}"
        },
    )
    assert me.status_code == 200, me.text
    assert me.json()["employee"]["status"] == "active"
    assert me.json()["employee"]["id"] == emp["id"]


@requires_postgres
@pytest.mark.parametrize("provider", ["vk", "yandex"])
def test_e2e_broker_login_same_sub_as_local(client: TestClient, provider: str) -> None:
    """Simulate KC Identity Broker: same realm user/sub after vk/yandex first login.

    Prodavan does not see the IdP — JWT.sub equals employees.keycloak_sub whether
    the user used local KC password or broker (kc_idp_hint={provider}).
    """
    admin = _token(sub=f"padmin-broker-{provider}", platform_admin=True)
    email = f"e2e-{provider}@prodavan.test"
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": f"E2E {provider}", "password": "test-company-pass"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    invited = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {admin}"},
        json={"email": email, "display_name": f"Broker {provider}", "role": "member"},
    )
    assert invited.status_code == 201, invited.text
    emp_id = invited.json()["id"]
    emp_sub = invited.json()["keycloak_sub"]
    assert emp_sub

    # Broker login mint: same claims shape as KC access token after IdP alias {provider}
    tok = _token(sub=emp_sub, email=email, roles=["employee"])
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200, me.text
    assert me.json()["employee"]["id"] == emp_id
    assert me.json()["employee"]["status"] == "active"
    assert "employee" in me.json()["contours"]

    # Second login (e.g. alternate IdP linked to same KC user) still resolves by sub
    me2 = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert me2.status_code == 200
    assert me2.json()["employee"]["id"] == emp_id


@requires_postgres
def test_e2e_wrong_sub_does_not_steal_bound_invite(client: TestClient) -> None:
    admin = _token(sub="padmin-steal", platform_admin=True)
    email = "e2e-steal@prodavan.test"
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "E2E Steal",
            "password": "test-company-pass",
            "admin_email": email,
        },
    )
    assert created.status_code == 201, created.text
    bound_sub = created.json()["admin_employee"]["keycloak_sub"]
    assert bound_sub

    # Attacker JWT with same email but different sub must not bind over existing keycloak_sub
    attacker = _token(sub="attacker-other-sub", email=email, roles=["employee"])
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {attacker}"})
    # Email-bind only fills null keycloak_sub; here sub already set → no employee for attacker sub
    assert me.status_code == 403, me.text
