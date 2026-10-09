"""Unit tests — chat block projection (Epic Chat UX)."""

from prodavan.application.agent.chat_projection import events_to_chat_blocks
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentEventType


def test_events_to_chat_blocks_cumulative_text_delta() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "hi"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "При"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Привет"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Привет!"}},
        {"type": AgentEventType.DONE, "data": {"reason": "completed"}},
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks[1]["text"] == "Привет!"


def test_events_to_chat_blocks_user_and_assistant() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "hi"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Hello"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": " world"}},
        {"type": AgentEventType.DONE, "data": {"reason": "completed"}},
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks == [
        {"kind": "user", "text": "hi"},
        {"kind": "assistant_markdown", "text": "Hello world"},
    ]


def test_events_to_chat_blocks_tool_and_thinking() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "go"}},
        {"type": AgentEventType.THINKING_DELTA, "data": {"text": "hmm"}},
        {"type": AgentEventType.THINKING_COMPLETE, "data": {"duration_ms": 120}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Done"}},
        {
            "type": AgentEventType.TOOL_CALL,
            "data": {"id": "tc_1", "name": "Read", "input": {"path": "a.ts"}},
        },
        {
            "type": AgentEventType.TOOL_RESULT,
            "data": {"id": "tc_1", "name": "Read", "output": "ok"},
        },
    ]
    blocks = events_to_chat_blocks(events)
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["user", "thinking", "assistant_markdown", "tool_call", "tool_result"]
    assert blocks[1]["duration_ms"] == 120
    assert blocks[3]["name"] == "Read"


def test_events_to_chat_blocks_subagent_tree() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "spawn"}},
        {
            "type": AgentEventType.SUBAGENT_START,
            "data": {"agent_id": "sub-1", "type": "explore", "parent_tool_use_id": "toolu_1"},
        },
        {
            "type": AgentEventType.SUBAGENT_EVENT,
            "data": {
                "parent_tool_use_id": "sub-1",
                "child_event": {"type": "text_delta", "data": {"text": "nested"}},
            },
        },
        {
            "type": AgentEventType.SUBAGENT_STOP,
            "data": {"agent_id": "sub-1", "result_summary": "done"},
        },
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks[1]["kind"] == "subagent"
    assert blocks[1]["status"] == "completed"
    assert blocks[1]["events"][0]["type"] == "text_delta"


def test_events_to_chat_blocks_subagent_tree_from_persisted_payload() -> None:
    """Events as stored in PG (_event_public shape) — parent_tool_use_id lives in data."""
    events = [
        {
            "seq": 1,
            "type": AgentEventType.SUBAGENT_START,
            "data": {"agent_id": "sub-1", "type": "explore", "parent_tool_use_id": "toolu_1"},
        },
        {
            "seq": 2,
            "type": AgentEventType.SUBAGENT_EVENT,
            "data": {
                "parent_tool_use_id": "sub-1",
                "child_event": {"type": "text_delta", "data": {"text": "nested"}},
            },
        },
        {
            "seq": 3,
            "type": AgentEventType.SUBAGENT_STOP,
            "data": {"agent_id": "sub-1", "result_summary": "done"},
        },
    ]
    blocks = events_to_chat_blocks(events)
    assert len(blocks) == 1
    assert blocks[0]["kind"] == "subagent"
    assert blocks[0]["status"] == "completed"
    assert blocks[0]["events"][0]["data"]["text"] == "nested"


def test_events_to_chat_blocks_plan_progress() -> None:
    events = [
        {
            "type": AgentEventType.TASK_PROGRESS,
            "data": {"tasks": [{"id": "1", "title": "Step", "status": "done"}]},
        },
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks[0]["kind"] == "plan"
    assert blocks[0]["tasks"][0]["status"] == "done"


def test_events_to_chat_blocks_user_and_assistant_timestamps() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "hi"}, "created_at": "2026-09-29T10:00:00+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "He"}, "created_at": "2026-09-29T10:00:02+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "llo"}, "created_at": "2026-09-29T10:00:05+00:00"},
        {"type": AgentEventType.DONE, "data": {"reason": "completed"}, "created_at": "2026-09-29T10:00:06+00:00"},
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks[0]["created_at"] == "2026-09-29T10:00:00+00:00"
    assert blocks[1]["created_at"] == "2026-09-29T10:00:05+00:00"
    assert blocks[1]["turn_ms"] == 3000


