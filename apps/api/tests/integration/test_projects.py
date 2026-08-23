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
from tests.conftest import requires_postgres


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
    assert body["container_ref"].startswith("local-ws:")
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
