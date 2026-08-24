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
from tests.conftest import requires_postgres, sql_backdate_project


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
    assert body["container_ref"].startswith("object-ws:")
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


@requires_postgres
def test_rematerialize_allowed_when_paused(client: TestClient) -> None:
    """Workspace rematerialize is maintenance — allowed while PROJECT_PAUSED."""
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Remat Paused"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    blocked_chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "nope"},
    )
    assert blocked_chat.status_code == 409

    remat = client.post(f"/api/v1/projects/{project_id}/rematerialize", headers=owner_h)
    assert remat.status_code == 200, remat.text
    assert remat.json()["project_id"] == project_id
    assert remat.json().get("workspace_root")


@requires_postgres
def test_paused_allows_patch_and_attachment_delete_blocks_upload(client: TestClient) -> None:
    """Paused: metadata/cleanup OK; runtime uploads still PROJECT_PAUSED; pause idempotent."""
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Pause Matrix"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "keep.txt",
            "content_base64": base64.b64encode(b"keep me").decode("ascii"),
        },
    )
    assert att.status_code == 201, att.text
    attachment_id = att.json()["id"]

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200
    again = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert again.status_code == 200
    assert again.json()["status"] == "paused"

    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"name": "Pause Matrix Renamed"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Pause Matrix Renamed"
    assert patched.json()["status"] == "paused"

    deleted = client.delete(
        f"/api/v1/projects/{project_id}/attachments/{attachment_id}",
        headers=owner_h,
    )
    assert deleted.status_code == 200, deleted.text

    blocked = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "nope.txt",
            "content_base64": base64.b64encode(b"nope").decode("ascii"),
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "PROJECT_PAUSED"

    trig = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "queued?"}},
    )
    assert trig.status_code == 409, trig.text
    assert trig.json()["code"] == "PROJECT_PAUSED"


@requires_postgres
def test_attachment_rejects_shebang_script(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Shebang Proj"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    bad = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"#!/bin/bash\nrm -rf /\n").decode("ascii"),
        },
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["code"] == "ATTACHMENT_CONTENT_FORBIDDEN"


@requires_postgres
def test_project_patch_agent_provider(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Provider Demo", "agent_provider": "cursor"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert created.json()["agent_provider"] == "cursor"

    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"agent_provider": "codex", "name": "Provider Demo Renamed"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["agent_provider"] == "codex"
    assert patched.json()["name"] == "Provider Demo Renamed"

    cleared = client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"agent_provider": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["agent_provider"] is None

    bad = client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"agent_provider": "nope"},
    )
    assert bad.status_code == 422


@requires_postgres
def test_admin_triggers_drain(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    admin = _token(sub="padmin", platform_admin=True)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Drain Demo"},
    )
    assert created.status_code == 201, created.text
    _project_id = created.json()["id"]

    # create enqueues project.prepare — drain should ack it
    drained = client.post(
        "/api/v1/admin/triggers/drain?max_projects=10&max_per_project=5",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert drained.status_code == 200, drained.text
    body = drained.json()
    assert "projects" in body
    assert body.get("count", 0) >= 1

    empty = client.post(
        "/api/v1/admin/triggers/drain",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert empty.status_code == 200
    assert empty.json().get("dispatched") is False


@requires_postgres
def test_attachment_respects_company_policy_and_extension(client: TestClient) -> None:
    admin = _token(sub="padmin", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "AttachCo", "admin_email": "owner@attachco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_tok = _token(sub="owner-sub", email="owner@attachco.test")

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "max_attachment_mb": 1},
    )
    assert policy.status_code == 200, policy.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "AttachCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "AttachProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    assert proj.json()["limits"]["attachment_max_bytes"] == 1024 * 1024

    too_big = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "big.txt",
            "content_base64": base64.b64encode(b"x" * (1024 * 1024 + 1)).decode("ascii"),
        },
    )
    assert too_big.status_code == 413
    assert too_big.json()["code"] == "ATTACHMENT_TOO_LARGE"

    forbidden = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "run.exe",
            "content_base64": base64.b64encode(b"MZ").decode("ascii"),
        },
    )
    assert forbidden.status_code == 422
    assert forbidden.json()["code"] == "ATTACHMENT_TYPE_FORBIDDEN"

    ok = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"hello").decode("ascii"),
        },
    )
    assert ok.status_code == 201, ok.text


