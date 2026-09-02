"""Unit tests — AgentEvent frozen schema."""

import pytest

from prodavan.domain.agent import AgentEvent, AgentEventType


def test_agent_event_types() -> None:
    ev = AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "hi"})
    assert ev.type == "text_delta"
    assert ev.to_dict()["data"]["text"] == "hi"


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValueError):
        AgentEvent(type="unknown", data={})


def test_extended_event_type_allowed() -> None:
    ev = AgentEvent.now(AgentEventType.SUBAGENT_START, {"agent_id": "sub-1"})
    assert ev.type == "subagent_start"


def test_assistant_text_from_events() -> None:
    from prodavan.application.agent.session_service import _assistant_text_from_events

    events = [
        {"type": "text_delta", "data": {"text": "Hello "}},
        {"type": "usage", "data": {"input_tokens": 1}},
        {"type": "text_delta", "data": {"text": "world"}},
    ]
    assert _assistant_text_from_events(events) == "Hello world"


def test_events_to_transcript() -> None:
    from prodavan.application.agent.session_service import events_to_transcript

    events = [
        {"type": "user_message", "data": {"text": "hi"}},
        {"type": "text_delta", "data": {"text": "Hello "}},
        {"type": "text_delta", "data": {"text": "there"}},
        {"type": "usage", "data": {"input_tokens": 1}},
        {"type": "done", "data": {"reason": "completed"}},
        {"type": "user_message", "data": {"text": "again"}},
        {"type": "text_delta", "data": {"text": "ok"}},
        {"type": "done", "data": {}},
    ]
    assert events_to_transcript(events) == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "Hello there"},
        {"role": "user", "text": "again"},
        {"role": "assistant", "text": "ok"},
    ]
