"""Cabinet registry + meta documents (integration)."""

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
def test_admin_cabinet_crud_and_meta_documents(client: TestClient) -> None:
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
    assert body["company_id"] == company_id
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

    put = client.put(
        f"/api/v1/cabinets/{cabinet_id}/meta/documents/ui",
        headers={"Authorization": f"Bearer {admin}"},
        json={"body": {"tabs": [{"title": "Home"}], "version": 1}},
    )
    assert put.status_code == 200, put.text
    assert put.json()["slug"] == "ui"
    assert put.json()["body"]["version"] == 1

    bad = client.put(
        f"/api/v1/cabinets/{cabinet_id}/meta/documents/bad",
        headers={"Authorization": f"Bearer {admin}"},
        json={"body": "not-json-object"},
    )
    assert bad.status_code == 422

    got = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/documents/ui",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert got.status_code == 200
    assert got.json()["body"]["tabs"][0]["title"] == "Home"

    docs = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/documents",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert docs.status_code == 200
    assert any(d["slug"] == "ui" for d in docs.json()["items"])

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
    assert any(i["id"] == cabinet_id for i in org.json()["items"])
    assert org.json()["items"][0]["writable"] is False

    invite = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"email": "member@grantco.test", "display_name": "Member"},
    )
    assert invite.status_code == 201, invite.text
    member_id = invite.json()["employee"]["id"]

    assigned = client.post(
        f"/api/v1/companies/{company_id}/cabinets/{cabinet_id}/assignments",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"employee_id": member_id},
    )
    assert assigned.status_code == 200, assigned.text

    member_tok = _token(sub="member-grant", email="member@grantco.test")
    listed = client.get("/api/v1/cabinets", headers={"Authorization": f"Bearer {member_tok}"})
    assert listed.status_code == 200, listed.text
    assert any(i["id"] == cabinet_id for i in listed.json()["items"])

    meta_denied = client.put(
        f"/api/v1/cabinets/{cabinet_id}/meta/documents/ui",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"body": {"version": 1}},
    )
    assert meta_denied.status_code == 403
