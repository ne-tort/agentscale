"""Unit tests — HITL pending approvals helper (L08)."""

from prodavan.application.agent.session_service import _pending_approvals_from_events
from prodavan.domain.agent import AgentEventType, PLATFORM_EVENT_TOOL_APPROVAL_DECISION


def test_pending_approvals_until_resolved() -> None:
    events = [
        {"type": AgentEventType.TOOL_APPROVAL_REQUEST, "data": {"id": "a1", "name": "shell.exec", "input": {}}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "wait"}},
    ]
    pending = _pending_approvals_from_events(events)
    assert len(pending) == 1
    assert pending[0]["id"] == "a1"


def test_pending_cleared_by_decision() -> None:
    events = [
        {"type": AgentEventType.TOOL_APPROVAL_REQUEST, "data": {"id": "a1", "name": "shell.exec", "input": {}}},
        {"type": PLATFORM_EVENT_TOOL_APPROVAL_DECISION, "data": {"id": "a1", "decision": "deny"}},
    ]
    assert _pending_approvals_from_events(events) == []


def test_pending_cleared_by_tool_result() -> None:
    events = [
        {"type": AgentEventType.TOOL_APPROVAL_REQUEST, "data": {"id": "a1", "name": "shell.exec", "input": {}}},
        {"type": AgentEventType.TOOL_RESULT, "data": {"id": "a1", "name": "shell.exec", "output": {}}},
    ]
    assert _pending_approvals_from_events(events) == []
