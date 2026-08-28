"""Admin modules — CRUD, meta, cabinet/project bindings."""

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
from tests.integration.support import owner_bearer_token


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


def _setup_two_cabinets(client: TestClient, admin: str) -> tuple[str, str, str, str, str]:
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "ModCo", "password": "test-company-pass", "admin_email": "boss@modco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
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
    owner_tok = owner_bearer_token(_token, created.json())
    return company_id, cab1.json()["id"], cab2.json()["id"], owner_tok, admin


def _company_bind_cabinets(
    client: TestClient,
    *,
    company_id: str,
    module_id: str,
    cabinet_ids: list[str],
    token: str,
) -> None:
    res = client.patch(
        f"/api/v1/companies/{company_id}/modules/{module_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"cabinet_ids": cabinet_ids},
    )
    assert res.status_code == 200, res.text


@requires_postgres
def test_admin_module_crud_meta_and_bindings(client: TestClient) -> None:
    admin = _token(sub="padmin-mod", platform_admin=True)
    company_id, cab1_id, cab2_id, owner_tok, _admin = _setup_two_cabinets(client, admin)

    mod = client.post(
        "/api/v1/admin/modules",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Suppliers Pack"},
    )
    assert mod.status_code == 200, mod.text
    body = mod.json()
    assert body["id"].startswith("mod_")
    module_id = body["id"]

    granted = client.patch(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"company_ids": [company_id]},
    )
    assert granted.status_code == 200, granted.text
    assert company_id in granted.json()["company_ids"]

    _company_bind_cabinets(
        client,
        company_id=company_id,
        module_id=module_id,
        cabinet_ids=[cab1_id, cab2_id],
        token=owner_tok,
    )

    put = client.put(
        f"/api/v1/admin/modules/{module_id}/meta/documents/tables",
        headers={"Authorization": f"Bearer {admin}"},
        json={"body": [{"slug": "suppliers", "label": "Suppliers"}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["slug"] == "tables"

    proj = client.post(
        f"/api/v1/cabinets/{cab1_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Mod Project"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    bound = client.post(
        f"/api/v1/admin/modules/{module_id}/projects/{project_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert bound.status_code == 200, bound.text

    proj2 = client.post(
        f"/api/v1/cabinets/{cab2_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Other Cab Project"},
    )
    assert proj2.status_code == 201, proj2.text
    denied = client.post(
        f"/api/v1/admin/modules/{module_id}/projects/{proj2.json()['id']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert denied.status_code == 422

    unbind_cab = client.patch(
        f"/api/v1/companies/{company_id}/modules/{module_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"cabinet_ids": [cab2_id]},
    )
    assert unbind_cab.status_code == 200, unbind_cab.text
    got = client.get(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert got.status_code == 200
    assert project_id not in got.json()["project_ids"]

    deleted = client.delete(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert deleted.status_code == 200, deleted.text

    cab_still = client.get(
        f"/api/v1/admin/cabinets/{cab1_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert cab_still.status_code == 200
    proj_still = client.get(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert proj_still.status_code == 200


@requires_postgres
def test_module_shared_template_isolated_data_per_cabinet(client: TestClient) -> None:
    admin = _token(sub="padmin-mod-data", platform_admin=True)
    company_id, cab1_id, cab2_id, owner_tok, _admin = _setup_two_cabinets(client, admin)

    mod = client.post(
        "/api/v1/admin/modules",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Shared Catalog"},
    )
    assert mod.status_code == 200, mod.text
    module_id = mod.json()["id"]

    client.put(
        f"/api/v1/admin/modules/{module_id}/meta/documents/tables",
        headers={"Authorization": f"Bearer {admin}"},
        json={"body": [{"slug": "suppliers", "label": "Suppliers", "storage_kind": "json_document"}]},
    )

    client.patch(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"company_ids": [company_id]},
    )
    _company_bind_cabinets(
        client,
        company_id=company_id,
        module_id=module_id,
        cabinet_ids=[cab1_id, cab2_id],
        token=owner_tok,
    )

    for cab_id, name in ((cab1_id, "Alpha"), (cab2_id, "Beta")):
        created = client.post(
            f"/api/v1/cabinets/{cab_id}/modules/{module_id}/data/suppliers",
            headers={"Authorization": f"Bearer {owner_tok}"},
            json={"body": {"name": name}},
        )
        assert created.status_code == 200, created.text
        assert created.json()["body"]["name"] == name

    cab1_rows = client.get(
        f"/api/v1/cabinets/{cab1_id}/modules/{module_id}/data/suppliers",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    cab2_rows = client.get(
        f"/api/v1/cabinets/{cab2_id}/modules/{module_id}/data/suppliers",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert cab1_rows.status_code == 200
    assert cab2_rows.status_code == 200
    assert cab1_rows.json()["items"][0]["body"]["name"] == "Alpha"
    assert cab2_rows.json()["items"][0]["body"]["name"] == "Beta"

    meta = client.get(
        f"/api/v1/cabinets/{cab1_id}/modules/{module_id}/meta/documents/tables",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert meta.status_code == 200
    assert meta.json()["body"][0]["slug"] == "suppliers"

    modules = client.get(
        f"/api/v1/cabinets/{cab1_id}/modules",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert modules.status_code == 200
    assert any(m["id"] == module_id for m in modules.json()["items"])
