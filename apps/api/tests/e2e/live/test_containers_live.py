"""L3b — live HTTP e2e against deployed Traefik (:8088) + Keycloak (:8089)."""

from __future__ import annotations

import time
from datetime import datetime

import httpx
import pytest

from tests.conftest import requires_live_api
from tests.e2e.live.keycloak_auth import (
    EMPLOYEE_PASSWORD,
    auth_header,
    fetch_platform_admin_token,
    login_via_api,
)

pytestmark = [pytest.mark.live, requires_live_api]


def _wait_runtime_running(
    client: httpx.Client,
    api: str,
    headers: dict[str, str],
    project_id: str,
    *,
    wait_sec: float = 180.0,
) -> dict:
    """Wait until project.runtime.status is running (pod ready for agent)."""
    deadline = time.time() + wait_sec
    last: dict = {}
    while time.time() < deadline:
        got = client.get(f"{api}/projects/{project_id}", headers=headers)
        assert got.status_code == 200, got.text
        last = got.json()
        if last.get("status") == "error":
            raise AssertionError(f"project error while waiting for runtime: {last}")
        runtime = last.get("runtime") or {}
        if (
            last.get("status") == "active"
            and runtime.get("observed_state") == "running"
            and runtime.get("desired_state") == "running"
        ):
            return last
        time.sleep(2.0)
    raise AssertionError(f"timeout waiting for running runtime: {last}")


def _wait_project_status(
    client: httpx.Client,
    api: str,
    headers: dict[str, str],
    project_id: str,
    *,
    want: str,
    wait_sec: float = 180.0,
    via_admin: bool = False,
) -> dict:
    """Poll project/container until status matches (launch sync; resume is async)."""
    path = (
        f"{api}/admin/containers/{project_id}"
        if via_admin
        else f"{api}/projects/{project_id}"
    )
    deadline = time.time() + wait_sec
    last: dict = {}
    while time.time() < deadline:
        got = client.get(path, headers=headers)
        assert got.status_code == 200, got.text
        last = got.json()
        status = last.get("status")
        if status == want:
            return last
        if status == "error":
            raise AssertionError(f"project entered error while waiting for {want}: {last}")
        time.sleep(2.0)
    raise AssertionError(
        f"timeout waiting for status={want!r} got={last.get('status')!r}: {last}"
    )