@requires_postgres
def test_list_project_attachments(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "List Attach"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"listed").decode("ascii"),
        },
    )
    assert att.status_code == 201, att.text

    listed = client.get(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["filename"] == "note.txt"


@requires_postgres
def test_download_attachment_content(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Download Attach"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAD0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "dot.png",
            "content_base64": base64.b64encode(png).decode("ascii"),
        },
    )
    assert att.status_code == 201, att.text
    attachment_id = att.json()["id"]
    assert att.json()["content_type"] == "image/png"

    got = client.get(
        f"/api/v1/projects/{project_id}/attachments/{attachment_id}/content",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert got.status_code == 200, got.text
    assert got.content == png
    assert got.headers["content-type"].startswith("image/png")

    peer = _token(sub="peer-dl", email="peer@other.test")
    denied = client.get(
        f"/api/v1/projects/{project_id}/attachments/{attachment_id}/content",
        headers={"Authorization": f"Bearer {peer}"},
    )
    assert denied.status_code == 403


@requires_postgres
def test_platform_event_emitted_on_project_create(client: TestClient) -> None:
    admin = _token(sub="padmin", platform_admin=True)
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Event Proj"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    events = client.get(
        f"/api/v1/admin/platform-events?project_id={project_id}&event_type=project.created",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    items = events.json()["items"]
    assert len(items) >= 1
    assert items[0]["event_type"] == "project.created"
    assert items[0]["project_id"] == project_id


@requires_postgres
def test_attachment_rejects_executable_magic(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Magic Proj"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    pe = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"MZ\x90\x00fakepe").decode("ascii"),
        },
    )
    assert pe.status_code == 422
    assert pe.json()["code"] == "ATTACHMENT_CONTENT_FORBIDDEN"


@requires_postgres
def test_delete_attachment_and_signed_webhook(client: TestClient) -> None:
    admin = _token(sub="padmin", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "HookCo", "admin_email": "owner@hookco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-sub", email="owner@hookco.test")

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "webhook_hmac_secret": "hook-secret"},
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["webhook_hmac_configured"] is True

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "HookCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "HookProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    # SPI delivery should have written cabinet audit for project.created
    audit = client.get(
        f"/api/v1/cabinets/{cabinet_id}/audit-events",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert audit.status_code == 200, audit.text
    assert any(e.get("event_type") == "platform_event.delivered" for e in audit.json())

    att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"bye").decode("ascii"),
        },
    )
    assert att.status_code == 201, att.text
    attachment_id = att.json()["id"]

    deleted = client.delete(
        f"/api/v1/projects/{project_id}/attachments/{attachment_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True

    from prodavan.domain.projects import webhook_signature

    body = b'{"text":"from webhook"}'
    bad = client.post(
        f"/api/v1/projects/{project_id}/webhooks/http",
        content=body,
        headers={"Content-Type": "application/json", "X-Prodavan-Signature": "sha256=bad"},
    )
    assert bad.status_code == 401

    ok = client.post(
        f"/api/v1/projects/{project_id}/webhooks/http",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Prodavan-Signature": webhook_signature("hook-secret", body),
        },
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["kind"] == "webhook.http"

    tg_policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "telegram_hmac_secret": "tg-secret"},
    )
    assert tg_policy.status_code == 200, tg_policy.text
    assert tg_policy.json()["telegram_hmac_configured"] is True

    tg_body = b'{"text":"from telegram"}'
    tg_bad = client.post(
        f"/api/v1/projects/{project_id}/webhooks/telegram",
        content=tg_body,
        headers={"Content-Type": "application/json", "X-Prodavan-Signature": "sha256=bad"},
    )
    assert tg_bad.status_code == 401

    tg_ok = client.post(
        f"/api/v1/projects/{project_id}/webhooks/telegram",
        content=tg_body,
        headers={
            "Content-Type": "application/json",
            "X-Prodavan-Signature": webhook_signature("tg-secret", tg_body),
        },
    )
    assert tg_ok.status_code == 202, tg_ok.text
    assert tg_ok.json()["kind"] == "telegram.message"


