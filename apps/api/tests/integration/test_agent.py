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

from prodavan.application.agent.openclaw_bridge import OpenClawBridgeBootstrap
from prodavan.config.settings import settings
from prodavan.domain.agent import AgentEvent, AgentEventType
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres
from tests.integration.support import configure_and_launch, owner_auth_from_company, owner_bearer_token


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
def test_agent_session_send_persists_events(client: TestClient) -> None:
    admin = _token(sub="adm-agent", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "AgentCo", "password": "test-company-pass", "admin_email": "owner@agentco.test"},
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

    owner_tok = owner_bearer_token(_token, co.json())
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "AgentCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Run 1"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

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

    configure_and_launch(client, owner_h, project_id)

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
def test_agent_session_send_via_bridge_proxy_persists_single_seq(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock_iter_send_events(self, **kwargs):
        yield AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "from-bridge"})
        yield AgentEvent.now(AgentEventType.DONE, {"reason": "completed"})

    monkeypatch.setattr(settings, "pod_agent_bridge_enabled", True)
    monkeypatch.setattr(OpenClawBridgeBootstrap, "iter_send_events", _mock_iter_send_events)

    admin = _token(sub="adm-bridge", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "BridgeCo", "password": "test-company-pass", "admin_email": "owner@bridgeco.test"},
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
            "secret": "sk-test-bridge",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {owner_bearer_token(_token, co.json())}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "BridgeCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "Bridge Run"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

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
        json={"text": "via bridge"},
    )
    assert sent.status_code == 200, sent.text
    events = sent.json()["events"]
    assert any(e["type"] == "text_delta" for e in events)
    assert events[-1]["type"] == "done"

    listed = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=owner_h,
    )
    assert listed.status_code == 200
    items = listed.json()["items"]
    seqs = [item["seq"] for item in items]
    assert len(seqs) == len(set(seqs))
    assert len(items) == 3  # user_message + text_delta + done


@requires_postgres
def test_project_chat_turn_creates_and_reuses_session(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='chat-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "ChatCo", "password": "test-company-pass", "admin_email": "chat@co.test"},
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

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "ChatCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "ChatProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

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
    blocks = transcript.json()["blocks"]
    assert transcript.json()["session_id"] == session_id
    assert blocks[0]["kind"] == "user"
    assert blocks[0]["text"] == "hello"
    assert any(b["kind"] == "assistant_markdown" for b in blocks)


@requires_postgres
def test_chat_blocked_when_pod_not_launched(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='chat-block-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "BlockCo", "password": "test-company-pass", "admin_email": "block@co.test"},
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
            "secret": "sk-block",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "BlockCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "BlockProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    blocked = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "hello"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "POD_NOT_RUNNING"


@requires_postgres
def test_agent_budget_per_run_blocks_followup(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='budget-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "BudgetCo", "password": "test-company-pass", "admin_email": "boss@budget.test"},
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

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "BudgetCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "BudgetProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

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
        json={"name": "StreamCo", "password": "test-company-pass", "admin_email": "stream@agentco.test"},
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

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "StreamCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "StreamProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

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
    blocks = transcript.json()["blocks"]
    assert any("stream me" in b.get("text", "") for b in blocks if b.get("kind") == "assistant_markdown")


@requires_postgres
def test_chat_with_attachment_refs_emits_tool_call(client: TestClient) -> None:
    import base64

    admin = _token(sub="attach-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "AttachCo", "password": "test-company-pass", "admin_email": "attach@agentco.test"},
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

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "AttachCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "AttachProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

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
    assert storage_ref in str(tool_calls[0].get("data", {}))

    transcript = client.get(
        f"/api/v1/projects/{project_id}/chat/transcript?session_id={session_id}",
        headers=owner_h,
    )
    assert transcript.status_code == 200, transcript.text
    blocks = transcript.json()["blocks"]
    assert any(b.get("kind") == "user" and "process file" in b.get("text", "") for b in blocks)
    assert any(b.get("kind") == "tool_call" and "mcp.cabinet.info" in b.get("name", "") for b in blocks)


@requires_postgres
def test_chat_rejects_unknown_attachment_ref(client: TestClient) -> None:
    admin = _token(sub="ref-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "RefCo", "password": "test-company-pass", "admin_email": "ref@agentco.test"},
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
            "secret": "sk-ref",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "RefCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "RefProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

    bad = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "nope", "attachment_refs": ["file://projects/other/inbox/x.txt"]},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "ATTACHMENT_NOT_FOUND"


@requires_postgres
def test_chat_accepts_attachment_id_ref(client: TestClient) -> None:
    import base64

    admin = _token(sub="id-ref-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "IdRefCo", "password": "test-company-pass", "admin_email": "idref@agentco.test"},
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
            "secret": "sk-idref",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "IdRefCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "IdRefProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=owner_h,
        json={
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"by id").decode("ascii"),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["id"]

    turn = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "use id", "attachment_refs": [attachment_id]},
    )
    assert turn.status_code == 200, turn.text


@requires_postgres
def test_agent_session_uses_platform_fallback_pool(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='fb-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "FallbackAgentCo", "password": "test-company-pass", "admin_email": "fb@agentco.test"},
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
    pool_key_id = pool.json()["id"]

    bound = client.put(
        f"/api/v1/admin/ai-keys/{pool_key_id}/companies",
        headers=admin_h,
        json={"company_ids": [company_id]},
    )
    assert bound.status_code == 200, bound.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "FbCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "FbProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)

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
        json={"name": "TrigCo", "password": "test-company-pass", "admin_email": "trig@agentco.test"},
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

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "TrigCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "TrigProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)
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
        json={"name": "DrainCo", "password": "test-company-pass", "admin_email": "drain@agentco.test"},
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

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "DrainCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "DrainProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)
    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)

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


