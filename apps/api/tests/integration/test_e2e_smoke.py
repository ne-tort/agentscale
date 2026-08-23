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
        json={"name": "E2E Co", "admin_email": "boss@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-boss', email='boss@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "E2E Cabinet", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "E2E Project"},
    )
    assert proj.status_code == 201, proj.text
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

    tabs = client.get(f"/api/v1/cabinets/{cabinet_id}/meta/tabs", headers=owner_h)
    assert tabs.status_code == 200, tabs.text
    slugs = {t.get("view_slug") for t in tabs.json()}
    assert "projects" in slugs
    assert all(t.get("view_slug") for t in tabs.json())

    tables = client.get(f"/api/v1/cabinets/{cabinet_id}/meta/tables", headers=owner_h)
    assert tables.status_code == 200, tables.text
    table_list = tables.json()
    if table_list:
        first_slug = table_list[0]["slug"]
        detail = client.get(
            f"/api/v1/cabinets/{cabinet_id}/meta/tables/{first_slug}",
            headers=owner_h,
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["slug"] == first_slug
        assert isinstance(body.get("columns"), list)

    exported = client.get(f"/api/v1/cabinets/{cabinet_id}/bundle", headers=owner_h)
    assert exported.status_code == 200, exported.text
    zip_b64 = exported.json()["zip_base64"]
    imported = client.post(
        "/api/v1/cabinets/import",
        headers=owner_h,
        json={"company_id": company_id, "zip_base64": zip_b64, "name": "E2E Imported"},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["cabinet"]["id"] != cabinet_id
    assert imported.json().get("tabs_imported", 0) >= 0


@requires_postgres
def test_e2e_disabled_employee_cannot_chat(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-dis-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Disabled Co", "admin_email": "disabled@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-dis-boss', email='disabled@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code == 201, proj.text
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
        json={"name": "Key Co", "admin_email": "key@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-key-boss', email='key@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    disabled = client.patch(
        f"/api/v1/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "no key"},
    )
    assert blocked.status_code == 404
    assert blocked.json()["code"] == "NO_AI_KEY"


@requires_postgres
def test_e2e_expired_ai_key_by_date_blocks_chat(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-exp-admin', platform_admin=True)}"}
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Expire Co", "admin_email": "expire@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-exp-boss', email='expire@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "expired key"},
    )
    assert blocked.status_code == 404
    assert blocked.json()["code"] == "NO_AI_KEY"

    expired = client.get(f"/api/v1/admin/ai-keys/{key_id}", headers=admin_h)
    assert expired.status_code == 200
    assert expired.json()["status"] == "expired"


@requires_postgres
def test_e2e_agent_budget_blocks_followup(client: TestClient) -> None:
    """Vertical slice: policy max_tokens_per_run → chat follow-up AGENT_BUDGET 429."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-budget-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Budget E2E Co", "admin_email": "budget@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-budget-boss', email='budget@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code == 201, proj.text
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
        json={"name": "USD Cap Co", "admin_email": "usd@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-usd-boss', email='usd@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code == 201, proj.text
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
def test_e2e_starter_bundle_import(client: TestClient) -> None:
    """Download official starter zip → import → line_items table + custom tab."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-starter-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "Starter Co", "admin_email": "starter@e2e.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    catalog = client.get("/api/v1/admin/starter-bundles", headers=admin_h)
    assert catalog.status_code == 200, catalog.text
    match = next(i for i in catalog.json()["items"] if i["id"] == "equipment-procurement")
    assert match["bundle_available"] is True

    bundle = client.get(
        "/api/v1/admin/starter-bundles/equipment-procurement/bundle",
        headers=admin_h,
    )
    assert bundle.status_code == 200, bundle.text
    zip_b64 = bundle.json()["zip_base64"]

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-starter-boss', email='starter@e2e.test')}"}
    imported = client.post(
        "/api/v1/cabinets/import",
        headers=owner_h,
        json={
            "company_id": company_id,
            "zip_base64": zip_b64,
            "name": "Procurement from starter",
        },
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["imported_tables"] >= 1
    assert body["tabs_imported"] >= 1
    cabinet_id = body["cabinet"]["id"]

    tables = client.get(f"/api/v1/cabinets/{cabinet_id}/meta/tables", headers=owner_h)
    assert tables.status_code == 200, tables.text
    assert any(t["slug"] == "line_items" for t in tables.json())

    detail = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables/line_items",
        headers=owner_h,
    )
    assert detail.status_code == 200, detail.text
    col_names = {c["name"] for c in detail.json()["columns"]}
    assert "title" in col_names
    assert "part_number" in col_names

    tabs = client.get(f"/api/v1/cabinets/{cabinet_id}/meta/tabs", headers=owner_h)
    assert tabs.status_code == 200, tabs.text
    custom = [t for t in tabs.json() if t.get("title") == "Line items" and not t.get("system")]
    assert custom
    assert custom[0].get("table_slug") == "line_items" or custom[0].get("view_slug") == "line_items"

    rows = client.get(
        f"/api/v1/cabinets/{cabinet_id}/data/line_items/rows",
        headers=owner_h,
    )
    assert rows.status_code == 200, rows.text
    assert len(rows.json()["rows"]) >= 1


@requires_postgres
def test_e2e_tool_approval_hitl(client: TestClient) -> None:
    """dangerous: message → pending approval → approve continues turn."""
    admin_h = {"Authorization": f"Bearer {_token(sub='e2e-hitl-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "HITL Co", "admin_email": "hitl@e2e.test"},
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

    owner_h = {"Authorization": f"Bearer {_token(sub='e2e-hitl-boss', email='hitl@e2e.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Proj"},
    )
    assert proj.status_code == 201, proj.text
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