@requires_postgres
def test_company_suspended_emit_and_chat_gate(client: TestClient) -> None:
    admin = _token(sub="padmin-sus", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "SusCo", "admin_email": "owner@susco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-sus", email="owner@susco.test")

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Cursor Sus",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-sus",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "SusCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "SusProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    sess = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={},
    )
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sub = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["subscription_expired"] is True

    events = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=company.suspended",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    items = events.json()["items"]
    assert len(items) >= 1
    assert items[0]["event_type"] == "company.suspended"
    assert items[0]["payload"].get("sessions_cancelled", 0) >= 1

    listed = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200, listed.text
    sess_row = next(s for s in listed.json()["items"] if s["id"] == session_id)
    assert sess_row["status"] == "cancelled"

    audit = client.get(
        f"/api/v1/cabinets/{cabinet_id}/audit-events",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert audit.status_code == 200, audit.text
    audit_types = [e.get("event_type") for e in audit.json()]
    assert "platform_event.delivered" in audit_types

    chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"text": "hello after suspend"},
    )
    assert chat.status_code == 403, chat.text
    assert chat.json()["code"] == "COMPANY_SUSPENDED"


@requires_postgres
def test_company_suspended_invokes_package_platform_handler(client: TestClient) -> None:
    from prodavan.infrastructure.cabinets.package_codec import build_minimal_package_zip

    admin = _token(sub="padmin-pkg", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "PkgCo", "admin_email": "owner@pkgco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-pkg", email="owner@pkgco.test")

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "PkgCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    pkg_b64 = base64.b64encode(
        build_minimal_package_zip(
            name="suspend_hook",
            platform_events=["company.suspended"],
        )
    ).decode("ascii")
    deployed = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp-packages",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"zip_base64": pkg_b64},
    )
    assert deployed.status_code == 201, deployed.text

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sub = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert sub.status_code == 200, sub.text

    audit = client.get(
        f"/api/v1/cabinets/{cabinet_id}/audit-events",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert audit.status_code == 200, audit.text
    events = audit.json()
    assert any(e.get("event_type") == "platform_event.delivered" for e in events)
    handler = next(e for e in events if e.get("event_type") == "platform_event.package_handler")
    detail = handler.get("detail") or {}
    assert detail.get("platform_event_type") == "company.suspended"
    assert detail.get("package_name") == "suspend_hook"
    assert detail.get("action") == "stub"


@requires_postgres
def test_subscription_suspend_event_is_idempotent(client: TestClient) -> None:
    admin = _token(sub="padmin-idem", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "IdemCo", "admin_email": "owner@idemco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    first = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert first.status_code == 200, first.text

    def _count_suspended() -> int:
        res = client.get(
            f"/api/v1/admin/platform-events?company_id={company_id}&event_type=company.suspended",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert res.status_code == 200, res.text
        return len(res.json()["items"])

    count_after_first = _count_suspended()
    assert count_after_first >= 1

    second = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert second.status_code == 200, second.text
    assert _count_suspended() == count_after_first


@requires_postgres
def test_subscription_reactivate_emits_event(client: TestClient) -> None:
    admin = _token(sub="padmin-react", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "ReactCo", "admin_email": "owner@reactco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-react", email="owner@reactco.test")

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ReactCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    suspend = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert suspend.status_code == 200, suspend.text

    future = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    renew = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": future},
    )
    assert renew.status_code == 200, renew.text
    assert renew.json()["subscription_expired"] is False

    events = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=company.reactivated",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    assert len(events.json()["items"]) >= 1

    audit = client.get(
        f"/api/v1/cabinets/{cabinet_id}/audit-events",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert audit.status_code == 200, audit.text
    assert any(e.get("event_type") == "platform_event.delivered" for e in audit.json())


@requires_postgres
def test_webhook_ingress_blocked_when_company_suspended(client: TestClient) -> None:
    from prodavan.domain.projects import webhook_signature

    admin = _token(sub="padmin-wh-sus", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "WhSusCo", "admin_email": "owner@whsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-wh-sus", email="owner@whsusco.test")

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "webhook_hmac_secret": "hook-secret"},
    )
    assert policy.status_code == 200, policy.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "WhSusCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "WhSusProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sub = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert sub.status_code == 200, sub.text

    body = b'{"text":"blocked"}'
    blocked = client.post(
        f"/api/v1/projects/{project_id}/webhooks/http",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Prodavan-Signature": webhook_signature("hook-secret", body),
        },
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["code"] == "COMPANY_SUSPENDED"


@requires_postgres
def test_webhook_ingress_blocked_when_project_paused(client: TestClient) -> None:
    from prodavan.domain.projects import webhook_signature

    admin = _token(sub="padmin-hook-pause", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "HookPauseCo", "admin_email": "owner@hookpause.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-hook-pause", email="owner@hookpause.test")

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "webhook_hmac_secret": "hook-pause-secret"},
    )
    assert policy.status_code == 200, policy.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "HookPauseCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "HookPauseProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    paused = client.post(
        f"/api/v1/projects/{project_id}/pause",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert paused.status_code == 200

    body = b'{"text":"while paused"}'
    blocked = client.post(
        f"/api/v1/projects/{project_id}/webhooks/http",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Prodavan-Signature": webhook_signature("hook-pause-secret", body),
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "PROJECT_PAUSED"


@requires_postgres
def test_telegram_webhook_blocked_when_project_paused(client: TestClient) -> None:
    from prodavan.domain.projects import webhook_signature

    admin = _token(sub="padmin-tg-pause", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "TgPauseCo", "admin_email": "owner@tgpause.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-tg-pause", email="owner@tgpause.test")

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "telegram_hmac_secret": "tg-pause-secret"},
    )
    assert policy.status_code == 200, policy.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "TgPauseCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "TgPauseProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    client.post(f"/api/v1/projects/{project_id}/pause", headers={"Authorization": f"Bearer {owner_tok}"})

    body = b'{"text":"tg while paused"}'
    blocked = client.post(
        f"/api/v1/projects/{project_id}/webhooks/telegram",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Prodavan-Signature": webhook_signature("tg-pause-secret", body),
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "PROJECT_PAUSED"


@requires_postgres
def test_queued_trigger_survives_pause_and_runs_after_resume(client: TestClient) -> None:
    """Leave policy: enqueue → pause (claim skipped) → resume → dispatch runs."""
    admin = _token(sub="padmin-leave-q", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "LeaveQCo", "admin_email": "owner@leaveq.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-leave-q", email="owner@leaveq.test")
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "LeaveQ Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-leave-q",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "LeaveQCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "LeaveQProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    # Drain create-time prepare so queue is clean.
    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)

    queued = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "webhook.http", "payload": {"source": "before-pause"}},
    )
    assert queued.status_code == 202, queued.text
    trigger_id = queued.json()["id"]

    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    skipped = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=5",
        headers=owner_h,
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json().get("dispatched") is False
    assert skipped.json().get("reason") == "project_paused"

    client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)

    listed = client.get(f"/api/v1/projects/{project_id}/triggers", headers=owner_h)
    assert listed.status_code == 200
    item = next(t for t in listed.json()["items"] if t["id"] == trigger_id)
    # Resume kick-drains leave-queued triggers.
    assert item["status"] == "done"