def _configure_and_launch(
    client: httpx.Client,
    api: str,
    owner_h: dict[str, str],
    project_id: str,
    *,
    wait_sec: float = 180.0,
) -> dict:
    """Patch AI key + launch; wait until project is active (real k8s provision)."""
    keys = client.get(f"{api}/projects/{project_id}/ai-keys/available", headers=owner_h)
    assert keys.status_code == 200, keys.text
    items = keys.json().get("items") or []
    assert items, keys.text
    key_id = items[0]["id"]
    patched = client.patch(
        f"{api}/projects/{project_id}",
        headers=owner_h,
        json={"agent_provider": "cursor", "resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text

    # If a prior launch attempt already provisioned a pod (500 after create, race),
    # skip re-launch and just wait for active/running.
    current = client.get(f"{api}/projects/{project_id}", headers=owner_h)
    assert current.status_code == 200, current.text
    cur_body = current.json()
    if cur_body.get("runtime") is not None or cur_body.get("status") == "active":
        _wait_project_status(
            client, api, owner_h, project_id, want="active", wait_sec=wait_sec
        )
        return _wait_runtime_running(
            client, api, owner_h, project_id, wait_sec=wait_sec
        )

    launched = client.post(f"{api}/projects/{project_id}/launch", headers=owner_h)
    if launched.status_code not in (200, 409):
        # One retry — concurrent e2e / rematerialize can briefly 500 the API.
        time.sleep(2.0)
        launched = client.post(f"{api}/projects/{project_id}/launch", headers=owner_h)
    # 409 POD_ALREADY_EXISTS: pod row exists after a partial prior launch — wait it out.
    assert launched.status_code in (200, 409), launched.text
    _wait_project_status(
        client, api, owner_h, project_id, want="active", wait_sec=wait_sec
    )
    return _wait_runtime_running(
        client, api, owner_h, project_id, wait_sec=wait_sec
    )


def test_live_health_and_auth_config(live_client, live_api_prefix: str) -> None:
    live = live_client.get("/health/live")
    assert live.status_code == 200
    ready = live_client.get("/health/ready")
    assert ready.status_code == 200
    cfg = live_client.get(f"{live_api_prefix}/auth/config")
    assert cfg.status_code == 200
    assert "auth_mode" in cfg.json()


def test_live_containers_lifecycle(live_client, live_api_prefix: str) -> None:
    """Vertical containers flow: draft → launch → pause/resume → agent → delete."""
    stamp = datetime.now().strftime("%H%M%S")
    owner_email = f"live-owner-{stamp}@e2e.local"
    owner_login = f"owner{stamp}"
    admin_tok = fetch_platform_admin_token(live_client, live_api_prefix)
    admin_h = auth_header(admin_tok)
    api = live_api_prefix

    co = live_client.post(
        f"{api}/companies",
        headers=admin_h,
        json={
            "name": f"LiveCtr {stamp}",
            "password": "test-company-pass",
        },
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    owner_inv = live_client.post(
        f"{api}/companies/{company_id}/employees",
        headers=admin_h,
        json={
            "login": owner_login,
            "password": EMPLOYEE_PASSWORD,
            "contact_email": owner_email,
            "display_name": "Owner",
            "role": "company.admin",
        },
    )
    assert owner_inv.status_code in (200, 201), owner_inv.text

    key = live_client.post(
        f"{api}/admin/ai-keys",
        headers=admin_h,
        json={
            "name": f"LiveKey {stamp}",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": f"sk-live-{stamp}",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    owner_tok = login_via_api(
        live_client,
        api,
        username=owner_login,
        password=EMPLOYEE_PASSWORD,
    )
    owner_h = auth_header(owner_tok)
    cab = live_client.post(
        f"{api}/cabinets",
        headers=owner_h,
        json={"name": f"LiveCab {stamp}", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    peer_login = f"peer{stamp}"
    inv = live_client.post(
        f"{api}/companies/{company_id}/employees",
        headers=admin_h,
        json={
            "login": peer_login,
            "password": EMPLOYEE_PASSWORD,
            "contact_email": f"peer-{stamp}@e2e.local",
            "display_name": "Peer",
            "role": "member",
        },
    )
    assert inv.status_code in (200, 201), inv.text
    peer_tok = login_via_api(
        live_client,
        api,
        username=peer_login,
        password=EMPLOYEE_PASSWORD,
    )

    denied = live_client.get(f"{api}/admin/containers", headers=auth_header(peer_tok))
    assert denied.status_code == 403

    projects: list[dict] = []
    for name in ("ProjA", "ProjB"):
        proj = live_client.post(
            f"{api}/cabinets/{cabinet_id}/projects",
            headers=owner_h,
            json={"name": f"{name} {stamp}"},
        )
        assert proj.status_code == 201, proj.text
        body = proj.json()
        assert body.get("status") == "draft"
        projects.append(body)

    assert projects[0]["workspace_key"] != projects[1]["workspace_key"]
    assert projects[0]["container_ref"].startswith("object-ws:")

    listed_draft = live_client.get(f"{api}/admin/containers", headers=admin_h)
    assert listed_draft.status_code == 200
    draft_ids = {i["project_id"] for i in listed_draft.json()["items"]}
    assert projects[0]["id"] in draft_ids and projects[1]["id"] in draft_ids
    row_draft = next(
        i for i in listed_draft.json()["items"] if i["project_id"] == projects[0]["id"]
    )
    assert row_draft["status"] == "draft"
    assert row_draft["k8s_phase"] is None
    assert row_draft["company_name"].startswith("LiveCtr")

    # Pause without a pod must fail (product rule since draft lifecycle).
    no_pod = live_client.post(
        f"{api}/admin/containers/{projects[1]['id']}/pause",
        headers=admin_h,
    )
    assert no_pod.status_code == 422, no_pod.text
    detail = (no_pod.json().get("detail") or no_pod.json().get("message") or "").lower()
    assert "no pod" in detail

    for p in projects:
        _configure_and_launch(live_client, api, owner_h, p["id"])

    listed = live_client.get(f"{api}/admin/containers", headers=admin_h)
    assert listed.status_code == 200
    active_rows = [
        i
        for i in listed.json()["items"]
        if i["project_id"] in {projects[0]["id"], projects[1]["id"]}
    ]
    assert all(i["status"] == "active" for i in active_rows)

    paused_b = live_client.post(
        f"{api}/admin/containers/{projects[1]['id']}/pause",
        headers=admin_h,
    )
    assert paused_b.status_code == 200, paused_b.text
    assert paused_b.json()["status"] == "paused"

    listed2 = live_client.get(f"{api}/admin/containers", headers=admin_h)
    statuses = [
        (i["project_id"], i["status"])
        for i in listed2.json()["items"]
        if i["project_id"] in {projects[0]["id"], projects[1]["id"]}
    ]
    idx_a = next(n for n, (pid, _) in enumerate(statuses) if pid == projects[0]["id"])
    idx_b = next(n for n, (pid, _) in enumerate(statuses) if pid == projects[1]["id"])
    # Active before paused in admin list.
    assert idx_a < idx_b
    assert statuses[idx_a][1] == "active"
    assert statuses[idx_b][1] == "paused"

    # Pause of B must not disturb A's live pod.
    _wait_runtime_running(live_client, api, owner_h, projects[0]["id"], wait_sec=60.0)

    sess = live_client.post(
        f"{api}/projects/{projects[0]['id']}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]
    sent = live_client.post(
        f"{api}/projects/{projects[0]['id']}/agent/sessions/{session_id}/send",
        headers=owner_h,
        json={"text": "ping"},
    )
    assert sent.status_code == 200, sent.text

    paused_a = live_client.post(
        f"{api}/admin/containers/{projects[0]['id']}/pause",
        headers=admin_h,
    )
    assert paused_a.status_code == 200, paused_a.text
    assert paused_a.json()["status"] == "paused"

    chat = live_client.post(
        f"{api}/projects/{projects[0]['id']}/chat",
        headers=owner_h,
        json={"text": "should fail"},
    )
    assert chat.status_code in (409, 422)

    resumed = live_client.post(
        f"{api}/admin/containers/{projects[0]['id']}/resume",
        headers=admin_h,
    )
    assert resumed.status_code == 200, resumed.text
    # Resume provisions in background; HTTP may still report paused/resuming.
    _wait_project_status(
        live_client,
        api,
        admin_h,
        projects[0]["id"],
        want="active",
        via_admin=True,
    )

    disabled = live_client.patch(
        f"{api}/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    after_key = live_client.get(
        f"{api}/admin/containers/{projects[1]['id']}",
        headers=admin_h,
    )
    assert after_key.status_code == 200
    assert after_key.json()["status"] == "paused"

    live_client.patch(
        f"{api}/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "active"},
    )

    for p in projects:
        r = live_client.post(
            f"{api}/admin/containers/{p['id']}/resume",
            headers=admin_h,
        )
        assert r.status_code == 200, r.text
        _wait_project_status(
            live_client,
            api,
            admin_h,
            p["id"],
            want="active",
            via_admin=True,
        )

    sess2 = live_client.post(
        f"{api}/projects/{projects[0]['id']}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess2.status_code == 201, sess2.text
    sid2 = sess2.json()["id"]
    live_client.post(
        f"{api}/projects/{projects[0]['id']}/agent/sessions/{sid2}/send",
        headers=owner_h,
        json={"text": "before delete"},
    )

    deleted = live_client.delete(
        f"{api}/admin/containers/{projects[0]['id']}",
        headers=admin_h,
    )
    assert deleted.status_code == 200
    gone = live_client.get(
        f"{api}/admin/containers/{projects[0]['id']}",
        headers=admin_h,
    )
    assert gone.status_code == 404

    sb = live_client.get(f"{api}/admin/projects/sandbox-k8s", headers=admin_h)
    assert sb.status_code == 200
    assert isinstance(sb.json(), dict)

    peer_proj = live_client.get(
        f"{api}/projects/{projects[1]['id']}",
        headers=auth_header(peer_tok),
    )
    assert peer_proj.status_code in (403, 404)

    live_client.delete(
        f"{api}/admin/containers/{projects[1]['id']}",
        headers=admin_h,
    )
    live_client.delete(f"{api}/admin/companies/{company_id}", headers=admin_h)
