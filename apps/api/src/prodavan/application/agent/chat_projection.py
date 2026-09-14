"""Chat projection — agent events → transcript messages / UI blocks."""

from __future__ import annotations

from prodavan.application.agent.text_delta import normalize_text_delta
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentEventType


def assistant_text_from_events(events: list[dict]) -> str:
    """Join assistant text deltas (incremental or legacy cumulative) into one string."""
    cumulative = ""
    for event in events:
        if event.get("type") != AgentEventType.TEXT_DELTA:
            continue
        data = event.get("data") or {}
        chunk = data.get("text")
        if chunk:
            _, cumulative = normalize_text_delta(cumulative, str(chunk))
    return cumulative


def events_to_transcript(events: list[dict]) -> list[dict]:
    """Collapse platform user_message + text_delta turns into chat bubbles."""
    messages: list[dict] = []
    assistant_cumulative = ""

    def flush_assistant() -> None:
        nonlocal assistant_cumulative
        if not assistant_cumulative:
            return
        messages.append({"role": "assistant", "text": assistant_cumulative})
        assistant_cumulative = ""

    for event in events:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == PLATFORM_EVENT_USER_MESSAGE:
            flush_assistant()
            text = data.get("text")
            refs = data.get("attachment_refs") or []
            attachments = data.get("attachments") or []
            if text or refs or attachments:
                bubble: dict = {"role": "user", "text": str(text or "")}
                if isinstance(refs, list) and refs:
                    bubble["attachment_refs"] = [str(r) for r in refs]
                if isinstance(attachments, list) and attachments:
                    bubble["attachments"] = attachments
                messages.append(bubble)
        elif etype == AgentEventType.TOOL_CALL:
            flush_assistant()
            name = data.get("name")
            if name:
                messages.append({"role": "tool", "text": str(name)})
        elif etype == AgentEventType.TOOL_APPROVAL_REQUEST:
            flush_assistant()
            name = data.get("name") or "tool"
            approval_id = data.get("id") or ""
            messages.append(
                {
                    "role": "approval",
                    "text": f"Approve {name}?",
                    "approval_id": approval_id,
                    "tool_name": name,
                    "input": data.get("input") or {},
                }
            )
        elif etype == AgentEventType.TEXT_DELTA:
            chunk = data.get("text")
            if chunk:
                _, assistant_cumulative = normalize_text_delta(assistant_cumulative, str(chunk))
        elif etype in {AgentEventType.DONE, AgentEventType.ERROR}:
            flush_assistant()

    flush_assistant()
    return messages