@requires_postgres
def test_trigger_regenerate_and_webhook_ack(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='regen-admin', platform_admin=True)}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "RegenCo", "password": "test-company-pass", "admin_email": "regen@agentco.test"},
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
            "secret": "sk-regen",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "RegenCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "RegenProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)
    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)

    first = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "original turn"},
    )
    assert first.status_code == 200, first.text
    session_id = first.json()["session_id"]

    regen = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.regenerate", "payload": {"session_id": session_id}},
    )
    assert regen.status_code == 202, regen.text
    dispatched = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch",
        headers=owner_h,
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json().get("dispatched") is True

    webhook = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "webhook.http", "payload": {"source": "test"}},
    )
    assert webhook.status_code == 202, webhook.text
    ack = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch",
        headers=owner_h,
    )
    assert ack.status_code == 200, ack.text
    assert ack.json().get("dispatched") is True
    assert ack.json().get("action") == "ack_webhook.http"


@requires_postgres
def test_agent_session_create_blocked_cancel_allowed_when_paused(client: TestClient) -> None:
    admin = _token(sub="adm-sess-pause", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "SessPauseCo", "password": "test-company-pass", "admin_email": "owner@sesspause.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "SessPause Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-sess-pause",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "SessPauseCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "SessPauseProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)

    sess = client.post(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h, json={})
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    # Pause auto-suspends ACTIVE sessions (recoverable on resume).
    listed = client.get(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h)
    assert listed.status_code == 200, listed.text
    sess_row = next(s for s in listed.json()["items"] if s["id"] == session_id)
    assert sess_row["status"] == "suspended"

    blocked = client.post(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h, json={})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "PROJECT_PAUSED"

    # Chat without session_id must not create orphan session while paused.
    chat = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "orphan?"},
    )
    assert chat.status_code == 409
    assert chat.json()["code"] == "PROJECT_PAUSED"

    # Explicit cancel remains allowed (idempotent cleanup).
    cancelled = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/cancel",
        headers=owner_h,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    # Transcript without session_id still returns history after auto-suspend.
    transcript = client.get(f"/api/v1/projects/{project_id}/chat/transcript", headers=owner_h)
    assert transcript.status_code == 200, transcript.text
    assert transcript.json()["session_id"] == session_id
    assert transcript.json().get("session_status") == "suspended"

    client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)

    # Suspended session_id is reactivated and reused after resume.
    after = client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=owner_h,
        json={"text": "after resume", "session_id": session_id},
    )
    assert after.status_code == 200, after.text
    assert after.json()["session_id"] == session_id


@requires_postgres
def test_project_prepare_allowed_while_paused(client: TestClient) -> None:
    admin = _token(sub="padmin-prep-pause", platform_admin=True)
    created_co = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "PrepPauseCo", "password": "test-company-pass", "admin_email": "owner@preppause.test"},
    )
    assert created_co.status_code == 201, created_co.text
    company_id = created_co.json()["company"]["id"]
    owner_tok = owner_bearer_token(_token, created_co.json())
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "PrepPauseCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PrepPauseProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    queued = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "project.prepare", "payload": {}},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["kind"] == "project.prepare"


