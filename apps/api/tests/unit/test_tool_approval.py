"""Unit tests — HITL pending approvals helper (L08)."""

from prodavan.application.agent.session_service import _pending_approvals_from_events
from prodavan.domain.agent import PLATFORM_EVENT_TOOL_APPROVAL_DECISION, AgentEventType


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


def test_late_approval_remains_pending_after_large_transcript() -> None:
    """Approval state must be independent of the capped transcript tail."""
    events = [
        {"type": AgentEventType.TOOL_APPROVAL_REQUEST, "data": {"id": "old", "name": "shell.exec", "input": {}}},
        {"type": AgentEventType.TOOL_RESULT, "data": {"id": "old", "output": {}}},
        *[
            {"type": AgentEventType.TEXT_DELTA, "data": {"text": str(index)}}
            for index in range(600)
        ],
        {"type": AgentEventType.TOOL_APPROVAL_REQUEST, "data": {"id": "late", "name": "fs.write", "input": {"path": "a.txt"}}},
    ]
    # The state query used by the service returns only these control-plane
    # events, regardless of transcript length.
    control_plane = [event for event in events if event["type"] in {
        AgentEventType.TOOL_APPROVAL_REQUEST,
        AgentEventType.TOOL_RESULT,
        PLATFORM_EVENT_TOOL_APPROVAL_DECISION,
    }]
    pending = _pending_approvals_from_events(control_plane)
    assert [item["id"] for item in pending] == ["late"]
