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
