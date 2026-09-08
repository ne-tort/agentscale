"""L2 — local/global module binds, lock flags, SoT isolation / sharing."""

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


def _workspace_for_template(
    client: TestClient, *, company_id: str, template_id: str, owner_tok: str
) -> str:
    org = client.get(
        f"/api/v1/companies/{company_id}/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert org.status_code == 200, org.text
    workspace = next(
        (i for i in org.json()["items"] if i.get("template_cabinet_id") == template_id),
        None,
    )
    assert workspace is not None, org.json()["items"]
    return workspace["id"]


def _setup(client: TestClient, *, stamp: str) -> tuple[str, str, str, str, str]:
    admin = _token(sub=f"padmin-bind-{stamp}", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": f"BindCo {stamp}",
            "password": "test-company-pass",
            "admin_email": f"boss-{stamp}@bindco.test",
        },
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created.json())
    cab = client.post(
        "/api/v1/admin/cabinets",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": f"BindCab {stamp}", "company_ids": [company_id]},
    )
    assert cab.status_code == 200, cab.text
    template_id = cab.json()["id"]
    cab_ws = _workspace_for_template(
        client, company_id=company_id, template_id=template_id, owner_tok=owner_tok
    )
    return admin, company_id, cab_ws, owner_tok, template_id


def _create_module(
    client: TestClient,
    *,
    admin: str,
    company_id: str,
    cab_ws: str,
    owner_tok: str,
    name: str,
) -> str:
    mod = client.post(
        "/api/v1/admin/modules",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": name},
    )
    assert mod.status_code == 200, mod.text
    module_id = mod.json()["id"]
    granted = client.patch(
        f"/api/v1/admin/modules/{module_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"company_ids": [company_id]},
    )
    assert granted.status_code == 200, granted.text
    tables = client.put(
        f"/api/v1/admin/modules/{module_id}/meta/documents/tables",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "body": [
                {
                    "slug": "notes",
                    "label": "Notes",
                    "storage_kind": "json_document",
                }
            ]
        },
    )
    assert tables.status_code == 200, tables.text
    bound = client.patch(
        f"/api/v1/companies/{company_id}/modules/{module_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"cabinet_ids": [cab_ws]},
    )
    assert bound.status_code == 200, bound.text
    return module_id


def _create_project(
    client: TestClient, *, cab_ws: str, owner_tok: str, name: str
) -> str:
    proj = client.post(
        f"/api/v1/cabinets/{cab_ws}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": name},
    )
    assert proj.status_code == 201, proj.text
    return proj.json()["id"]


def _bind(
    client: TestClient,
    *,
    admin: str,
    module_id: str,
    project_id: str,
    bind_kind: str,
    child_may_edit: bool | None = None,
) -> dict:
    payload: dict = {"bind_kind": bind_kind}
    if child_may_edit is not None:
        payload["child_may_edit"] = child_may_edit
    res = client.post(
        f"/api/v1/admin/modules/{module_id}/projects/{project_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json=payload,
    )
    assert res.status_code == 200, res.text
    return res.json()


