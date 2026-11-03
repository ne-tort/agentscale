"""Acceptance contract for the /api/v1/me active field."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
os.environ.setdefault("KEYCLOAK_INVITE_MODE", "fake")

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
        "roles": roles if roles is not None else ([] if not platform_admin else ["platform.admin"]),
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
    with TestClient(create_app()) as test_client:
        yield test_client


def _assert_no_credentials(response, token: str, *secrets: str) -> dict:
    body = response.json()

    def inspect(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = key.casefold()
                assert not any(
                    marker in lowered
                    for marker in ("password", "secret", "token", "credential")
                )
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(body)
    for secret in (token, *secrets):
        assert secret not in response.text
    return body


def _assert_active(response, expected: bool, token: str, *secrets: str) -> dict:
    assert response.status_code == 200, response.text
    body = _assert_no_credentials(response, token, *secrets)
    assert type(body.get("active")) is bool
    assert body["active"] is expected
    return body


def _assert_denied(response, token: str, *secrets: str) -> None:
    assert response.status_code == 403, response.text
    _assert_no_credentials(response, token, *secrets)


@requires_postgres
def test_me_active_is_true_for_platform_admin(client: TestClient) -> None:
    token = _token(sub="active-contract-platform-admin", platform_admin=True)
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    body = _assert_active(response, True, token)
    assert body["company"] is None
    assert body["employee"] is None


@requires_postgres
def test_me_active_is_true_for_active_employee(client: TestClient) -> None:
    admin_token = _token(sub="active-contract-employee-admin", platform_admin=True)
    company_password = "company-contract-password-42"
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Active employee contract",
            "password": company_password,
            "admin_email": "active-contract-employee@example.test",
        },
    )
    assert created.status_code == 201, created.text
    employee = created.json()["admin_employee"]
    token = _token(
        sub=employee["keycloak_sub"],
        email=employee["email"],
        roles=["employee"],
    )

    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    body = _assert_active(response, True, token, company_password)
    assert body["employee"]["id"] == employee["id"]
    assert body["employee"]["status"] == "active"


@requires_postgres
def test_me_denies_disabled_and_deleted_employees(client: TestClient) -> None:
    admin_token = _token(sub="active-contract-lifecycle-admin", platform_admin=True)
    company_password = "company-lifecycle-password-52"
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Employee lifecycle contract", "password": company_password},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    credentials = []
    for login in ("disabledcontract", "deletedcontract"):
        password = f"{login}-employee-password"
        invited = client.post(
            f"/api/v1/companies/{company_id}/employees",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"login": login, "password": password, "role": "member"},
        )
        assert invited.status_code == 201, invited.text
        employee = invited.json()
        token = _token(sub=employee["keycloak_sub"], roles=["employee"])
        active = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        active_body = _assert_active(active, True, token, password, company_password)
        assert active_body["employee"]["status"] == "active"
        credentials.append((employee, token, password))

    disabled_employee, disabled_token, disabled_password = credentials[0]
    disabled = client.post(
        f"/api/v1/employees/{disabled_employee['id']}/disable",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert disabled.status_code == 200, disabled.text
    disabled_me = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {disabled_token}"}
    )
    _assert_denied(disabled_me, disabled_token, disabled_password, company_password)

    deleted_employee, deleted_token, deleted_password = credentials[1]
    deleted = client.delete(
        f"/api/v1/employees/{deleted_employee['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deleted.status_code == 200, deleted.text
    deleted_me = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {deleted_token}"}
    )
    _assert_denied(deleted_me, deleted_token, deleted_password, company_password)


@requires_postgres
def test_me_active_tracks_company_principal_lifecycle(client: TestClient) -> None:
    admin_token = _token(sub="active-contract-company-admin", platform_admin=True)
    company_password = "company-principal-password-63"
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Company principal active contract", "password": company_password},
    )
    assert created.status_code == 201, created.text
    company = created.json()["company"]
    company_token = _token(
        sub=company["keycloak_sub"],
        username=company["id"],
        roles=["company"],
    )
    company_headers = {"Authorization": f"Bearer {company_token}"}

    active = client.get("/api/v1/me", headers=company_headers)
    active_body = _assert_active(active, True, company_token, company_password)
    assert active_body["company"]["id"] == company["id"]
    assert active_body["employee"] is None

    deleted = client.delete(
        f"/api/v1/admin/companies/{company['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deleted.status_code == 200, deleted.text
    restored = client.post(
        f"/api/v1/admin/companies/{company['id']}/restore",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "paused"

    inactive = client.get("/api/v1/me", headers=company_headers)
    inactive_body = _assert_active(inactive, False, company_token, company_password)
    assert inactive_body["company"]["id"] == company["id"]