def events_to_chat_blocks(events: list[dict]) -> list[dict]:
    """Project persisted agent events into typed chat blocks for Flutter UI."""
    blocks: list[dict] = []
    assistant_cumulative = ""
    thinking_cumulative = ""
    open_subagents: dict[str, dict] = {}

    def flush_assistant() -> None:
        nonlocal assistant_cumulative
        if not assistant_cumulative:
            return
        blocks.append({"kind": "assistant_markdown", "text": assistant_cumulative})
        assistant_cumulative = ""

    def flush_thinking(duration_ms: int | None = None) -> None:
        nonlocal thinking_cumulative
        if not thinking_cumulative:
            return
        block: dict = {"kind": "thinking", "text": thinking_cumulative}
        if duration_ms is not None:
            block["duration_ms"] = duration_ms
        blocks.append(block)
        thinking_cumulative = ""

    for event in events:
        etype = event.get("type")
        data = event.get("data") or {}
        parent_id = data.get("parent_tool_use_id") or event.get("parent_tool_use_id")

        if etype == PLATFORM_EVENT_USER_MESSAGE:
            flush_assistant()
            flush_thinking()
            text = data.get("text")
            refs = data.get("attachment_refs") or []
            attachments = data.get("attachments") or []
            if text or refs or attachments:
                block: dict = {"kind": "user", "text": str(text or "")}
                if isinstance(refs, list) and refs:
                    block["attachment_refs"] = [str(r) for r in refs]
                if isinstance(attachments, list) and attachments:
                    block["attachments"] = attachments
                blocks.append(block)
            continue

        if etype == AgentEventType.TEXT_DELTA:
            chunk = data.get("text")
            if chunk:
                _, assistant_cumulative = normalize_text_delta(assistant_cumulative, str(chunk))
            continue

        if etype == AgentEventType.THINKING_DELTA:
            flush_assistant()
            chunk = data.get("text")
            if chunk:
                _, thinking_cumulative = normalize_text_delta(thinking_cumulative, str(chunk))
            continue

        if etype == AgentEventType.THINKING_COMPLETE:
            flush_assistant()
            duration = data.get("duration_ms")
            flush_thinking(int(duration) if isinstance(duration, (int, float)) else None)
            continue

        if etype in {AgentEventType.DONE, AgentEventType.ERROR}:
            flush_assistant()
            flush_thinking()
            if etype == AgentEventType.ERROR:
                blocks.append(
                    {
                        "kind": "error",
                        "code": data.get("code"),
                        "message": data.get("message") or "Agent error",
                        "retryable": bool(data.get("retryable")),
                    }
                )
            continue

        flush_assistant()
        flush_thinking()

        if etype == AgentEventType.TOOL_CALL:
            blocks.append(
                {
                    "kind": "tool_call",
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "input": data.get("input") or {},
                    "parent_tool_use_id": parent_id or data.get("parent_tool_use_id"),
                }
            )
        elif etype == AgentEventType.TOOL_CALL_DELTA:
            blocks.append(
                {
                    "kind": "tool_call_delta",
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "partial": data.get("partial") or data.get("partial_json") or {},
                }
            )
        elif etype == AgentEventType.TOOL_RESULT:
            blocks.append(
                {
                    "kind": "tool_result",
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "output": data.get("output"),
                    "is_error": bool(data.get("is_error")),
                }
            )
        elif etype == AgentEventType.TOOL_PROGRESS:
            blocks.append(
                {
                    "kind": "tool_progress",
                    "id": data.get("id"),
                    "message": data.get("message") or "",
                }
            )
        elif etype == AgentEventType.TOOL_APPROVAL_REQUEST:
            blocks.append(
                {
                    "kind": "approval",
                    "id": data.get("id") or "",
                    "name": data.get("name") or "tool",
                    "input": data.get("input") or {},
                    "reason": data.get("reason"),
                }
            )
        elif etype == AgentEventType.SUBAGENT_START:
            sub_id = str(data.get("agent_id") or data.get("parent_tool_use_id") or parent_id or "")
            block = {
                "kind": "subagent",
                "id": sub_id,
                "agent_id": data.get("agent_id"),
                "agent_type": data.get("type") or data.get("agent_type"),
                "parent_tool_use_id": data.get("parent_tool_use_id") or parent_id,
                "status": "running",
                "events": [],
            }
            open_subagents[sub_id] = block
            blocks.append(block)
        elif etype == AgentEventType.SUBAGENT_EVENT:
            sub_id = str(data.get("parent_tool_use_id") or parent_id or "")
            child = data.get("child_event") or data.get("event")
            target = open_subagents.get(sub_id)
            if target is not None and isinstance(child, dict):
                target.setdefault("events", []).append(child)
            else:
                blocks.append(
                    {
                        "kind": "subagent_event",
                        "parent_tool_use_id": sub_id,
                        "event": child,
                    }
                )
        elif etype == AgentEventType.SUBAGENT_STOP:
            sub_id = str(data.get("agent_id") or data.get("parent_tool_use_id") or parent_id or "")
            target = open_subagents.pop(sub_id, None)
            if target is not None:
                target["status"] = "completed"
                target["result_summary"] = data.get("result_summary")
            else:
                blocks.append(
                    {
                        "kind": "subagent",
                        "id": sub_id,
                        "status": "completed",
                        "result_summary": data.get("result_summary"),
                        "events": [],
                    }
                )
        elif etype == AgentEventType.TASK_PROGRESS:
            blocks.append(
                {
                    "kind": "plan",
                    "tasks": data.get("tasks") or data.get("items") or [],
                    "message": data.get("message"),
                }
            )
        elif etype == AgentEventType.STATUS:
            blocks.append(
                {
                    "kind": "status",
                    "phase": data.get("phase"),
                    "message": data.get("message") or data.get("detail"),
                }
            )
        elif etype == AgentEventType.COMPACT_BOUNDARY:
            blocks.append({"kind": "system_notice", "reason": data.get("reason") or "compact_boundary"})
        elif etype == AgentEventType.PERMISSION_DENIAL:
            blocks.append(
                {
                    "kind": "permission_denial",
                    "tool_use_id": data.get("tool_use_id"),
                    "name": data.get("name"),
                    "reason": data.get("reason"),
                }
            )
        elif etype == AgentEventType.USAGE:
            blocks.append({"kind": "usage", **{k: v for k, v in data.items() if v is not None}})

    flush_assistant()
    flush_thinking()
    return blocks