@requires_postgres
def test_create_project_blocked_when_company_suspended(client: TestClient) -> None:
    admin = _token(sub="padmin-proj-sus", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "ProjSusCo", "admin_email": "owner@projsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-proj-sus", email="owner@projsusco.test")

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ProjSusCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sub = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert sub.status_code == 200, sub.text

    blocked = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Should Fail"},
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["code"] == "COMPANY_SUSPENDED"


@requires_postgres
def test_package_platform_handler_invoked_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prodavan.config.settings import settings
    from prodavan.infrastructure.cabinets.package_codec import build_minimal_package_zip

    monkeypatch.setattr(settings, "mcp_platform_event_invoke", True)

    admin = _token(sub="padmin-inv", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "InvCo", "admin_email": "owner@invco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-inv", email="owner@invco.test")

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "InvCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    pkg_b64 = base64.b64encode(
        build_minimal_package_zip(
            name="invoke_hook",
            platform_events=["company.suspended"],
            with_platform_event_handler=True,
        )
    ).decode("ascii")
    deployed = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp-packages",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"zip_base64": pkg_b64},
    )
    assert deployed.status_code == 201, deployed.text

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sub = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert sub.status_code == 200, sub.text

    audit = client.get(
        f"/api/v1/cabinets/{cabinet_id}/audit-events",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert audit.status_code == 200, audit.text
    handler = next(
        e for e in audit.json() if e.get("event_type") == "platform_event.package_handler"
    )
    detail = handler.get("detail") or {}
    assert detail.get("action") == "invoked"
    assert detail.get("exit_code") == 0


@requires_postgres
def test_get_project_includes_company_subscription(client: TestClient) -> None:
    admin = _token(sub="padmin-sub-dto", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "SubDtoCo", "admin_email": "owner@subdtoco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-sub-dto", email="owner@subdtoco.test")

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "SubDtoCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "SubDtoProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    assert proj.json()["company_subscription"]["subscription_expired"] is False

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )

    got = client.get(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert got.status_code == 200, got.text
    sub = got.json()["company_subscription"]
    assert sub["subscription_expired"] is True


@requires_postgres
def test_upload_attachment_blocked_when_company_suspended(client: TestClient) -> None:
    admin = _token(sub="padmin-att-sus", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "AttSusCo", "admin_email": "owner@attsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-att-sus", email="owner@attsusco.test")

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "AttSusCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "AttSusProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )

    blocked = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"nope").decode("ascii"),
        },
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["code"] == "COMPANY_SUSPENDED"


