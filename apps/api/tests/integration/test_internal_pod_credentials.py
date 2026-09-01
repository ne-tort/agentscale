"""Integration — internal pod credential broker (L08/L09)."""

from __future__ import annotations

import os
import uuid
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
from tests.integration.support import owner_auth_from_company


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
    reset_invite_client()
    with TestClient(create_app()) as test_client:
        yield test_client


def _launch_project(client: TestClient) -> tuple[dict[str, str], str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    admin_h = {"Authorization": f"Bearer {_token(sub=f'int-cred-admin-{suffix}', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={
            "name": f"IntCredCo-{suffix}",
            "password": "test-company-pass",
            "admin_email": f"ic-{suffix}@co.test",
        },
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-int-cred",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "IntCredCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "IntCredProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"agent_provider": "cursor", "resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text

    launched = client.post(f"/api/v1/projects/{project_id}/launch", headers=owner_h)
    assert launched.status_code == 200, launched.text
    runtime = launched.json().get("runtime") or {}
    pod_id = runtime.get("pod_id")
    assert pod_id, launched.text
    return owner_h, project_id, pod_id, key_id


@requires_postgres
def test_internal_list_and_lease_credentials(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_auth_token", "pod-int-secret")
    _, _project_id, pod_id, key_id = _launch_project(client)
    pod_h = {"Authorization": "Bearer pod-int-secret"}

    listed = client.get(f"/api/v1/internal/pods/{pod_id}/credentials", headers=pod_h)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(i["key_id"] == key_id for i in items)
    assert all("secret" not in i for i in items)

    leased = client.post(
        f"/api/v1/internal/pods/{pod_id}/credentials/{key_id}/lease",
        headers=pod_h,
        json={"ttl_sec": 120},
    )
    assert leased.status_code == 201, leased.text
    body = leased.json()
    assert body["key_id"] == key_id
    assert body["secret"] == "sk-int-cred"
    assert body["ttl_sec"] == 120
    assert body["lease_id"].startswith("lease_")


@requires_postgres
def test_internal_credentials_reject_user_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_auth_token", "pod-int-secret")
    owner_h, _project_id, pod_id, key_id = _launch_project(client)

    denied = client.get(f"/api/v1/internal/pods/{pod_id}/credentials", headers=owner_h)
    assert denied.status_code == 403, denied.text

    denied_lease = client.post(
        f"/api/v1/internal/pods/{pod_id}/credentials/{key_id}/lease",
        headers=owner_h,
        json={},
    )
    assert denied_lease.status_code == 403, denied_lease.text


@requires_postgres
def test_internal_revoke_lease_fails_without_runtime(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_auth_token", "pod-int-secret")
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", False)
    _, _project_id, pod_id, _key_id = _launch_project(client)
    pod_h = {"Authorization": "Bearer pod-int-secret"}

    revoked = client.delete(
        f"/api/v1/internal/pods/{pod_id}/credentials/leases/lease_abc123",
        headers=pod_h,
    )
    assert revoked.status_code == 502, revoked.text
    assert revoked.json()["code"] == "REVOKE_FAILED"
