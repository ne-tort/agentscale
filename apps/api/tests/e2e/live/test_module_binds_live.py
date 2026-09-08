"""L3b live — local/global module binds against deployed Traefik + Keycloak."""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.conftest import requires_live_api
from tests.e2e.live.keycloak_auth import (
    EMPLOYEE_PASSWORD,
    auth_header,
    fetch_platform_admin_token,
    login_via_api,
)

pytestmark = [pytest.mark.live, requires_live_api]


def _setup_company_cabinet_module(live_client, api: str, *, stamp: str, label: str):
    admin_tok = fetch_platform_admin_token(live_client, api)
    admin_h = auth_header(admin_tok)
    owner_login = f"{label}{stamp}"[:24]
    owner_email = f"{label}-{stamp}@e2e.local"

    co = live_client.post(
        f"{api}/companies",
        headers=admin_h,
        json={"name": f"Live{label} {stamp}", "password": "test-company-pass"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    inv = live_client.post(
        f"{api}/companies/{company_id}/employees",
        headers=admin_h,
        json={
            "login": owner_login,
            "password": EMPLOYEE_PASSWORD,
            "contact_email": owner_email,
            "display_name": f"{label} Owner",
            "role": "company.admin",
        },
    )
    assert inv.status_code in (200, 201), inv.text
    owner_h = auth_header(
        login_via_api(live_client, api, username=owner_login, password=EMPLOYEE_PASSWORD)
    )

    cab = live_client.post(
        f"{api}/cabinets",
        headers=owner_h,
        json={"name": f"{label}Cab {stamp}", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    mod = live_client.post(
        f"{api}/admin/modules",
        headers=admin_h,
        json={"name": f"{label} Mod {stamp}"},
    )
    assert mod.status_code == 200, mod.text
    module_id = mod.json()["id"]

    # Create platform SoT before grant/bind (meta write → ensure_platform_instance).
    tables = live_client.put(
        f"{api}/admin/modules/{module_id}/meta/documents/tables",
        headers=admin_h,
        json={
            "body": [
                {"slug": "notes", "label": "Notes", "storage_kind": "json_document"}
            ]
        },
    )
    assert tables.status_code == 200, tables.text

    # Platform modules default to global company grant — SoT is platform instance.
    granted = live_client.patch(
        f"{api}/admin/modules/{module_id}",
        headers=admin_h,
        json={"company_ids": [company_id]},
    )
    assert granted.status_code == 200, granted.text
    assert company_id in granted.json()["company_ids"]

    bound = live_client.patch(
        f"{api}/companies/{company_id}/modules/{module_id}",
        headers=owner_h,
        json={"cabinet_ids": [cabinet_id]},
    )
    assert bound.status_code == 200, bound.text
    assert cabinet_id in bound.json()["cabinet_ids"]

    return admin_h, owner_h, company_id, cabinet_id, module_id


def test_live_local_and_global_locked_binds(live_client, live_api_prefix: str) -> None:
    stamp = datetime.now().strftime("%H%M%S%f")
    api = live_api_prefix
    admin_h, owner_h, _company_id, cabinet_id, module_id = _setup_company_cabinet_module(
        live_client, api, stamp=stamp, label="bind"
    )

    p_local = live_client.post(
        f"{api}/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": f"Local {stamp}"},
    )
    assert p_local.status_code == 201, p_local.text
    local_id = p_local.json()["id"]

    p_global = live_client.post(
        f"{api}/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": f"Global {stamp}"},
    )
    assert p_global.status_code == 201, p_global.text
    global_id = p_global.json()["id"]

    local_bind = live_client.post(
        f"{api}/projects/{local_id}/modules/{module_id}/bind",
        headers=owner_h,
        json={"bind_kind": "local"},
    )
    assert local_bind.status_code == 200, local_bind.text
    assert local_bind.json()["bind_kind"] == "local"

    local_row = live_client.post(
        f"{api}/projects/{local_id}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
        json={"body": {"title": "live-local"}},
    )
    assert local_row.status_code == 200, local_row.text
    local_instance = local_row.json()["instance_id"]

    global_bind = live_client.post(
        f"{api}/projects/{global_id}/modules/{module_id}/bind",
        headers=owner_h,
        json={"bind_kind": "global", "child_may_edit": False},
    )
    assert global_bind.status_code == 200, global_bind.text
    assert global_bind.json()["bind_kind"] == "global"
    assert global_bind.json()["child_may_edit"] is False

    cab_row = live_client.post(
        f"{api}/cabinets/{cabinet_id}/modules/{module_id}/data/notes",
        headers=owner_h,
        json={"body": {"title": "live-cab"}},
    )
    assert cab_row.status_code == 200, cab_row.text
    cab_instance = cab_row.json()["instance_id"]
    assert cab_instance != local_instance

    via_global = live_client.get(
        f"{api}/projects/{global_id}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
    )
    assert via_global.status_code == 200, via_global.text
    titles = {i["body"].get("title") for i in via_global.json()["items"]}
    assert "live-cab" in titles
    assert via_global.json()["items"][0]["instance_id"] == cab_instance

    forbidden = live_client.post(
        f"{api}/projects/{global_id}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
        json={"body": {"title": "blocked"}},
    )
    assert forbidden.status_code == 403, forbidden.text

    bound = live_client.get(
        f"{api}/cabinets/{cabinet_id}/modules/{module_id}/bound-projects",
        headers=owner_h,
    )
    assert bound.status_code == 200, bound.text
    bound_ids = {i["project_id"] for i in bound.json()["items"]}
    assert local_id in bound_ids
    assert global_id in bound_ids

    mods = live_client.get(f"{api}/projects/{global_id}/modules", headers=owner_h)
    assert mods.status_code == 200, mods.text
    item = next(i for i in mods.json()["items"] if i["module_id"] == module_id)
    assert item["bind_kind"] == "global"
    assert item["child_may_edit"] is False

    live_client.delete(f"{api}/projects/{local_id}/modules/{module_id}", headers=owner_h)
    live_client.delete(f"{api}/projects/{global_id}/modules/{module_id}", headers=owner_h)
    live_client.delete(f"{api}/admin/modules/{module_id}", headers=admin_h)


def test_live_global_unlocked_shared_write(live_client, live_api_prefix: str) -> None:
    stamp = datetime.now().strftime("%H%M%S%f")
    api = live_api_prefix
    admin_h, owner_h, _company_id, cabinet_id, module_id = _setup_company_cabinet_module(
        live_client, api, stamp=stamp, label="gunl"
    )

    projects: list[str] = []
    for label in ("A", "B"):
        p = live_client.post(
            f"{api}/cabinets/{cabinet_id}/projects",
            headers=owner_h,
            json={"name": f"{label} {stamp}"},
        )
        assert p.status_code == 201, p.text
        pid = p.json()["id"]
        projects.append(pid)
        b = live_client.post(
            f"{api}/projects/{pid}/modules/{module_id}/bind",
            headers=owner_h,
            json={"bind_kind": "global", "child_may_edit": True},
        )
        assert b.status_code == 200, b.text
        assert b.json()["child_may_edit"] is True

    created = live_client.post(
        f"{api}/projects/{projects[0]}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
        json={"body": {"title": "shared-live"}},
    )
    assert created.status_code == 200, created.text
    shared = created.json()["instance_id"]

    other = live_client.get(
        f"{api}/projects/{projects[1]}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
    )
    assert other.status_code == 200, other.text
    assert other.json()["items"][0]["instance_id"] == shared
    assert other.json()["items"][0]["body"]["title"] == "shared-live"

    for pid in projects:
        live_client.delete(f"{api}/projects/{pid}/modules/{module_id}", headers=owner_h)
    live_client.delete(f"{api}/admin/modules/{module_id}", headers=admin_h)


def test_live_unbind_and_rebind_switch(live_client, live_api_prefix: str) -> None:
    stamp = datetime.now().strftime("%H%M%S%f")
    api = live_api_prefix
    admin_h, owner_h, _company_id, cabinet_id, module_id = _setup_company_cabinet_module(
        live_client, api, stamp=stamp, label="swch"
    )

    p = live_client.post(
        f"{api}/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": f"Switch {stamp}"},
    )
    assert p.status_code == 201, p.text
    project_id = p.json()["id"]

    local = live_client.post(
        f"{api}/projects/{project_id}/modules/{module_id}/bind",
        headers=owner_h,
        json={"bind_kind": "local"},
    )
    assert local.status_code == 200, local.text

    row = live_client.post(
        f"{api}/projects/{project_id}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
        json={"body": {"title": "forked"}},
    )
    assert row.status_code == 200, row.text
    forked = row.json()["instance_id"]

    switched = live_client.post(
        f"{api}/projects/{project_id}/modules/{module_id}/bind",
        headers=owner_h,
        json={"bind_kind": "global", "child_may_edit": True},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["bind_kind"] == "global"

    # Shared write goes to parent SoT (not the abandoned local fork).
    shared = live_client.post(
        f"{api}/projects/{project_id}/runtime-modules/{module_id}/data/notes",
        headers=owner_h,
        json={"body": {"title": "after-switch"}},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["instance_id"] != forked

    cab_view = live_client.get(
        f"{api}/cabinets/{cabinet_id}/modules/{module_id}/data/notes",
        headers=owner_h,
    )
    assert cab_view.status_code == 200, cab_view.text
    titles = {i["body"].get("title") for i in cab_view.json()["items"]}
    assert "after-switch" in titles

    live_client.delete(f"{api}/projects/{project_id}/modules/{module_id}", headers=owner_h)
    live_client.delete(f"{api}/admin/modules/{module_id}", headers=admin_h)