@requires_postgres
def test_trigger_drain_fails_queued_when_company_suspended(client: TestClient) -> None:
    admin = _token(sub="padmin-trg-sus", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "TrgSusCo", "admin_email": "owner@trgsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-trg-sus", email="owner@trgsusco.test")

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Cursor Trg",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-trg",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "TrgSusCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "TrgSusProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    from prodavan.domain.projects import webhook_signature

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "webhook_hmac_secret": "pre-suspend"},
    )
    assert policy.status_code == 200, policy.text

    body = b'{"text":"queued before suspend"}'
    queued = client.post(
        f"/api/v1/projects/{project_id}/webhooks/http",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Prodavan-Signature": webhook_signature("pre-suspend", body),
        },
    )
    assert queued.status_code == 202, queued.text

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )

    drain = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=1",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert drain.status_code == 200, drain.text
    items = drain.json().get("items") or []
    assert items
    assert items[0].get("reason") == "company_suspended"

    listed = client.get(
        f"/api/v1/projects/{project_id}/triggers",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200, listed.text
    assert any(t.get("status") == "failed" for t in listed.json())


def _sql_backdate_subscription(company_id: str, ends_at: datetime) -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import DATABASE_URL

    async def _run() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE companies SET subscription_ends_at = :ends, subscription_lifetime = false "
                    "WHERE id = :cid"
                ),
                {"ends": ends_at, "cid": company_id},
            )
        await engine.dispose()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=30)


