"""Source tests for the typed legacy web facade integration adapter."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from prodavan.application.web_facade.auth import ScopeViolation, WebRequestContext
from prodavan.application.web_facade.contracts import (
    ChatRunRequest, CurrentUser, RunEventType,
)
from prodavan.application.web_facade_adapter import AdapterScope, WebFacadeAdapter, parse_last_event_id


class FakeServices:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = events or []
        self.calls: list[tuple[str, Any]] = []

    async def current_user(self, *, context):
        self.calls.append(("current_user", context))
        return {"id": "u1", "company_id": "co1"}

    async def projects(self, *, context, cabinet_id):
        self.calls.append(("projects", cabinet_id))
        return [{"id": "p1", "cabinet_id": cabinet_id, "name": "P", "status": "active"}]

    async def container_status(self, *, context, project_id):
        return {"project_id": project_id, "lifecycle": "running", "desired": "running"}

    async def chat_sidebar(self, *, context, cabinet_id):
        return {"cabinet_id": cabinet_id}

    async def chat_transcript(self, *, context, project_id, session_id, after_seq=None):
        return {"project_id": project_id, "session_id": session_id, "status": "active", "blocks": []}

    async def chat_run(self, *, context, request, after_seq=None) -> AsyncIterator[object]:
        self.calls.append(("chat_run", after_seq))
        for event in self.events:
            yield event


def context(*, project: str | None = "p1", session: str | None = "s1", company: str | None = "co1"):
    return WebRequestContext(
        user=CurrentUser(id="u1", company_id=company), company_id=company,
        cabinet_id="cab1", project_id=project, session_id=session,
    )


def test_last_event_id_parser_is_strict_and_replayable() -> None:
    cursor = parse_last_event_id("run-1:7")
    assert cursor is not None
    assert cursor.run_id == "run-1"
    assert cursor.after_seq == 7
    assert parse_last_event_id(" ") is None
    with pytest.raises(ValueError):
        parse_last_event_id("run-1:not-a-number")
    with pytest.raises(ValueError):
        parse_last_event_id("run-1:-1")


@pytest.mark.asyncio
async def test_scope_is_deny_by_default_before_legacy_service() -> None:
    services = FakeServices()
    adapter = WebFacadeAdapter(services)
    with pytest.raises(ScopeViolation):
        await adapter.projects(context=context(), cabinet_id="cab1")
    assert services.calls == []


@pytest.mark.asyncio
async def test_project_and_session_boundaries_are_explicit() -> None:
    adapter = WebFacadeAdapter(FakeServices(), scopes={AdapterScope.CONTAINER_STATUS_READ})
    with pytest.raises(ScopeViolation):
        await adapter.container_status(context=context(project="p1"), project_id="p2")

    adapter = WebFacadeAdapter(FakeServices(), scopes={AdapterScope.CHAT_TRANSCRIPT_READ})
    with pytest.raises(ScopeViolation):
        await adapter.chat_transcript(context=context(session="s1"), project_id="p1", session_id="s2")


@pytest.mark.asyncio
async def test_company_is_not_inferred_from_employee_identity() -> None:
    services = FakeServices()
    adapter = WebFacadeAdapter(services, scopes={AdapterScope.CURRENT_USER_READ})
    value = await adapter.current_user(context=context(company=None))
    assert value.company_id is None


@pytest.mark.asyncio
async def test_legacy_events_translate_and_resume_after_last_event_id() -> None:
    services = FakeServices([
        {"type": "text_delta", "run_id": "run-1", "seq": 7, "text": "old"},
        {"type": "delta", "run_id": "run-1", "seq": 8, "data": {"text": "new"}},
    ])
    adapter = WebFacadeAdapter(services, scopes={AdapterScope.CHAT_RUN})
    events = [event async for event in adapter.run_chat(
        context=context(),
        request=ChatRunRequest(project_id="p1", session_id="s1", prompt="hi"),
        last_event_id="run-1:7",
    )]
    assert services.calls == [("chat_run", 7)]
    assert len(events) == 1
    assert events[0].id == "run-1:8"
    assert events[0].event == RunEventType.TEXT_DELTA.value
