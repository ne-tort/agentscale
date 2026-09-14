"""Unit tests — mid-turn PG flush for agent chat durability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.session_service import _STREAM_FLUSH_EVERY, AgentSessionService
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentEvent, AgentEventType


@pytest.mark.asyncio
async def test_flush_events_commits_session() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    svc = AgentSessionService(session)
    await svc._flush_events()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_flush_stream_on_threshold_and_types() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    svc = AgentSessionService(session)

    n = await svc._maybe_flush_stream(since_flush=_STREAM_FLUSH_EVERY, event_type=AgentEventType.TEXT_DELTA)
    assert n == 0
    assert session.commit.await_count == 1

    n = await svc._maybe_flush_stream(since_flush=1, event_type=AgentEventType.TOOL_RESULT)
    assert n == 0
    assert session.commit.await_count == 2

    n = await svc._maybe_flush_stream(since_flush=1, event_type=AgentEventType.TEXT_DELTA)
    assert n == 1
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_iter_send_events_commits_user_message_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """user_message is committed before the vendor stream starts."""
    commits: list[str] = []

    session = AsyncMock()
    session.add = MagicMock()

    async def _commit() -> None:
        commits.append("commit")

    session.commit = AsyncMock(side_effect=_commit)
    session.execute = AsyncMock(
        return_value=SimpleNamespace(
            scalar_one=lambda: 0,
            scalar_one_or_none=lambda: 0,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        ),
    )

    svc = AgentSessionService(session)
    project = SimpleNamespace(
        id="proj_1",
        company_id="co_1",
        cabinet_id="cab_1",
        workspace_key="ws1",
        agent_provider=None,
        resolved_ai_key_id=None,
    )
    row = SimpleNamespace(
        id="sess_1",
        project_id="proj_1",
        status="active",
        vendor_agent_id="va",
        provider="openclaw",
        api_kind="platform_openclaw",
        cwd="/workspace",
        model="m",
        resolved_key_id=None,
        adapter_state=None,
        title=None,
        last_message_at=None,
    )

    async def _require_access(**_kwargs):
        return project

    async def _require_active(_company_id):
        return None

    async def _get_session(*, session_id: str):
        assert session_id == "sess_1"
        return row

    async def _resolve_send_model(**_kwargs):
        return "m"

    async def _enforce(**_kwargs):
        return None

    async def _normalize_refs(**_kwargs):
        return []

    async def _require_running(_session, **_kwargs):
        return {"runtime_ref": "pod-x"}

    monkeypatch.setattr(svc._projects, "require_access", _require_access)
    monkeypatch.setattr(svc._subscription, "require_active", _require_active)
    monkeypatch.setattr(svc, "get_session", _get_session)
    monkeypatch.setattr(svc, "_resolve_send_model", _resolve_send_model)
    monkeypatch.setattr(svc._budget, "enforce_before_turn", _enforce)
    monkeypatch.setattr(
        "prodavan.application.agent.session_service.require_running_pod_runtime",
        _require_running,
    )
    monkeypatch.setattr(
        "prodavan.application.agent.session_service.ProjectAttachmentService",
        lambda _s: SimpleNamespace(normalize_refs=_normalize_refs),
    )
    monkeypatch.setattr(
        "prodavan.application.agent.session_service.AdminCompanyService",
        lambda _s: SimpleNamespace(get_agent_policy=AsyncMock(return_value={})),
    )
    monkeypatch.setattr(
        "prodavan.application.agent.session_service.settings",
        SimpleNamespace(pod_agent_runtime_enabled=False),
    )

    class _Adapter:
        async def send(self, _handle, _message):
            yield AgentEvent(type=AgentEventType.TEXT_DELTA, data={"text": "hi"})
            yield AgentEvent(type=AgentEventType.DONE, data={})

    monkeypatch.setattr(
        "prodavan.application.agent.session_service.get_agent_adapter",
        lambda **_kwargs: _Adapter(),
    )
    monkeypatch.setattr(
        "prodavan.application.metrics.publish.schedule_agent_request",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(svc, "_checkpoint_workspace_after_turn", AsyncMock())

    employee = SimpleNamespace(id="emp_1")
    principal = SimpleNamespace(sub="u1")
    events = []
    async for ev in svc._iter_send_events(
        project_id="proj_1",
        session_id="sess_1",
        text="hello",
        attachment_refs=None,
        principal=principal,  # type: ignore[arg-type]
        employee=employee,  # type: ignore[arg-type]
    ):
        events.append(ev)
        if ev.get("type") == AgentEventType.TEXT_DELTA:
            assert commits, "user_message must be committed before stream deltas"

    assert events[0]["type"] == PLATFORM_EVENT_USER_MESSAGE
    assert commits
    assert any(e.get("type") == AgentEventType.DONE for e in events)