@requires_postgres
def test_natural_subscription_expiry_emits_suspended_on_read(client: TestClient) -> None:
    admin = _token(sub="padmin-nat-exp", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "NatExpCo", "admin_email": "owner@natexpco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-nat-exp", email="owner@natexpco.test")

    future = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    active = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": future},
    )
    assert active.status_code == 200, active.text
    assert active.json()["subscription_expired"] is False

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "NatExpCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "NatExpProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    empty = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=company.suspended",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []

    past = datetime.now(UTC) - timedelta(days=1)
    _sql_backdate_subscription(company_id, past)

    got = client.get(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert got.status_code == 200, got.text
    assert got.json()["company_subscription"]["subscription_expired"] is True

    events = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=company.suspended",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    items = events.json()["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "company.suspended"
    assert items[0]["payload"].get("reason") == "subscription_expired"


@requires_postgres
def test_project_pause_blocks_chat_and_attachment(client: TestClient) -> None:
    admin = _token(sub="padmin-pause-chat", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "PauseChatCo", "admin_email": "owner@pausechat.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-pause-chat", email="owner@pausechat.test")
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Pause Chat Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-pause-chat",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "PauseChatCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PauseChatProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    blocked_chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "while paused"},
    )
    assert blocked_chat.status_code == 409, blocked_chat.text
    assert blocked_chat.json()["code"] == "PROJECT_PAUSED"

    blocked_att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "blocked.txt",
            "content_base64": base64.b64encode(b"nope").decode("ascii"),
        },
    )
    assert blocked_att.status_code == 409, blocked_att.text
    assert blocked_att.json()["code"] == "PROJECT_PAUSED"

    resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"

    ok_chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "after resume"},
    )
    assert ok_chat.status_code == 200, ok_chat.text


@requires_postgres
def test_idle_pause_sweep_pauses_stale_project(client: TestClient) -> None:
    admin = _token(sub="padmin-idle", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "IdleCo", "admin_email": "owner@idleco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-idle", email="owner@idleco.test")

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tool_preset": "workspace_dev", "idle_pause_after_hours": 24},
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["idle_pause_enabled"] is True
    assert policy.json()["idle_pause_after_hours"] == 24

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "IdleCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "IdleProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    noop = client.post(
        f"/api/v1/admin/companies/{company_id}/idle-pause/sweep",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["count"] == 0

    stale = datetime.now(UTC) - timedelta(hours=48)
    sql_backdate_project(project_id, stale)

    swept = client.post(
        f"/api/v1/admin/companies/{company_id}/idle-pause/sweep",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert swept.status_code == 200, swept.text
    assert swept.json()["count"] == 1
    assert swept.json()["paused"][0]["project_id"] == project_id

    got = client.get(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "paused"

    events = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=project.paused",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    assert any(e.get("payload", {}).get("reason") == "idle_pause" for e in events.json()["items"])


@requires_postgres
def test_mcp_package_deploy_rematerializes_project(client: TestClient) -> None:
    from prodavan.infrastructure.cabinets.package_codec import build_minimal_package_zip

    admin = _token(sub="padmin-remat", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "RematCo", "admin_email": "owner@rematco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = _token(sub="owner-remat", email="owner@rematco.test")

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Remat Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-remat",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "RematCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "RematProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    pkg_b64 = base64.b64encode(build_minimal_package_zip(name="remat_pkg")).decode()
    deployed = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp-packages",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"zip_base64": pkg_b64},
    )
    assert deployed.status_code == 201, deployed.text
    remat = deployed.json()["rematerialized"]
    assert remat["count"] == 1
    assert remat["projects"][0]["project_id"] == project_id
    assert "remat_pkg" in remat["projects"][0]["package_names"]

    manual = client.post(
        f"/api/v1/projects/{project_id}/rematerialize",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert manual.status_code == 200, manual.text
    assert "remat_pkg" in manual.json()["package_names"]
