"""Integration — admin Project Containers P1 read-model."""

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
    with TestClient(create_app()) as client:
        yield client


def _setup_project(client: TestClient) -> tuple[str, str]:
    admin = _token(sub="padmin-ctr", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "CtrCo", "admin_email": "owner@ctrco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Ctr Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-ctr",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    owner_tok = _token(sub="owner-ctr-sub", email="owner@ctrco.test")
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "CtrCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    proj = client.post(
        f"/api/v1/cabinets/{cab.json()['id']}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "CtrProj"},
    )
    assert proj.status_code == 201, proj.text
    return admin, proj.json()["id"]


@requires_postgres
def test_admin_containers_list_and_pause(client: TestClient) -> None:
    admin, project_id = _setup_project(client)
    listed = client.get(
        "/api/v1/admin/containers",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(i["project_id"] == project_id for i in items)
    row = next(i for i in items if i["project_id"] == project_id)
    assert row["status"] == "active"
    assert row["company_name"] == "CtrCo"
    assert row["cabinet_name"] == "CtrCab"
    assert row["owner_email"] == "owner@ctrco.test"
    assert row["container_ref"].startswith("object-ws:")

    paused = client.post(
        f"/api/v1/admin/containers/{project_id}/pause",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    got = client.get(
        f"/api/v1/admin/containers/{project_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert got.status_code == 200
    assert got.json()["status"] == "paused"

    # active items should sort before paused when mixed
    resumed = client.post(
        f"/api/v1/admin/containers/{project_id}/resume",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"
