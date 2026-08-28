"""Cabinet registry (integration)."""

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


def _token(*, sub: str, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
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


def test_cabinets_require_auth(client: TestClient) -> None:
    r = client.get("/api/v1/cabinets")
    assert r.status_code == 401


@requires_postgres
def test_admin_cabinet_crud(client: TestClient) -> None:
    admin = _token(sub="padmin-cab", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "CabAdminCo", "password": "test-company-pass", "admin_email": "a@cabadmin.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    cab = client.post(
        "/api/v1/admin/cabinets",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Ops Cabinet", "company_id": company_id},
    )
    assert cab.status_code == 200, cab.text
    body = cab.json()
    assert body["id"].startswith("cab_")
    assert body["schema_name"].startswith("cab_inst_")
    assert body["company_id"] is None
    assert body["owner_employee_id"] is None
    assert body["owner_scope"] == "platform"
    assert company_id in body["company_ids"]
    assert body["writable"] is False
    cabinet_id = body["id"]

    listed = client.get("/api/v1/admin/cabinets", headers={"Authorization": f"Bearer {admin}"})
    assert listed.status_code == 200
    ids = {i["id"] for i in listed.json()["items"]}
    assert cabinet_id in ids

    patched = client.patch(
        f"/api/v1/admin/cabinets/{cabinet_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Ops Cabinet Renamed"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Ops Cabinet Renamed"

    deleted = client.delete(
        f"/api/v1/admin/cabinets/{cabinet_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True

    missing = client.get(
        f"/api/v1/admin/cabinets/{cabinet_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert missing.status_code == 404


@requires_postgres
def test_employee_cabinet_peer_isolation(client: TestClient) -> None:
    admin = _token(sub="padmin-peer", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "PeerCo", "password": "test-company-pass", "admin_email": "owner@peerco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    owner_tok = _token(sub="owner-peer", email="owner@peerco.test")
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {owner_tok}"})
    assert me.status_code == 200

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Owned", "company_id": company_id},
    )
    assert cab.status_code == 200, cab.text
    cabinet_id = cab.json()["id"]

    peer = _token(sub="peer-sub", email="peer@elsewhere.test")
    denied = client.get(
        f"/api/v1/cabinets/{cabinet_id}",
        headers={"Authorization": f"Bearer {peer}"},
    )
    assert denied.status_code in (403, 404)


@requires_postgres
def test_admin_cabinet_grants_company_visibility_and_assignment(client: TestClient) -> None:
    admin = _token(sub="padmin-grant", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "GrantCo", "password": "test-company-pass", "admin_email": "boss@grantco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    cab = client.post(
        "/api/v1/admin/cabinets",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Shared Ops", "company_ids": [company_id]},
    )
    assert cab.status_code == 200, cab.text
    cabinet_id = cab.json()["id"]

    boss_tok = _token(sub="boss-grant", email="boss@grantco.test")
    org = client.get(
        f"/api/v1/companies/{company_id}/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert org.status_code == 200, org.text
    items = org.json()["items"]
    workspace = next((i for i in items if i.get("template_cabinet_id") == cabinet_id), None)
    assert workspace is not None, items
    workspace_id = workspace["id"]
    assert workspace_id != cabinet_id
    assert workspace["writable"] is False
    assert workspace.get("projects_count") is not None

    invite = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"email": "member@grantco.test", "display_name": "Member"},
    )
    assert invite.status_code == 201, invite.text
    member_id = invite.json()["employee"]["id"]

    assigned = client.post(
        f"/api/v1/companies/{company_id}/cabinets/{workspace_id}/assignments",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"employee_id": member_id},
    )
    assert assigned.status_code == 200, assigned.text

    member_tok = _token(sub="member-grant", email="member@grantco.test")
    listed = client.get("/api/v1/cabinets", headers={"Authorization": f"Bearer {member_tok}"})
    assert listed.status_code == 200, listed.text
    assert any(i["id"] == workspace_id for i in listed.json()["items"])


@requires_postgres
def test_cabinet_max_projects_quota(client: TestClient) -> None:
    admin = _token(sub="padmin-maxproj", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "MaxProjCo", "password": "test-company-pass", "admin_email": "boss@maxproj.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    cab = client.post(
        "/api/v1/admin/cabinets",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Limited", "company_ids": [company_id]},
    )
    assert cab.status_code == 200, cab.text
    template_id = cab.json()["id"]

    patched = client.patch(
        f"/api/v1/admin/cabinets/{template_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"max_projects": 1},
    )
    assert patched.status_code == 200, patched.text

    boss_tok = _token(sub="boss-maxproj", email="boss@maxproj.test")
    org = client.get(
        f"/api/v1/companies/{company_id}/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert org.status_code == 200, org.text
    workspace = next(i for i in org.json()["items"] if i.get("template_cabinet_id") == template_id)
    workspace_id = workspace["id"]
    assert workspace["max_projects"] == 1

    member_invite = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"email": "worker@maxproj.test", "display_name": "Worker"},
    )
    assert member_invite.status_code == 201, member_invite.text
    member_id = member_invite.json()["employee"]["id"]
    assigned = client.post(
        f"/api/v1/companies/{company_id}/cabinets/{workspace_id}/assignments",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"employee_id": member_id},
    )
    assert assigned.status_code == 200, assigned.text

    worker_tok = _token(sub="worker-maxproj", email="worker@maxproj.test")
    first = client.post(
        f"/api/v1/cabinets/{workspace_id}/projects",
        headers={"Authorization": f"Bearer {worker_tok}"},
        json={"name": "One"},
    )
    assert first.status_code == 201, first.text
    assert first.json().get("created_by_employee_id") == member_id

    second = client.post(
        f"/api/v1/cabinets/{workspace_id}/projects",
        headers={"Authorization": f"Bearer {worker_tok}"},
        json={"name": "Two"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "PROJECT_QUOTA"
