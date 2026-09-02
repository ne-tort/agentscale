"""Fixture Cursor adapter — simulates CursorSdkAdapter without Node sidecar (L08)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from prodavan.domain.agent import AgentEvent, AgentEventType, AgentHandle, ChatMessage, CreateOpts


class FixtureCursorAdapter:
    """Maps to cursor_sdk api_kind; emits normalized AgentEvent stream."""

    provider = "cursor"

    async def create(self, opts: CreateOpts) -> AgentHandle:
        if opts.api_kind != "cursor_sdk":
            raise ValueError("FixtureCursorAdapter requires cursor_sdk api_kind")
        return AgentHandle(
            id=f"cursor_{uuid.uuid4().hex[:12]}",
            provider=self.provider,
            cwd=opts.cwd,
            model=opts.model or "composer-fixture",
        )

    async def resume(self, handle: AgentHandle, opts: CreateOpts) -> AgentHandle:
        return handle

    async def send(self, handle: AgentHandle, message: ChatMessage) -> AsyncIterator[AgentEvent]:
        # HITL demo path: messages with "dangerous:" pause on tool_approval_request (no done).
        if "dangerous:" in message.text.lower():
            yield AgentEvent.now(
                AgentEventType.TEXT_DELTA,
                {"text": "Waiting for approval to run a dangerous tool."},
            )
            yield AgentEvent.now(
                AgentEventType.TOOL_APPROVAL_REQUEST,
                {
                    "id": f"apr_{uuid.uuid4().hex[:10]}",
                    "name": "shell.exec",
                    "input": {"command": message.text},
                },
            )
            return

        if message.text.startswith("cumulative:"):
            payload = message.text.removeprefix("cumulative:").strip() or "OK"
            for i in range(1, len(payload) + 1):
                yield AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": payload[:i]})
            yield AgentEvent.now(AgentEventType.DONE, {"reason": "completed"})
            return

        reply = f"Cursor fixture: {message.text}"
        for i, word in enumerate(reply.split()):
            chunk = word if i == 0 else f" {word}"
            yield AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": chunk})
        if message.attachment_refs:
            yield AgentEvent.now(
                AgentEventType.TOOL_CALL,
                {"id": "tc_1", "name": "mcp.cabinet.info", "input": {"refs": list(message.attachment_refs)}},
            )
            yield AgentEvent.now(
                AgentEventType.TOOL_RESULT,
                {"id": "tc_1", "name": "mcp.cabinet.info", "output": {"ok": True}, "is_error": False},
            )
        yield AgentEvent.now(
            AgentEventType.USAGE,
            {
                "input_tokens": 42,
                "output_tokens": 17,
                "cost_usd": 0.05,
                "provider": "cursor",
                "model": handle.model,
            },
        )
        yield AgentEvent.now(AgentEventType.DONE, {"reason": "completed"})

    async def cancel(self, handle: AgentHandle) -> None:
        return None

    async def close(self, handle: AgentHandle) -> None:
        return None
