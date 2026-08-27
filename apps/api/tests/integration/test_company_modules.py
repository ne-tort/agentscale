"""Company modules — grants, local CRUD, cabinet bind, RO platform meta."""

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


def _setup_company_with_cabinets(client: TestClient, admin: str) -> tuple[str, str, str, str, str]:
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "ModCo", "password": "test-company-pass", "admin_email": "boss@modco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    boss_tok = _token(sub="boss-mod", email="boss@modco.test")
    cab1 = client.post(
        "/api/v1/admin/cabinets",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Cab One", "company_ids": [company_id]},
    )
    assert cab1.status_code == 200, cab1.text
    cab2 = client.post(
        "/api/v1/admin/cabinets",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Cab Two", "company_ids": [company_id]},
    )
    assert cab2.status_code == 200, cab2.text
    return company_id, cab1.json()["id"], cab2.json()["id"], boss_tok, admin


@requires_postgres
def test_company_module_grant_bind_and_local_crud(client: TestClient) -> None:
    admin = _token(sub="padmin-cmod", platform_admin=True)
    company_id, cab1_id, cab2_id, boss_tok, admin_tok = _setup_company_with_cabinets(client, admin)
    h = {"Authorization": f"Bearer {boss_tok}"}

    mod = client.post(
        "/api/v1/admin/modules",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"name": "Platform Pack"},
    )
    assert mod.status_code == 200, mod.text
    platform_module_id = mod.json()["id"]

    client.put(
        f"/api/v1/admin/modules/{platform_module_id}/meta/documents/tables",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"body": [{"slug": "items", "label": "Items"}]},
    )

    granted = client.patch(
        f"/api/v1/admin/modules/{platform_module_id}",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"company_ids": [company_id]},
    )
    assert granted.status_code == 200, granted.text
    assert company_id in granted.json()["company_ids"]

    listed = client.get(f"/api/v1/companies/{company_id}/modules", headers=h)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(m["id"] == platform_module_id for m in items)
    platform_row = next(m for m in items if m["id"] == platform_module_id)
    assert platform_row["writable"] is False
    assert platform_row["source"] == "platform_assigned"

    bound = client.patch(
        f"/api/v1/companies/{company_id}/modules/{platform_module_id}",
        headers=h,
        json={"cabinet_ids": [cab1_id, cab2_id]},
    )
    assert bound.status_code == 200, bound.text
    assert set(bound.json()["cabinet_ids"]) == {cab1_id, cab2_id}

    rename_denied = client.patch(
        f"/api/v1/companies/{company_id}/modules/{platform_module_id}",
        headers=h,
        json={"name": "Hacked"},
    )
    assert rename_denied.status_code == 403

    meta_put_denied = client.put(
        f"/api/v1/companies/{company_id}/modules/{platform_module_id}/meta/documents/tables",
        headers=h,
        json={"body": []},
    )
    assert meta_put_denied.status_code == 403

    meta_get = client.get(
        f"/api/v1/companies/{company_id}/modules/{platform_module_id}/meta/documents/tables",
        headers=h,
    )
    assert meta_get.status_code == 200
    assert meta_get.json()["body"][0]["slug"] == "items"

    local = client.post(
        f"/api/v1/companies/{company_id}/modules",
        headers=h,
        json={"name": "Local Pack"},
    )
    assert local.status_code == 201, local.text
    local_id = local.json()["id"]
    assert local.json()["writable"] is True
    assert local.json()["source"] == "company_local"

    renamed = client.patch(
        f"/api/v1/companies/{company_id}/modules/{local_id}",
        headers=h,
        json={"name": "Local Pack v2", "cabinet_ids": [cab1_id]},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Local Pack v2"
    assert renamed.json()["cabinet_ids"] == [cab1_id]

    client.put(
        f"/api/v1/companies/{company_id}/modules/{local_id}/meta/documents/tables",
        headers=h,
        json={"body": [{"slug": "local_tbl", "label": "Local"}]},
    )

    deleted = client.delete(f"/api/v1/companies/{company_id}/modules/{local_id}", headers=h)
    assert deleted.status_code == 200, deleted.text

    delete_platform = client.delete(
        f"/api/v1/companies/{company_id}/modules/{platform_module_id}",
        headers=h,
    )
    assert delete_platform.status_code == 403


@requires_postgres
def test_revoking_company_grant_clears_cabinet_bindings(client: TestClient) -> None:
    admin = _token(sub="padmin-cmod-revoke", platform_admin=True)
    company_id, cab1_id, _cab2_id, boss_tok, admin_tok = _setup_company_with_cabinets(client, admin)
    h = {"Authorization": f"Bearer {boss_tok}"}

    mod = client.post(
        "/api/v1/admin/modules",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"name": "Revoke Pack"},
    )
    assert mod.status_code == 200, mod.text
    module_id = mod.json()["id"]

    client.patch(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"company_ids": [company_id]},
    )
    bound = client.patch(
        f"/api/v1/companies/{company_id}/modules/{module_id}",
        headers=h,
        json={"cabinet_ids": [cab1_id]},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["cabinet_ids"] == [cab1_id]

    revoked = client.patch(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"company_ids": []},
    )
    assert revoked.status_code == 200, revoked.text

    admin_view = client.get(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert admin_view.status_code == 200
    assert cab1_id not in admin_view.json()["cabinet_ids"]

    listed = client.get(f"/api/v1/companies/{company_id}/modules", headers=h)
    assert listed.status_code == 200
    assert not any(m["id"] == module_id for m in listed.json()["items"])
