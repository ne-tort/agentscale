"""Integration tests — L07 projects runtime."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    return TestClient(create_app())


def _setup_cabinet(client: TestClient) -> tuple[str, str, str]:
    admin = _token(sub="padmin", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "ProjCo", "admin_email": "owner@projco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_tok = _token(sub="owner-sub", email="owner@projco.test")
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ProjCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    return company_id, cab.json()["id"], owner_tok


@requires_postgres
def test_project_create_materialize_lifecycle(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Demo Run"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    project_id = body["id"]
    assert body["status"] == "active"
    assert body["container_ref"].startswith("local-ws:")
    ws_root = Path(body["materialize"]["workspace_root"])
    assert ws_root.is_dir()
    assert (ws_root / "AGENTS.md").is_file()
    assert (ws_root / "CLAUDE.md").is_file()
    assert (ws_root / "mcp.json").is_file()
    assert (ws_root / "inbox").is_dir()

    listed = client.get(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200
    assert any(p["id"] == project_id for p in listed.json()["items"])

    paused = client.post(
        f"/api/v1/projects/{project_id}/pause",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(
        f"/api/v1/projects/{project_id}/resume",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    trig = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"kind": "chat.message", "payload": {"text": "hello"}},
    )
    assert trig.status_code == 202
    assert trig.json()["kind"] == "chat.message"

    att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"hello file").decode("ascii"),
        },
    )
    assert att.status_code == 201
    assert att.json()["filename"] == "note.txt"
    assert (ws_root / "inbox" / "note.txt").is_file()

    deleted = client.delete(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert not ws_root.parent.parent.joinpath(body["workspace_key"]).exists() or not ws_root.exists()