@requires_postgres
def test_local_bind_isolates_project_sot(client: TestClient) -> None:
    admin, company_id, cab_ws, owner_tok, _ = _setup(client, stamp="local")
    module_id = _create_module(
        client,
        admin=admin,
        company_id=company_id,
        cab_ws=cab_ws,
        owner_tok=owner_tok,
        name="Local Notes",
    )
    p1 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="P1")
    p2 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="P2")
    b1 = _bind(client, admin=admin, module_id=module_id, project_id=p1, bind_kind="local")
    b2 = _bind(client, admin=admin, module_id=module_id, project_id=p2, bind_kind="local")
    assert b1["bind_kind"] == "local"
    assert b1["child_may_edit"] is True
    assert b2["bind_kind"] == "local"

    r1 = client.post(
        f"/api/v1/projects/{p1}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "alpha"}},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/api/v1/projects/{p2}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "beta"}},
    )
    assert r2.status_code == 200, r2.text
    assert r1.json()["instance_id"] != r2.json()["instance_id"]

    list1 = client.get(
        f"/api/v1/projects/{p1}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    list2 = client.get(
        f"/api/v1/projects/{p2}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert list1.status_code == 200
    assert list2.status_code == 200
    titles1 = {i["body"]["title"] for i in list1.json()["items"]}
    titles2 = {i["body"]["title"] for i in list2.json()["items"]}
    assert titles1 == {"alpha"}
    assert titles2 == {"beta"}

    mods = client.get(
        f"/api/v1/projects/{p1}/modules",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert mods.status_code == 200
    item = next(i for i in mods.json()["items"] if i["module_id"] == module_id)
    assert item["enabled"] is True
    assert item["bind_kind"] == "local"
    assert item["child_may_edit"] is True


@requires_postgres
def test_global_locked_share_sot_and_forbid_project_write(client: TestClient) -> None:
    admin, company_id, cab_ws, owner_tok, _ = _setup(client, stamp="glock")
    module_id = _create_module(
        client,
        admin=admin,
        company_id=company_id,
        cab_ws=cab_ws,
        owner_tok=owner_tok,
        name="Global Locked",
    )
    p1 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="G1")
    p2 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="G2")
    _bind(
        client,
        admin=admin,
        module_id=module_id,
        project_id=p1,
        bind_kind="global",
        child_may_edit=False,
    )
    _bind(
        client,
        admin=admin,
        module_id=module_id,
        project_id=p2,
        bind_kind="global",
        child_may_edit=False,
    )

    # Seed via cabinet SoT (writable for local MC default).
    seeded = client.post(
        f"/api/v1/cabinets/{cab_ws}/modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "shared"}},
    )
    assert seeded.status_code == 200, seeded.text
    cab_instance = seeded.json()["instance_id"]

    listed = client.get(
        f"/api/v1/projects/{p1}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) >= 1
    assert items[0]["instance_id"] == cab_instance
    assert items[0]["body"]["title"] == "shared"

    listed2 = client.get(
        f"/api/v1/projects/{p2}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed2.status_code == 200
    assert listed2.json()["items"][0]["instance_id"] == cab_instance

    forbidden = client.post(
        f"/api/v1/projects/{p1}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "nope"}},
    )
    assert forbidden.status_code == 403, forbidden.text

    mods = client.get(
        f"/api/v1/projects/{p1}/modules",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    item = next(i for i in mods.json()["items"] if i["module_id"] == module_id)
    assert item["bind_kind"] == "global"
    assert item["child_may_edit"] is False


@requires_postgres
def test_global_unlocked_allows_shared_write(client: TestClient) -> None:
    admin, company_id, cab_ws, owner_tok, _ = _setup(client, stamp="gunlock")
    module_id = _create_module(
        client,
        admin=admin,
        company_id=company_id,
        cab_ws=cab_ws,
        owner_tok=owner_tok,
        name="Global Unlocked",
    )
    p1 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="U1")
    p2 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="U2")
    _bind(
        client,
        admin=admin,
        module_id=module_id,
        project_id=p1,
        bind_kind="global",
        child_may_edit=True,
    )
    _bind(
        client,
        admin=admin,
        module_id=module_id,
        project_id=p2,
        bind_kind="global",
        child_may_edit=True,
    )

    created = client.post(
        f"/api/v1/projects/{p1}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "from-p1"}},
    )
    assert created.status_code == 200, created.text
    shared_id = created.json()["instance_id"]

    via_p2 = client.get(
        f"/api/v1/projects/{p2}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert via_p2.status_code == 200
    assert via_p2.json()["items"][0]["instance_id"] == shared_id
    assert via_p2.json()["items"][0]["body"]["title"] == "from-p1"


@requires_postgres
def test_employee_bind_unbind_and_bound_projects(client: TestClient) -> None:
    admin, company_id, cab_ws, owner_tok, _ = _setup(client, stamp="emp")
    module_id = _create_module(
        client,
        admin=admin,
        company_id=company_id,
        cab_ws=cab_ws,
        owner_tok=owner_tok,
        name="Employee Bind",
    )
    p1 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="E1")
    p2 = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="E2")

    empty = client.get(
        f"/api/v1/cabinets/{cab_ws}/modules/{module_id}/bound-projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    bound = client.post(
        f"/api/v1/projects/{p1}/modules/{module_id}/bind",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"bind_kind": "local"},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["bind_kind"] == "local"

    listed = client.get(
        f"/api/v1/cabinets/{cab_ws}/modules/{module_id}/bound-projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200
    ids = {i["project_id"] for i in listed.json()["items"]}
    assert p1 in ids
    assert p2 not in ids

    revoked = client.delete(
        f"/api/v1/projects/{p1}/modules/{module_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert revoked.status_code == 200, revoked.text

    after = client.get(
        f"/api/v1/cabinets/{cab_ws}/modules/{module_id}/bound-projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert after.status_code == 200
    ids_after = {i["project_id"] for i in after.json()["items"]}
    assert p1 not in ids_after


@requires_postgres
def test_switch_local_to_global_drops_project_leaf(client: TestClient) -> None:
    admin, company_id, cab_ws, owner_tok, _ = _setup(client, stamp="switch")
    module_id = _create_module(
        client,
        admin=admin,
        company_id=company_id,
        cab_ws=cab_ws,
        owner_tok=owner_tok,
        name="Switch Bind",
    )
    project_id = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="S1")
    _bind(client, admin=admin, module_id=module_id, project_id=project_id, bind_kind="local")

    local_row = client.post(
        f"/api/v1/projects/{project_id}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "leaf"}},
    )
    assert local_row.status_code == 200, local_row.text
    leaf_id = local_row.json()["instance_id"]

    switched = _bind(
        client,
        admin=admin,
        module_id=module_id,
        project_id=project_id,
        bind_kind="global",
        child_may_edit=False,
    )
    assert switched["bind_kind"] == "global"

    # After switch, reads resolve to cabinet SoT (different instance).
    cab_seed = client.post(
        f"/api/v1/cabinets/{cab_ws}/modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"body": {"title": "cab"}},
    )
    assert cab_seed.status_code == 200, cab_seed.text
    cab_id = cab_seed.json()["instance_id"]
    assert cab_id != leaf_id

    via_project = client.get(
        f"/api/v1/projects/{project_id}/runtime-modules/{module_id}/data/notes",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert via_project.status_code == 200
    assert via_project.json()["items"][0]["instance_id"] == cab_id


@requires_postgres
def test_set_module_ids_empty_does_not_keep_stale_binds(client: TestClient) -> None:
    admin, company_id, cab_ws, owner_tok, _ = _setup(client, stamp="empty")
    module_id = _create_module(
        client,
        admin=admin,
        company_id=company_id,
        cab_ws=cab_ws,
        owner_tok=owner_tok,
        name="Empty Patch",
    )
    project_id = _create_project(client, cab_ws=cab_ws, owner_tok=owner_tok, name="EP")
    _bind(client, admin=admin, module_id=module_id, project_id=project_id, bind_kind="local")

    cleared = client.patch(
        f"/api/v1/projects/{project_id}/modules",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"module_ids": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["module_ids"] == []

    mods = client.get(
        f"/api/v1/projects/{project_id}/modules",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    item = next(i for i in mods.json()["items"] if i["module_id"] == module_id)
    assert item["enabled"] is False
    assert item["bind_kind"] is None