def test_events_to_chat_blocks_thinking_starts_turn_duration() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "go"}, "created_at": "2026-09-29T10:00:00+00:00"},
        {"type": AgentEventType.THINKING_DELTA, "data": {"text": "hmm"}, "created_at": "2026-09-29T10:00:01+00:00"},
        {"type": AgentEventType.THINKING_COMPLETE, "data": {"duration_ms": 1000}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Done"}, "created_at": "2026-09-29T10:00:04+00:00"},
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks[2]["created_at"] == "2026-09-29T10:00:04+00:00"
    assert blocks[2]["turn_ms"] == 3000


def test_events_to_chat_blocks_without_timestamps_omit_fields() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "hi"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Hello"}},
        {"type": AgentEventType.DONE, "data": {}},
    ]
    blocks = events_to_chat_blocks(events)
    assert "created_at" not in blocks[0]
    assert "created_at" not in blocks[1]
    assert "turn_ms" not in blocks[1]


def test_events_to_chat_blocks_turn_duration_resets_per_turn() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "one"}, "created_at": "2026-09-29T10:00:00+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "A"}, "created_at": "2026-09-29T10:00:02+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "B"}, "created_at": "2026-09-29T10:00:05+00:00"},
        {"type": AgentEventType.DONE, "data": {}, "created_at": "2026-09-29T10:00:06+00:00"},
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "two"}, "created_at": "2026-09-29T11:00:00+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "C"}, "created_at": "2026-09-29T11:00:01+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "D"}, "created_at": "2026-09-29T11:00:03+00:00"},
    ]
    blocks = events_to_chat_blocks(events)
    assistant = [b for b in blocks if b["kind"] == "assistant_markdown"]
    assert assistant[0]["turn_ms"] == 3000
    assert assistant[1]["turn_ms"] == 2000


def test_events_to_chat_blocks_multi_segment_assistant_timestamps() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "go"}, "created_at": "2026-09-29T10:00:00+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Before "}, "created_at": "2026-09-29T10:00:01+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "tool"}, "created_at": "2026-09-29T10:00:02+00:00"},
        {"type": AgentEventType.TOOL_CALL, "data": {"id": "tc_1", "name": "Read", "input": {}}, "created_at": "2026-09-29T10:00:03+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "After"}, "created_at": "2026-09-29T10:00:04+00:00"},
    ]
    blocks = events_to_chat_blocks(events)
    first = blocks[1]
    second = blocks[3]
    assert first["created_at"] == "2026-09-29T10:00:02+00:00"
    assert first["turn_ms"] == 1000
    assert second["created_at"] == "2026-09-29T10:00:04+00:00"
    assert second["turn_ms"] == 3000


def test_events_to_chat_blocks_timestamp_falls_back_to_at() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "hi"}, "at": "2026-09-29T10:00:00+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "He"}, "at": "2026-09-29T10:00:01+00:00"},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "llo"}, "at": "2026-09-29T10:00:03+00:00"},
    ]
    blocks = events_to_chat_blocks(events)
    assert blocks[0]["created_at"] == "2026-09-29T10:00:00+00:00"
    assert blocks[1]["created_at"] == "2026-09-29T10:00:03+00:00"
    assert blocks[1]["turn_ms"] == 2000


def test_event_public_includes_created_at() -> None:
    from datetime import UTC, datetime

    from prodavan.application.agent.session_service import _event_public

    class _Row:
        seq = 3
        event_type = "user_message"
        payload = {"text": "hi"}
        at = None
        created_at = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)

    public = _event_public(_Row())  # type: ignore[arg-type]
    assert public["created_at"] == "2026-09-29T10:00:00+00:00"


def test_transcript_entries_to_chat_blocks_maps_roles() -> None:
    """Sidechain transcript entries (Anthropic-shaped) → chat blocks for the UI."""
    from prodavan.application.agent.chat_projection import (
        transcript_entries_to_chat_blocks,
    )

    entries = [
        {"role": "user", "content": "найди аналоги"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Ищу"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a"}},
            ],
        },
        {"role": "tool", "content": "ok", "meta": {"tool_call_id": "t1", "name": "Read"}},
    ]
    blocks = transcript_entries_to_chat_blocks(entries)
    assert blocks[0] == {"kind": "user", "text": "найди аналоги"}
    assert blocks[1] == {"kind": "assistant_markdown", "text": "Ищу"}
    assert blocks[2]["kind"] == "tool_call"
    assert blocks[2]["name"] == "Read"
    assert blocks[3]["kind"] == "tool_result"
    assert blocks[3]["id"] == "t1"


def test_transcript_entries_to_chat_blocks_tolerates_junk() -> None:
    from prodavan.application.agent.chat_projection import (
        transcript_entries_to_chat_blocks,
    )

    assert transcript_entries_to_chat_blocks(None) == []
    assert transcript_entries_to_chat_blocks("nope") == []
    assert transcript_entries_to_chat_blocks([1, "x", {}]) == []
