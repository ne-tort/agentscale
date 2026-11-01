"""Contract tests for the migration web facade (not wired to legacy routes)."""

import pytest
from pydantic import ValidationError

from prodavan.application.web_api.contracts import (
    ChatRunRequest as WebApiChatRunRequest,
    CurrentUserResponse,
    SseEventEnvelope as WebApiSseEventEnvelope,
)
from prodavan.application.web_facade.auth import ScopeBoundary, ScopeViolation, WebRequestContext
from prodavan.application.web_facade.contracts import (
    ChatBlock,
    ChatBlockKind,
    ChatRunRequest,
    CurrentUser,
    RunEventType,
    RunStreamEvent,
    SseEventEnvelope,
)


def _context(*, admin: bool = False) -> WebRequestContext:
    return WebRequestContext(
        user=CurrentUser(id="u1", company_id="co1", is_platform_admin=admin),
        company_id="co1",
        cabinet_id="cab1",
        project_id="project1",
        session_id="session1",
    )


def test_sse_envelope_has_replayable_event_id() -> None:
    event = RunStreamEvent(
        type=RunEventType.TEXT_DELTA,
        run_id="run1",
        seq=7,
        text="hello",
    )

    envelope = SseEventEnvelope.from_run_event(event)

    assert envelope.id == "run1:7"
    assert envelope.event == "text_delta"
    assert envelope.data == event
    assert envelope.model_dump(mode="json")["data"]["seq"] == 7


def test_contract_rejects_unknown_wire_fields() -> None:
    with pytest.raises(ValidationError):
        CurrentUser(id="u1", unknown_field="must not leak")


def test_company_scope_requires_authenticated_tenant() -> None:
    context = _context()
    context.require_company("co1")
    with pytest.raises(ScopeViolation):
        context.require_company("co2")

    missing_tenant = WebRequestContext(user=CurrentUser(id="u2"), company_id="co1")
    with pytest.raises(ScopeViolation):
        missing_tenant.require_company("co1")


def test_scope_boundary_requires_both_project_and_session() -> None:
    boundary = ScopeBoundary(_context())

    assert boundary.project("project1").project_id == "project1"
    assert boundary.session("project1", "session1").session_id == "session1"

    with pytest.raises(ScopeViolation):
        boundary.project("project2")
    with pytest.raises(ScopeViolation):
        boundary.session("project1", "session2")


def test_platform_admin_does_not_bypass_explicit_bound_session() -> None:
    context = WebRequestContext(
        user=CurrentUser(id="admin", is_platform_admin=True),
        company_id=None,
        cabinet_id=None,
        project_id=None,
        session_id=None,
    )
    boundary = ScopeBoundary(context)

    # Admin may address an unselected project, but an explicitly selected session
    # remains bound to its project/session context.
    assert boundary.project("project2").user.is_platform_admin
    with pytest.raises(ScopeViolation):
        boundary.session("project2", "session2")


def test_web_api_request_adapts_to_canonical_facade_request() -> None:
    request = WebApiChatRunRequest(project_id="project1", session_id="session1", text="hello")

    canonical = request.to_facade()

    assert canonical == ChatRunRequest(project_id="project1", session_id="session1", prompt="hello")


def test_web_api_user_and_sse_adapters_preserve_identity_and_cursor() -> None:
    user = CurrentUserResponse(id="u1", subject="kc-u1", roles=("platform.admin",))
    event = RunStreamEvent(type=RunEventType.TEXT_DELTA, run_id="run1", seq=7, text="hello")

    wire_event = WebApiSseEventEnvelope.from_facade_event(
        event, project_id="project1", session_id="session1"
    )

    assert user.to_facade().id == user.id
    assert user.to_facade().is_platform_admin is True
    assert wire_event.sse_event_name == "text_delta"
    assert wire_event.sequence == event.seq
    assert wire_event.resume_after == event.seq


def test_run_request_and_block_are_typed() -> None:
    request = ChatRunRequest(project_id="project1", session_id="session1", prompt="hello")
    block = ChatBlock(
        seq=1,
        kind=ChatBlockKind.ASSISTANT_MARKDOWN,
        content="answer",
        metadata={"source": "runtime"},
    )
    event = RunStreamEvent(
        type=RunEventType.BLOCK,
        run_id="run1",
        seq=2,
        block=block,
    )

    assert request.prompt == "hello"
    assert event.block is not None
    assert event.block.kind == ChatBlockKind.ASSISTANT_MARKDOWN
