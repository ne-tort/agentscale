"""Integration tests — L07 projects runtime."""

from __future__ import annotations

import base64
import json
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
from tests.integration.support import configure_and_launch, owner_bearer_token
from tests.unit.test_mcp_package_validator import _zip_with_manifest


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
        json={"name": "ProjCo", "password": "test-company-pass", "admin_email": "owner@projco.test"},
    )
    assert created.status_code == 201, created.text
    created_body = created.json()
    company_id = created_body["company"]["id"]
    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "ProjCo Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-projco",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    owner_tok = owner_bearer_token(_token, created.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ProjCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    return company_id, cab.json()["id"], owner_tok


def _configure_and_launch(client: TestClient, owner_h: dict[str, str], project_id: str) -> dict:
    keys = client.get(f"/api/v1/projects/{project_id}/ai-keys/available", headers=owner_h)
    assert keys.status_code == 200, keys.text
    items = keys.json().get("items") or []
    assert items, keys.text
    key_id = items[0]["id"]
    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["agent_provider"] == "cursor"
    launched = client.post(f"/api/v1/projects/{project_id}/launch", headers=owner_h)
    assert launched.status_code == 200, launched.text
    return launched.json()


def _sync_project_workspace(client: TestClient, owner_h: dict[str, str], project_id: str) -> dict:
    synced = client.post(f"/api/v1/projects/{project_id}/sync", headers=owner_h)
    assert synced.status_code == 200, synced.text
    return synced.json()


@requires_postgres
def test_project_create_materialize_lifecycle(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Demo Run"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    project_id = body["id"]
    assert body["status"] == "draft"
    assert body["container_ref"].startswith("object-ws:")
    assert "materialize" not in body

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.json().get("runtime") is None

    launched = _configure_and_launch(client, owner_h, project_id)
    ws_root = Path(launched["materialize"]["workspace_root"])
    assert ws_root.is_dir()
    assert (ws_root / "AGENTS.md").is_file()
    assert not (ws_root / "CLAUDE.md").exists()
    assert (ws_root / "mcp.json").is_file()
    assert (ws_root / ".prodavan" / "config.yaml").is_file()
    assert (ws_root / "inbox").is_dir()

    listed = client.get(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
    )
    assert listed.status_code == 200
    listed_item = next(p for p in listed.json()["items"] if p["id"] == project_id)
    assert listed_item.get("created_by_login")

    paused = client.post(
        f"/api/v1/projects/{project_id}/pause",
        headers=owner_h,
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(
        f"/api/v1/projects/{project_id}/resume",
        headers=owner_h,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    trig = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "hello"}},
    )
    assert trig.status_code == 202
    assert trig.json()["kind"] == "chat.message"

    att = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
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
        headers=owner_h,
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert ws_root.is_dir(), "soft delete retains workspace until purge"

    purged = client.delete(
        f"/api/v1/projects/{project_id}/purge",
        headers=owner_h,
    )
    assert purged.status_code == 200, purged.text
    assert not ws_root.exists()


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
    _configure_and_launch(client, owner_h, project_id)

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    blocked_chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "nope"},
    )
    assert blocked_chat.status_code == 409

    remat = client.post(f"/api/v1/projects/{project_id}/materialize", headers=owner_h)
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
    _configure_and_launch(client, owner_h, project_id)

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
        json={"name": "AttachCo", "password": "test-company-pass", "admin_email": "owner@attachco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created.json())

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
    assert cab.status_code in (200, 201), cab.text
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
        json={"name": "HookCo", "password": "test-company-pass", "admin_email": "owner@hookco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "HookProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

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
        json={"name": "SusCo", "password": "test-company-pass", "admin_email": "owner@susco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
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

    chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"text": "hello after suspend"},
    )
    assert chat.status_code == 403, chat.text
    assert chat.json()["code"] == "COMPANY_SUSPENDED"


@requires_postgres
def test_subscription_suspend_event_is_idempotent(client: TestClient) -> None:
    admin = _token(sub="padmin-idem", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "IdemCo", "password": "test-company-pass", "admin_email": "owner@idemco.test"},
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
        json={"name": "ReactCo", "password": "test-company-pass", "admin_email": "owner@reactco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ReactCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text

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


@requires_postgres
def test_webhook_ingress_blocked_when_company_suspended(client: TestClient) -> None:
    from prodavan.domain.projects import webhook_signature

    admin = _token(sub="padmin-wh-sus", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "WhSusCo", "password": "test-company-pass", "admin_email": "owner@whsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
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
        json={"name": "HookPauseCo", "password": "test-company-pass", "admin_email": "owner@hookpause.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "HookPauseProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Hook Pause Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-hook-pause",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    configure_and_launch(client, owner_h, project_id)

    paused = client.post(
        f"/api/v1/projects/{project_id}/pause",
        headers=owner_h,
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
        json={"name": "TgPauseCo", "password": "test-company-pass", "admin_email": "owner@tgpause.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "TgPauseProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Tg Pause Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-tg-pause",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    configure_and_launch(client, owner_h, project_id)

    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

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
        json={"name": "LeaveQCo", "password": "test-company-pass", "admin_email": "owner@leaveq.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())
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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "LeaveQProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)
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
        json={"name": "ProjSusCo", "password": "test-company-pass", "admin_email": "owner@projsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ProjSusCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
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
def test_get_project_includes_company_subscription(client: TestClient) -> None:
    admin = _token(sub="padmin-sub-dto", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "SubDtoCo", "password": "test-company-pass", "admin_email": "owner@subdtoco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "SubDtoCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
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
        json={"name": "AttSusCo", "password": "test-company-pass", "admin_email": "owner@attsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "AttSusCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
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
        json={"name": "TrgSusCo", "password": "test-company-pass", "admin_email": "owner@trgsusco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "TrgSusProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    configure_and_launch(client, owner_h, project_id)

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
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=10",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert drain.status_code == 200, drain.text
    body = drain.json()
    reasons = [body["reason"]] if body.get("reason") else []
    reasons.extend(i.get("reason") for i in (body.get("items") or []) if i.get("reason"))
    assert "company_suspended" in reasons, body

    listed = client.get(
        f"/api/v1/projects/{project_id}/triggers",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200, listed.text
    assert any(t.get("status") == "failed" for t in listed.json()["items"])


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
        json={"name": "NatExpCo", "password": "test-company-pass", "admin_email": "owner@natexpco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
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
        json={"name": "PauseChatCo", "password": "test-company-pass", "admin_email": "owner@pausechat.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())
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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PauseChatProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)

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
        json={"name": "IdleCo", "password": "test-company-pass", "admin_email": "owner@idleco.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())

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
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "IdleProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Idle Sweep Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-idle-sweep",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    configure_and_launch(client, owner_h, project_id)

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
def test_project_resume_requires_valid_ai_key(client: TestClient) -> None:
    admin = _token(sub="padmin-resume-key", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "ResumeKeyCo", "password": "test-company-pass", "admin_email": "owner@resumekey.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created.json())
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Resume Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-resume-key",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "ResumeKeyCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    proj = client.post(
        f"/api/v1/cabinets/{cab.json()['id']}/projects",
        headers=owner_h,
        json={"name": "ResumeKeyProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"resolved_ai_key_id": None},
    )
    client.patch(
        f"/api/v1/admin/ai-keys/{key_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"status": "disabled"},
    )

    blocked = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert blocked.status_code == 404, blocked.text
    assert blocked.json()["code"] == "NO_AI_KEY"
    assert client.get(f"/api/v1/projects/{project_id}", headers=owner_h).json()["status"] == "paused"


@requires_postgres
def test_patch_resolved_ai_key_sets_agent_provider(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Key Only Proj"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert created.json()["agent_provider"] is None

    keys = client.get(f"/api/v1/projects/{project_id}/ai-keys/available", headers=owner_h)
    assert keys.status_code == 200, keys.text
    key_id = keys.json()["items"][0]["id"]

    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["resolved_ai_key_id"] == key_id
    assert patched.json()["agent_provider"] == "cursor"

    cleared = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"resolved_ai_key_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["resolved_ai_key_id"] is None
    assert cleared.json()["agent_provider"] is None


@requires_postgres
def test_cabinet_and_project_metrics(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Metrics Proj"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    cab_metrics = client.get(f"/api/v1/cabinets/{cabinet_id}/metrics", headers=owner_h)
    assert cab_metrics.status_code == 200, cab_metrics.text
    cab_body = cab_metrics.json()
    assert cab_body["cabinet_id"] == cabinet_id
    assert cab_body["projects_total"] >= 1
    assert "agent_tokens_used" in cab_body
    assert "employees_total" in cab_body
    assert "module_bindings_count" not in cab_body

    proj_metrics = client.get(f"/api/v1/projects/{project_id}/metrics", headers=owner_h)
    assert proj_metrics.status_code == 200, proj_metrics.text
    proj_body = proj_metrics.json()
    assert proj_body["project_id"] == project_id
    assert proj_body["status"] == "draft"
    assert proj_body["agent_tokens_used"] == 0

    mod_list = client.get(f"/api/v1/projects/{project_id}/modules", headers=owner_h)
    assert mod_list.status_code == 200, mod_list.text
    mod_body = mod_list.json()
    assert "module_ids" in mod_body
    assert "items" in mod_body
    assert isinstance(mod_body["items"], list)


def _file_ref_from_upload(upload: dict) -> dict:
    return {
        "asset_id": upload["asset_id"],
        "version_id": upload["version_id"],
        "storage_key": upload["storage_key"],
        "filename": upload["filename"],
        "sha256": upload["sha256"],
    }


def _file_ref_from_content_asset(
    client: TestClient,
    owner_h: dict[str, str],
    company_id: str,
    *,
    data: bytes,
    filename: str,
    mime: str,
) -> dict:
    """Company-scoped asset upload without cabinet link (avoids uq_content_asset_link_target)."""
    from prodavan.infrastructure.files.manager import ensure_file_store

    asset = client.post(
        "/api/v1/content/assets",
        headers=owner_h,
        json={"owner_company_id": company_id, "title": filename, "mime": mime},
    )
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()["id"]

    ver = client.post(
        f"/api/v1/content/assets/{asset_id}/versions",
        headers=owner_h,
        json={"mime": mime},
    )
    assert ver.status_code == 200, ver.text
    version_id = ver.json()["version"]["id"]
    storage_key = ver.json()["version"]["storage_key"]

    store = ensure_file_store()
    store.put_bytes_sync(storage_key, data, content_type=mime)

    fin = client.post(
        f"/api/v1/content/assets/{asset_id}/versions/{version_id}/finalize",
        headers=owner_h,
    )
    assert fin.status_code == 200, fin.text
    import hashlib

    return {
        "asset_id": asset_id,
        "version_id": version_id,
        "storage_key": storage_key,
        "filename": filename,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


@requires_postgres
def test_project_materialize_copy_blob_and_mcp_package(client: TestClient) -> None:
    """L07: mod_files copy_blob + mod_mcp mcp_package land in workspace on launch."""
    company_id, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    file_payload = b"hello workspace file"
    file_up = client.post(
        f"/api/v1/cabinets/{cabinet_id}/content/upload",
        headers=owner_h,
        files={"file": ("hello.txt", file_payload, "text/plain")},
    )
    assert file_up.status_code == 200, file_up.text
    file_ref = _file_ref_from_upload(file_up.json())

    file_row = client.post(
        f"/api/v1/cabinets/{cabinet_id}/modules/mod_files/data/files",
        headers=owner_h,
        json={
            "body": {
                "name": "Hello",
                "target_path": "assets/hello.txt",
                "file_ref": file_ref,
            }
        },
    )
    assert file_row.status_code == 200, file_row.text

    mcp_zip = _zip_with_manifest("demo")
    mcp_ref = _file_ref_from_content_asset(
        client,
        owner_h,
        company_id,
        data=mcp_zip,
        filename="demo.zip",
        mime="application/zip",
    )

    mcp_row = client.post(
        f"/api/v1/cabinets/{cabinet_id}/modules/mod_mcp/data/mcp_packages",
        headers=owner_h,
        json={
            "body": {
                "name": "demo",
                "version": "1.0.0",
                "enabled": True,
                "file_ref": mcp_ref,
            }
        },
    )
    assert mcp_row.status_code == 200, mcp_row.text

    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Materialize Files MCP"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    launched = _configure_and_launch(client, owner_h, project_id)
    ws_root = Path(launched["materialize"]["workspace_root"])
    assert ws_root.is_dir()

    copied = ws_root / "assets" / "hello.txt"
    assert copied.is_file(), list(ws_root.rglob("*"))
    assert copied.read_bytes() == file_payload

    manifest = ws_root / "packages" / "demo" / "manifest.json"
    assert manifest.is_file(), list((ws_root / "packages").rglob("*"))
    assert json.loads(manifest.read_text(encoding="utf-8"))["name"] == "demo"

    mcp_cfg = json.loads((ws_root / "mcp.json").read_text(encoding="utf-8"))
    pkg_names = {p.get("name") for p in mcp_cfg.get("packages") or []}
    assert "demo" in pkg_names


@requires_postgres
def test_project_materialize_after_mod_files_row_on_running_pod(client: TestClient) -> None:
    """mod_files row CRUD schedules inline rematerialize — workspace updates without manual POST /materialize."""
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Remat After Row"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    launched = _configure_and_launch(client, owner_h, project_id)
    ws_root = Path(launched["materialize"]["workspace_root"])

    file_payload = b"sync path content"
    file_up = client.post(
        f"/api/v1/cabinets/{cabinet_id}/content/upload",
        headers=owner_h,
        files={"file": ("sync.txt", file_payload, "text/plain")},
    )
    assert file_up.status_code == 200, file_up.text

    file_row = client.post(
        f"/api/v1/cabinets/{cabinet_id}/modules/mod_files/data/files",
        headers=owner_h,
        json={
            "body": {
                "name": "Sync file",
                "target_path": "assets/sync.txt",
                "file_ref": _file_ref_from_upload(file_up.json()),
            }
        },
    )
    assert file_row.status_code == 200, file_row.text
    remat_meta = file_row.json().get("rematerialize") or file_row.json().get("workspace_sync") or {}
    assert int(remat_meta.get("marked_outdated") or 0) >= 1

    _sync_project_workspace(client, owner_h, project_id)

    copied = ws_root / "assets" / "sync.txt"
    assert copied.is_file()
    assert copied.read_bytes() == file_payload

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.json()["runtime"]["hydrate_generation"] >= 1


@requires_postgres
def test_patch_project_modules_syncs_running_pod(client: TestClient) -> None:
    """PATCH /projects/{id}/modules rematerializes workspace on running pod (inline Celery fallback)."""
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Module Toggle Sync"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    _configure_and_launch(client, owner_h, project_id)

    before = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert before.json()["runtime"]["hydrate_generation"] == 0

    listed = client.get(f"/api/v1/projects/{project_id}/modules", headers=owner_h)
    assert listed.status_code == 200, listed.text
    all_ids = [m["module_id"] for m in listed.json()["items"]]
    assert "mod_files" in all_ids

    patched = client.patch(
        f"/api/v1/projects/{project_id}/modules",
        headers=owner_h,
        json={"module_ids": ["mod_files"]},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["module_ids"] == ["mod_files"]
    ws_meta = body.get("workspace_sync") or body.get("rematerialize") or {}
    assert ws_meta.get("mode") == "deferred" or int(ws_meta.get("marked_outdated") or 0) >= 1

    _sync_project_workspace(client, owner_h, project_id)

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert after.json()["runtime"]["hydrate_generation"] == 1


@requires_postgres
def test_admin_cabinet_module_bind_syncs_running_pod(client: TestClient) -> None:
    """Admin rebind mod_files enables row CRUD rematerialize on a running project pod."""
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    admin_h = {"Authorization": f"Bearer {_token(sub='padmin-bind', platform_admin=True)}"}

    file_payload = b"bind sync content"
    file_up = client.post(
        f"/api/v1/cabinets/{cabinet_id}/content/upload",
        headers=owner_h,
        files={"file": ("bind-sync.txt", file_payload, "text/plain")},
    )
    assert file_up.status_code == 200, file_up.text

    cab = client.get(f"/api/v1/admin/cabinets/{cabinet_id}", headers=admin_h)
    assert cab.status_code == 200, cab.text
    all_module_ids = list(cab.json()["module_ids"])
    assert "mod_files" in all_module_ids
    without_files = [m for m in all_module_ids if m != "mod_files"]

    stripped = client.patch(
        f"/api/v1/admin/cabinets/{cabinet_id}",
        headers=admin_h,
        json={"module_ids": without_files},
    )
    assert stripped.status_code == 200, stripped.text
    assert "mod_files" not in stripped.json()["module_ids"]

    created = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Cabinet Bind Sync"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    launched = _configure_and_launch(client, owner_h, project_id)
    ws_root = Path(launched["materialize"]["workspace_root"])

    missing = ws_root / "assets" / "bind-sync.txt"
    assert not missing.is_file(), list(ws_root.rglob("*"))

    before = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert before.json()["runtime"]["hydrate_generation"] == 0

    rebound = client.patch(
        f"/api/v1/admin/cabinets/{cabinet_id}",
        headers=admin_h,
        json={"module_ids": all_module_ids},
    )
    assert rebound.status_code == 200, rebound.text
    assert "mod_files" in rebound.json()["module_ids"]

    file_row = client.post(
        f"/api/v1/cabinets/{cabinet_id}/modules/mod_files/data/files",
        headers=owner_h,
        json={
            "body": {
                "name": "Bind sync",
                "target_path": "assets/bind-sync.txt",
                "file_ref": _file_ref_from_upload(file_up.json()),
            }
        },
    )
    assert file_row.status_code == 200, file_row.text
    remat_meta = file_row.json().get("rematerialize") or file_row.json().get("workspace_sync") or {}
    assert int(remat_meta.get("marked_outdated") or 0) >= 1

    _sync_project_workspace(client, owner_h, project_id)

    assert missing.is_file(), list(ws_root.rglob("*"))
    assert missing.read_bytes() == file_payload

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert after.json()["runtime"]["hydrate_generation"] >= 1
