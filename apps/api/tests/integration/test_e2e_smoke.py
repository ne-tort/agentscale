"""E2E smoke — Admin → Company → Cabinet → Project → Agent (L09)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
os.environ.setdefault("KEYCLOAK_INVITE_MODE", "fake")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres, sql_backdate_project
from tests.integration.support import owner_auth_from_company


def _owner_h(company_body: dict) -> dict[str, str]:
    return owner_auth_from_company(_token, company_body)


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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


@requires_postgres
def test_e2e_smoke_admin_to_agent_ping(client: TestClient) -> None:
    """Happy path: company + key + cabinet + project + agent turn + admin metrics."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2E Co", "password": "test-company-pass", "admin_email": "boss@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "E2E Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-e2e",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2E Cabinet", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2E Project"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    sess = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    sent = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/send",
        headers=owner_h,
        json={"text": "ping"},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["events"][-1]["type"] == "done"

    metrics = client.get(f"/api/v1/admin/companies/{company_id}/metrics", headers=admin_h)
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["projects_total"] >= 1
    assert body["agent_tokens_used"] > 0
    assert body["agent_messages"] >= 1
    assert "storage_bytes" in body
    assert body["storage_bytes"] >= 0
    assert "last_activity_at" in body
    assert body["last_activity_at"] is not None

    platform_metrics = client.get("/api/v1/admin/metrics/companies", headers=admin_h)
    assert platform_metrics.status_code == 200
    items = platform_metrics.json()["items"]
    assert any(i["company_id"] == company_id for i in items)
    match = next(i for i in items if i["company_id"] == company_id)
    assert match["agent_tokens_used"] == body["agent_tokens_used"]

    peer_h = {"Authorization": f"Bearer {_token(sub='peer', email='peer@other.test')}"}
    denied = client.get(f"/api/v1/cabinets/{cabinet_id}", headers=peer_h)
    assert denied.status_code == 403

    chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "via chat endpoint"},
    )
    assert chat.status_code == 200, chat.text
    assert "Cursor fixture:" in chat.json()["assistant_text"]

    import json

    stream_events: list[dict] = []
    with client.stream(
        "POST",
        f"/api/v1/projects/{project_id}/chat/stream",
        headers=owner_h,
        json={"text": "stream smoke", "session_id": chat.json()["session_id"]},
    ) as resp:
        assert resp.status_code == 200, resp.text
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            stream_events.append(json.loads(line.removeprefix("data: ")))
    assert any(e.get("type") == "text_delta" for e in stream_events)
    assert any(e.get("type") == "_turn_complete" for e in stream_events)


@requires_postgres
def test_e2e_disabled_employee_cannot_chat(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-dis-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Disabled Co", "password": "test-company-pass", "admin_email": "disabled@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]
    emp_id = co.json()["admin_employee"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-dis",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    disable = client.post(f"/api/v1/employees/{emp_id}/disable", headers=admin_h)
    assert disable.status_code == 200

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "should fail"},
    )
    assert blocked.status_code == 403


@requires_postgres
def test_e2e_disabled_ai_key_blocks_session(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-key-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Key Co", "password": "test-company-pass", "admin_email": "key@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "SoonDisabled",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-soon",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    disabled = client.patch(
        f"/api/v1/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    cascade = disabled.json().get("runtime_cascade") or {}
    assert project_id in (cascade.get("projects_paused") or [])

    paused = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "no key"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "PROJECT_PAUSED"


@requires_postgres
def test_e2e_expired_ai_key_by_date_blocks_chat(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-exp-admin', platform_admin=True)}"}
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Expire Co", "password": "test-company-pass", "admin_email": "expire@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "PastRenewal",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-past",
            "next_renewal_at": past,
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "expired key"},
    )
    assert blocked.status_code in (404, 409)
    assert blocked.json()["code"] in ("NO_AI_KEY", "PROJECT_PAUSED")

    disabled = client.get(f"/api/v1/admin/ai-keys/{key_id}", headers=admin_h)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    paused = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    # Renew date only — still disabled; project stays paused (no auto-resume).
    client.post(
        f"/api/v1/admin/ai-keys/{key_id}/renew",
        headers=admin_h,
        json={"months": 1},
    )
    client.patch(
        f"/api/v1/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "active"},
    )
    still = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert still.json()["status"] == "paused"

    resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"