@requires_postgres
def test_ai_key_disable_cancels_session_and_pauses_project(client: TestClient) -> None:
    admin = _token(sub="adm-key-cascade", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "KeyCascadeCo", "password": "test-company-pass", "admin_email": "owner@keycascade.test"},
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "OnlyKey",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-cascade",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    owner_tok = owner_bearer_token(_token, co.json())
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "CascadeCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "CascadeProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    configure_and_launch(client, owner_h, project_id)

    sess = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    assert sess.json()["resolved_key_id"] == key_id
    session_id = sess.json()["id"]

    disabled = client.patch(
        f"/api/v1/admin/ai-keys/{key_id}",
        headers=admin_h,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    cascade = disabled.json()["runtime_cascade"]
    assert cascade["sessions_cancelled"] >= 1
    assert project_id in cascade["projects_paused"]

    listed = client.get(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h)
    assert listed.status_code == 200
    match = next(s for s in listed.json()["items"] if s["id"] == session_id)
    assert match["status"] == "cancelled"

    project = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert project.status_code == 200
    assert project.json()["status"] == "paused"


@requires_postgres
def test_append_agent_event_hybrid_write(client: TestClient) -> None:
    admin = _token(sub="append-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "AppendCo", "password": "test-company-pass", "admin_email": "append@co.test"},
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
            "secret": "sk-append",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "AppendCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "AppendProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

    sess = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    appended = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=owner_h,
        json={"type": "user_message", "data": {"text": "from openclaw"}},
    )
    assert appended.status_code == 201, appended.text
    assert appended.json()["type"] == "user_message"
    assert appended.json()["seq"] == 1

    bad = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=owner_h,
        json={"type": "not_a_real_type", "data": {}},
    )
    assert bad.status_code == 422

    listed = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=owner_h,
    )
    assert listed.status_code == 200
    assert any(e["type"] == "user_message" for e in listed.json()["items"])

    transcript = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/transcript",
        headers=owner_h,
    )
    assert transcript.status_code == 200
    blocks = transcript.json()["blocks"]
    assert any(b.get("text") == "from openclaw" for b in blocks if b.get("kind") == "assistant_markdown")


@requires_postgres
def test_pod_agent_service_token_append_and_list(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", "pod-test-secret")
    admin = _token(sub="pod-agent-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "PodAgentCo", "password": "test-company-pass", "admin_email": "pod@co.test"},
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
            "secret": "sk-pod",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "PodCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PodProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

    sess = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions",
        headers=owner_h,
        json={},
    )
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    pod_h = {"Authorization": "Bearer pod-test-secret"}
    appended = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=pod_h,
        json={
            "type": "tool_approval_request",
            "data": {"id": "apr-1", "name": "shell.exec", "input": {"cmd": "ls"}},
        },
    )
    assert appended.status_code == 201, appended.text
    assert appended.json()["type"] == "tool_approval_request"

    pending = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/pending-approvals",
        headers=pod_h,
    )
    assert pending.status_code == 200, pending.text
    assert any(p["id"] == "apr-1" for p in pending.json()["items"])

    denied_user = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/events",
        headers=pod_h,
        json={"type": "user_message", "data": {"text": "blocked for pod"}},
    )
    assert denied_user.status_code == 422

    bad_token = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/pending-approvals",
        headers={"Authorization": "Bearer wrong"},
    )
    assert bad_token.status_code == 401


@requires_postgres
def test_fork_session_creates_new_active_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", False)
    admin = _token(sub="fork-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "ForkCo", "password": "test-company-pass", "admin_email": "fork@co.test"},
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
            "secret": "sk-fork",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "ForkCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "ForkProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

    sess = client.post(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h, json={})
    assert sess.status_code == 201, sess.text
    source_id = sess.json()["id"]

    forked = client.post(
        f"/api/v1/projects/{project_id}/agent/sessions/{source_id}/fork",
        headers=owner_h,
    )
    assert forked.status_code == 201, forked.text
    new_id = forked.json()["id"]
    assert new_id != source_id
    assert forked.json()["status"] == "active"
    assert forked.json()["api_kind"] == "cursor_sdk"


@requires_postgres
def test_sidechain_transcript_unavailable_without_runtime(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", False)
    admin = _token(sub="side-admin", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}

    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "SideCo", "password": "test-company-pass", "admin_email": "side@co.test"},
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
            "secret": "sk-side",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "SideCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "SideProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    configure_and_launch(client, owner_h, project_id)

    sess = client.post(f"/api/v1/projects/{project_id}/agent/sessions", headers=owner_h, json={})
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    resp = client.get(
        f"/api/v1/projects/{project_id}/agent/sessions/{session_id}/sidechains/toolu_abc/transcript",
        headers=owner_h,
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "RUNTIME_UNAVAILABLE"

