"""Integration tests — L08 agent port + event persistence."""

from __future__ import annotations

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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    reset_jwt_validator()
    reset_invite_client()
    return TestClient(create_app())


@requires_postgres
def test_agent_session_send_persists_events(client: TestClient) -> None:
    admin = _token(sub="adm-agent", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "AgentCo", "admin_email": "owner@agentco.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-test-cursor",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_tok = _token(sub="owner-agent", email="owner@agentco.test")
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "AgentCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Run 1"},
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
    assert sess.json()["provider"] == "cursor"
    assert sess.json()["api_kind"] == "cursor_sdk"

    sent = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/send",
        headers=owner_h,
        json={"text": "find suppliers"},
    )
    assert sent.status_code == 200, sent.text
    events = sent.json()["events"]
    assert any(e["type"] == "text_delta" for e in events)
    assert any(e["type"] == "usage" for e in events)
    assert events[-1]["type"] == "done"

    listed = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=owner_h,
    )
    assert listed.status_code == 200
    assert len(listed.json()["items"]) >= 3

    trig = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "via trigger"}},
    )
    assert trig.status_code == 202

    disp = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch",
        headers=owner_h,
    )
    assert disp.status_code == 200, disp.text
    assert disp.json()["dispatched"] is True


@requires_postgres
def test_project_chat_turn_creates_and_reuses_session(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='chat-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "ChatCo", "admin_email": "chat@co.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-chat",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {_token(sub='chat-owner', email='chat@co.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "ChatCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "ChatProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    first = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "hello"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    session_id = body["session_id"]
    assert body["assistant_text"]
    assert body["events"][-1]["type"] == "done"

    second = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "again", "session_id": session_id},
    )
    assert second.status_code == 200, second.text
    assert second.json()["session_id"] == session_id

    sessions = client.get(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h)
    assert sessions.status_code == 200
    assert any(s["id"] == session_id for s in sessions.json()["items"])

    transcript = client.get(f"/api/v1/projects/{project_id}/chat/transcript", headers=owner_h)
    assert transcript.status_code == 200, transcript.text
    messages = transcript.json()["messages"]
    assert transcript.json()["session_id"] == session_id
    assert messages[0]["role"] == "user"
    assert messages[0]["text"] == "hello"
    assert any(m["role"] == "assistant" for m in messages)


@requires_postgres
def test_agent_budget_per_run_blocks_followup(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='budget-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "BudgetCo", "admin_email": "boss@budget.test"},
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
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-budget",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {_token(sub='budget-boss', email='boss@budget.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "BudgetCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "BudgetProj"},
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
def test_chat_stream_sse(client: TestClient) -> None:
    import json

    admin = _token(sub="stream-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "StreamCo", "admin_email": "stream@agentco.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-stream",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {_token(sub='stream-owner', email='stream@agentco.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "StreamCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "StreamProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    events: list[dict] = []
    with client.stream(
        "POST",
        f"/api/v1/projects/{project_id}/chat/stream",
        headers=owner_h,
        json={"text": "stream me"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        assert "text/event-stream" in resp.headers.get("content-type", "")
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            events.append(json.loads(line.removeprefix("data: ")))

    assert events[0]["type"] == "_session"
    session_id = events[0]["data"]["session_id"]
    assert session_id
    deltas = [e for e in events if e.get("type") == "text_delta"]
    assert len(deltas) >= 2
    complete = next(e for e in events if e.get("type") == "_turn_complete")
    assert "Cursor fixture: stream me" in complete["data"]["assistant_text"]

    transcript = client.get(
        f"/api/v1/projects/{project_id}/chat/transcript?session_id={session_id}",
        headers=owner_h,
    )
    assert transcript.status_code == 200
    assert any("stream me" in m.get("text", "") for m in transcript.json()["messages"])


@requires_postgres
def test_chat_with_attachment_refs_emits_tool_call(client: TestClient) -> None:
    import base64

    admin = _token(sub="attach-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "AttachCo", "admin_email": "attach@agentco.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-attach",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {_token(sub='attach-owner', email='attach@agentco.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "AttachCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "AttachProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    note = b"hello attachment"
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(note).decode("ascii"),
            "content_type": "text/plain",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    storage_ref = uploaded.json()["storage_ref"]
    assert storage_ref

    turn = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "process file", "attachment_refs": [storage_ref]},
    )
    assert turn.status_code == 200, turn.text
    session_id = turn.json()["session_id"]

    events = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=owner_h,
    )
    assert events.status_code == 200, events.text
    tool_calls = [e for e in events.json()["items"] if e.get("type") == "tool_call"]
    assert tool_calls
    assert storage_ref in str(tool_calls[0].get("payload", {}))

    transcript = client.get(
        f"/api/v1/projects/{project_id}/chat/transcript?session_id={session_id}",
        headers=owner_h,
    )
    assert transcript.status_code == 200, transcript.text
    msgs = transcript.json()["messages"]
    assert any(m.get("role") == "user" and "process file" in m.get("text", "") for m in msgs)
    assert any(m.get("role") == "tool" and "mcp.cabinet.info" in m.get("text", "") for m in msgs)


@requires_postgres
def test_agent_session_uses_platform_fallback_pool(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='fb-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "FallbackAgentCo", "admin_email": "fb@agentco.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    pool = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Platform pool",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-platform-pool",
            "company_ids": [],
        },
    )
    assert pool.status_code == 201, pool.text

    owner_h = {"Authorization": f"Bearer {_token(sub='fb-owner', email='fb@agentco.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "FbCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "FbProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    sess = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    assert sess.json()["provider"] == "cursor"


@requires_postgres
def test_trigger_dispatch_runs_chat_message(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='trig-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "TrigCo", "admin_email": "trig@agentco.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-trig",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {_token(sub='trig-owner', email='trig@agentco.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "TrigCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "TrigProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    # Drain project.prepare from create before asserting chat.message id.
    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)

    trig = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "from trigger"}},
    )
    assert trig.status_code == 202, trig.text
    trigger_id = trig.json()["id"]

    dispatched = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch",
        headers=owner_h,
    )
    assert dispatched.status_code == 200, dispatched.text
    body = dispatched.json()
    assert body.get("dispatched") is True
    assert body.get("trigger_id") == trigger_id
    assert body.get("run", {}).get("session_id")

    listed = client.get(f"/api/v1/projects/{project_id}/triggers", headers=owner_h)
    assert listed.status_code == 200
    done = next(t for t in listed.json()["items"] if t["id"] == trigger_id)
    assert done["status"] == "done"


@requires_postgres
def test_trigger_dispatch_drain_batch(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='drain-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "DrainCo", "admin_email": "drain@agentco.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-drain",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {_token(sub='drain-owner', email='drain@agentco.test')}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "DrainCab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "DrainProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    for text in ("one", "two"):
        r = client.post(
            f"/api/v1/projects/{project_id}/triggers",
            headers=owner_h,
            json={"kind": "chat.message", "payload": {"text": text}},
        )
        assert r.status_code == 202, r.text

    drained = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=10",
        headers=owner_h,
    )
    assert drained.status_code == 200, drained.text
    body = drained.json()
    assert body.get("dispatched") is True
    assert body.get("count", 0) >= 2
    assert all(item.get("dispatched") for item in body.get("items", []))

    empty = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=5",
        headers=owner_h,
    )
    assert empty.status_code == 200
    assert empty.json().get("dispatched") is False
    assert empty.json().get("reason") == "no queued triggers"