@requires_postgres
def test_e2e_agent_budget_blocks_followup(client: TestClient) -> None:
    """Vertical slice: policy max_tokens_per_run → chat follow-up AGENT_BUDGET 429."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-budget-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Budget E2E Co", "password": "test-company-pass", "admin_email": "budget@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers=admin_h,
        json={"tool_preset": "workspace_dev", "max_tokens_per_run": 50},
    )
    assert policy.status_code == 200, policy.text

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Budget Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-budget-e2e",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    first = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "hello"},
    )
    assert first.status_code == 200, first.text
    session_id = first.json()["session_id"]

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "again", "session_id": session_id},
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "AGENT_BUDGET"


@requires_postgres
def test_e2e_usd_cost_cap_blocks_followup(client: TestClient) -> None:
    """Vertical slice: max_cost_usd_month → follow-up AGENT_BUDGET after fixture usage."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-usd-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "USD Cap Co", "password": "test-company-pass", "admin_email": "usd@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers=admin_h,
        json={"tool_preset": "workspace_dev", "max_cost_usd_month": 0.01},
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["max_cost_usd_month"] == 0.01

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "USD Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-usd-e2e",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    first = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "spend"},
    )
    assert first.status_code == 200, first.text

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "again"},
    )
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["code"] == "AGENT_BUDGET"
    assert "USD" in blocked.json()["detail"]


@requires_postgres
def test_e2e_tool_approval_hitl(client: TestClient) -> None:
    """dangerous: message → pending approval → approve continues turn."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-hitl-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "HITL Co", "password": "test-company-pass", "admin_email": "hitl@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "HITL Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-hitl",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    turn = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "dangerous: rm -rf /tmp/demo"},
    )
    assert turn.status_code == 200, turn.text
    body = turn.json()
    session_id = body["session_id"]
    pending = body["pending_approvals"]
    assert len(pending) == 1
    approval_id = pending[0]["id"]
    assert pending[0]["name"] == "shell.exec"
    assert not any(e.get("type") == "done" for e in body["events"])

    listed = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/pending-approvals",
        headers=owner_h,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["id"] == approval_id

    denied = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/tool-approvals",
        headers=owner_h,
        json={"id": approval_id, "decision": "deny"},
    )
    assert denied.status_code == 200, denied.text
    assert denied.json()["decision"] == "deny"
    assert any(e.get("type") == "done" for e in denied.json()["events"])

    empty = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/pending-approvals",
        headers=owner_h,
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    again = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "dangerous: echo hi", "session_id": session_id},
    )
    assert again.status_code == 200, again.text
    apr2 = again.json()["pending_approvals"][0]["id"]
    approved = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/tool-approvals",
        headers=owner_h,
        json={"id": apr2, "decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["decision"] == "approve"
    assert "Approved" in approved.json()["assistant_text"]


@requires_postgres
def test_e2e_company_suspend_blocks_chat_and_lists_subscription(client: TestClient) -> None:
    """L04→L07→L09: expire subscription → COMPANY_SUSPENDED + project DTO flag."""
    admin = _token(sub="e2e-admin-sus", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "E2ESusCo", "password": "test-company-pass", "admin_email": "owner@e2esus.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_h = _owner_h(created.json())

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "E2E Sus Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-e2e-sus",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2ESusCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2ESusProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]
    assert proj.json()["company_subscription"]["subscription_expired"] is False

    ok_chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "before suspend"},
    )
    assert ok_chat.status_code == 200, ok_chat.text

    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sub = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": past},
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["subscription_expired"] is True

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    assert got.json()["company_subscription"]["subscription_expired"] is True

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "after suspend"},
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["code"] == "COMPANY_SUSPENDED"

    events = client.get(
        f"/api/v1/admin/platform-events?company_id={company_id}&event_type=company.suspended",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert events.status_code == 200, events.text
    assert len(events.json()["items"]) >= 1


@requires_postgres
def test_e2e_project_pause_blocks_chat(client: TestClient) -> None:
    """L07→L09: manual pause → PROJECT_PAUSED on chat; resume restores writes."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-pause-admin', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2EPauseCo", "password": "test-company-pass", "admin_email": "owner@e2epause.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "E2E Pause Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-e2e-pause",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2EPauseCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2EPauseProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]
    assert proj.json()["status"] == "active"

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "paused"

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "paused"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "PROJECT_PAUSED"

    # Session create is runtime — blocked; cancel remains cleanup path.
    sess_blocked = client.post(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h, json={})
    assert sess_blocked.status_code == 409
    assert sess_blocked.json()["code"] == "PROJECT_PAUSED"

    resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert resumed.status_code == 200, resumed.text

    ok = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "after resume"},
    )
    assert ok.status_code == 200, ok.text


