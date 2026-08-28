"""L3b — live HTTP e2e against deployed dev API (Traefik :8088, OIDC stack)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from tests.conftest import requires_live_api

pytestmark = [pytest.mark.live, requires_live_api]

# Dev cluster AUTH_TEST_SECRET when AUTH_MODE=test; live API uses oidc — mint with cluster secret.
LIVE_JWT_SECRET = os.getenv(
    "PRODAVAN_E2E_JWT_SECRET",
    "k3s-dev-change-me-in-production-32b",
)
LIVE_AUD = os.getenv("PRODAVAN_E2E_OIDC_AUDIENCE", "prodavan-api")


def _mint(*, sub: str, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": LIVE_AUD,
        "exp": now + timedelta(hours=2),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": ["platform.admin"] if platform_admin else [],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, LIVE_JWT_SECRET, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_live_health_and_auth_config(live_client, live_api_prefix: str) -> None:
    live = live_client.get("/health/live")
    assert live.status_code == 200
    ready = live_client.get("/health/ready")
    assert ready.status_code == 200
    cfg = live_client.get(f"{live_api_prefix}/auth/config")
    assert cfg.status_code == 200
    assert "auth_mode" in cfg.json()


def test_live_containers_lifecycle(live_client, live_api_prefix: str) -> None:
    """Vertical containers flow against deployed API (migrated from tools/_live_containers_e2e.py)."""
    stamp = datetime.now().strftime("%H%M%S")
    admin = _mint(sub=f"padmin-live-{stamp}", platform_admin=True)
    admin_h = _auth(admin)

    co = live_client.post(
        f"{live_api_prefix}/companies",
        headers=admin_h,
        json={
            "name": f"LiveCtr {stamp}",
            "admin_email": f"live-owner-{stamp}@e2e.local",
            "admin_display_name": f"Live Owner {stamp}",
        },
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]
    email = f"live-owner-{stamp}@e2e.local"

    key = live_client.post(
        f"{live_api_prefix}/admin/ai-keys",
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

    owner = _mint(sub=f"owner-live-{stamp}", email=email)
    owner_h = _auth(owner)
    cab = live_client.post(
        f"{live_api_prefix}/cabinets",
        headers=owner_h,
        json={"name": f"LiveCab {stamp}", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    inv = live_client.post(
        f"{live_api_prefix}/companies/{company_id}/employees",
        headers=admin_h,
        json={"email": f"peer-{stamp}@e2e.local", "display_name": "Peer", "role": "member"},
    )
    assert inv.status_code in (200, 201), inv.text
    peer = _mint(sub=f"peer-live-{stamp}", email=f"peer-{stamp}@e2e.local")

    denied = live_client.get(f"{live_api_prefix}/cabinets/{cabinet_id}", headers=_auth(peer))
    assert denied.status_code == 403

    projects = []
    for name in ("ProjA", "ProjB"):
        proj = live_client.post(
            f"{live_api_prefix}/cabinets/{cabinet_id}/projects",
            headers=owner_h,
            json={"name": f"{name} {stamp}"},
        )
        assert proj.status_code == 201, proj.text
        projects.append(proj.json())

    assert projects[0]["workspace_key"] != projects[1]["workspace_key"]
    assert projects[0]["container_ref"].startswith("object-ws:")

    listed = live_client.get(f"{live_api_prefix}/admin/containers", headers=admin_h)
    assert listed.status_code == 200
    ids = {i["project_id"] for i in listed.json()["items"]}
    assert projects[0]["id"] in ids and projects[1]["id"] in ids

    row = next(i for i in listed.json()["items"] if i["project_id"] == projects[0]["id"])
    assert row["k8s_phase"] is None
    assert row["company_name"].startswith("LiveCtr")

    paused_b = live_client.post(
        f"{live_api_prefix}/admin/containers/{projects[1]['id']}/pause",
        headers=admin_h,
    )
    assert paused_b.status_code == 200
    listed2 = live_client.get(f"{live_api_prefix}/admin/containers", headers=admin_h)
    statuses = [
        (i["project_id"], i["status"])
        for i in listed2.json()["items"]
        if i["project_id"] in {projects[0]["id"], projects[1]["id"]}
    ]
    idx_a = next(n for n, (pid, _) in enumerate(statuses) if pid == projects[0]["id"])
    idx_b = next(n for n, (pid, _) in enumerate(statuses) if pid == projects[1]["id"])
    assert idx_a < idx_b

    sess = live_client.post(
        f"{live_api_prefix}/projects/{projects[0]['id']}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]
    sent = live_client.post(
        f"{live_api_prefix}/projects/{projects[0]['id']}/agent/sessions/{session_id}/send",
        headers=owner_h,
        json={"text": "ping"},
    )
    assert sent.status_code == 200, sent.text

    paused_a = live_client.post(
        f"{live_api_prefix}/admin/containers/{projects[0]['id']}/pause",
        headers=admin_h,
    )
    assert paused_a.status_code == 200
    assert paused_a.json()["status"] == "paused"

    chat = live_client.post(
        f"{live_api_prefix}/projects/{projects[0]['id']}/chat",
        headers=owner_h,
        json={"text": "should fail"},
    )
    assert chat.status_code in (409, 422)

    resumed = live_client.post(
        f"{live_api_prefix}/admin/containers/{projects[0]['id']}/resume",
        headers=admin_h,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    disabled = live_client.patch(
        f"{live_api_prefix}/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    after_key = live_client.get(
        f"{live_api_prefix}/admin/containers/{projects[1]['id']}",
        headers=admin_h,
    )
    assert after_key.json()["status"] == "paused"

    live_client.patch(
        f"{live_api_prefix}/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "active"},
    )

    for p in projects:
        r = live_client.post(
            f"{live_api_prefix}/admin/containers/{p['id']}/resume",
            headers=admin_h,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    sess2 = live_client.post(
        f"{live_api_prefix}/projects/{projects[0]['id']}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess2.status_code == 201
    sid2 = sess2.json()["id"]
    live_client.post(
        f"{live_api_prefix}/projects/{projects[0]['id']}/agent/sessions/{sid2}/send",
        headers=owner_h,
        json={"text": "before delete"},
    )

    deleted = live_client.delete(
        f"{live_api_prefix}/admin/containers/{projects[0]['id']}",
        headers=admin_h,
    )
    assert deleted.status_code == 200
    gone = live_client.get(
        f"{live_api_prefix}/admin/containers/{projects[0]['id']}",
        headers=admin_h,
    )
    assert gone.status_code == 404

    sb = live_client.get(f"{live_api_prefix}/admin/projects/sandbox-k8s", headers=admin_h)
    assert sb.status_code == 200
    assert isinstance(sb.json(), dict)

    peer_proj = live_client.get(
        f"{live_api_prefix}/projects/{projects[1]['id']}",
        headers=_auth(peer),
    )
    assert peer_proj.status_code == 403

    live_client.delete(
        f"{live_api_prefix}/admin/containers/{projects[1]['id']}",
        headers=admin_h,
    )
    live_client.delete(f"{live_api_prefix}/admin/companies/{company_id}", headers=admin_h)
