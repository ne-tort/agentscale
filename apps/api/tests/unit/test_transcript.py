"""Unit tests — chat transcript collapse (L05/L08)."""

from prodavan.application.agent.session_service import events_to_transcript
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentEventType


def test_events_to_transcript_user_and_assistant() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "hi"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Hello"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": " world"}},
        {"type": AgentEventType.DONE, "data": {"reason": "completed"}},
    ]
    messages = events_to_transcript(events)
    assert messages == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "Hello world"},
    ]


def test_events_to_transcript_multiple_turns() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "one"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "A"}},
        {"type": AgentEventType.DONE, "data": {}},
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "two"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "B"}},
    ]
    messages = events_to_transcript(events)
    assert len(messages) == 4
    assert messages[0]["role"] == "user"
    assert messages[1]["text"] == "A"
    assert messages[2]["text"] == "two"
    assert messages[3]["text"] == "B"


def test_events_to_transcript_includes_tool_call() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "run tool"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Ok"}},
        {
            "type": AgentEventType.TOOL_CALL,
            "data": {"id": "tc_1", "name": "mcp.cabinet.info", "input": {}},
        },
        {"type": AgentEventType.DONE, "data": {}},
    ]
    messages = events_to_transcript(events)
    assert messages == [
        {"role": "user", "text": "run tool"},
        {"role": "assistant", "text": "Ok"},
        {"role": "tool", "text": "mcp.cabinet.info"},
    ]


def test_events_to_transcript_includes_attachment_refs() -> None:
    events = [
        {
            "type": PLATFORM_EVENT_USER_MESSAGE,
            "data": {"text": "see file", "attachment_refs": ["file://projects/a/inbox/n.txt"]},
        },
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "got it"}},
    ]
    messages = events_to_transcript(events)
    assert messages[0]["role"] == "user"
    assert messages[0]["attachment_refs"] == ["file://projects/a/inbox/n.txt"]
    assert messages[1]["text"] == "got it"