@requires_postgres
def test_e2e_idle_pause_sweep_vertical(client: TestClient) -> None:
    """L04→L07→L09: idle policy + admin sweep pauses stale project."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-idle-admin', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2EIdleCo", "password": "test-company-pass", "admin_email": "owner@e2eidle.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers=admin_h,
        json={"tool_preset": "workspace_dev", "idle_pause_after_hours": 24},
    )
    assert policy.status_code == 200, policy.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2EIdleCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2EIdleProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    stale = datetime.now(UTC) - timedelta(hours=48)
    sql_backdate_project(project_id, stale)

    swept = client.post(
        f"/api/v1/admin/companies/{company_id}/idle-pause/sweep",
        headers=admin_h,
    )
    assert swept.status_code == 200, swept.text
    assert swept.json()["count"] == 1

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "paused"

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "idle paused"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "PROJECT_PAUSED"


@requires_postgres
def test_e2e_attachment_content_download_and_paused_read(client: TestClient) -> None:
    """L07→L09: PNG content download; read still allowed when project paused."""
    import base64

    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-att-admin', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2EAttCo", "password": "test-company-pass", "admin_email": "owner@e2eatt.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2EAttCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2EAttProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAD0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "dot.png",
            "content_base64": base64.b64encode(png).decode("ascii"),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["id"]
    assert uploaded.json()["content_type"] == "image/png"

    content = client.get(
        f"/api/v1/projects/{project_id}/attachments/{attachment_id}/content",
        headers=owner_h,
    )
    assert content.status_code == 200, content.text
    assert content.content == png
    assert content.headers["content-type"].startswith("image/png")

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text

    # Read path must stay open while paused (viewer / transcript reload).
    still = client.get(
        f"/api/v1/projects/{project_id}/attachments/{attachment_id}/content",
        headers=owner_h,
    )
    assert still.status_code == 200, still.text
    assert still.content == png

    blocked_upload = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "blocked.txt",
            "content_base64": base64.b64encode(b"nope").decode("ascii"),
        },
    )
    assert blocked_upload.status_code == 409, blocked_upload.text
    assert blocked_upload.json()["code"] == "PROJECT_PAUSED"


@requires_postgres
def test_e2e_text_json_attachment_content_types(client: TestClient) -> None:
    """L07→L09: .txt/.json upload sniff + content download for text preview."""
    import base64

    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-txt-admin', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2ETxtCo", "password": "test-company-pass", "admin_email": "owner@e2etxt.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2ETxtCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2ETxtProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    note = b"spec line one\nspec line two"
    txt = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(note).decode("ascii"),
        },
    )
    assert txt.status_code == 201, txt.text
    assert txt.json()["content_type"] == "text/plain"
    txt_id = txt.json()["id"]

    txt_body = client.get(
        f"/api/v1/projects/{project_id}/attachments/{txt_id}/content",
        headers=owner_h,
    )
    assert txt_body.status_code == 200
    assert txt_body.content == note

    payload = b'{"sku":"ABC","qty":2}'
    js = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "data.json",
            "content_base64": base64.b64encode(payload).decode("ascii"),
        },
    )
    assert js.status_code == 201, js.text
    assert js.json()["content_type"] == "application/json"
    js_id = js.json()["id"]

    js_body = client.get(
        f"/api/v1/projects/{project_id}/attachments/{js_id}/content",
        headers=owner_h,
    )
    assert js_body.status_code == 200
    assert js_body.content == payload
    assert js_body.headers["content-type"].startswith("application/json")


@requires_postgres
def test_e2e_platform_idle_pause_sweep_all(client: TestClient) -> None:
    """L04→L07→L09: POST /admin/triggers/idle-pause/sweep across companies."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-idle-all-admin', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2EIdleAllCo", "password": "test-company-pass", "admin_email": "owner@e2eidleall.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers=admin_h,
        json={"tool_preset": "workspace_dev", "idle_pause_after_hours": 12},
    )
    assert policy.status_code == 200, policy.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2EIdleAllCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2EIdleAllProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    sql_backdate_project(project_id, datetime.now(UTC) - timedelta(hours=48))

    swept = client.post("/api/v1/admin/triggers/idle-pause/sweep", headers=admin_h)
    assert swept.status_code == 200, swept.text
    body = swept.json()
    assert body["count"] >= 1
    assert any(
        c.get("company_id") == company_id and c.get("count", 0) >= 1 for c in body["companies"]
    )

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200
    assert got.json()["status"] == "paused"


@requires_postgres
def test_e2e_paused_blocks_triggers_allows_metadata(client: TestClient) -> None:
    """L07→L09: paused blocks chat.message trigger; PATCH still works."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-pm-admin', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "E2EPauseMatrixCo", "password": "test-company-pass", "admin_email": "owner@e2epm.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "E2E PM Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-e2e-pm",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = _owner_h(co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2EPauseMatrixCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2EPauseMatrixProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]

    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    blocked = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "no"}},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "PROJECT_PAUSED"

    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"name": "Renamed While Paused"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed While Paused"
    assert patched.json()["status"] == "paused"
