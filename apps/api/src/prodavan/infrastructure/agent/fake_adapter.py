"""Fake agent adapter for contract tests (L08)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from prodavan.domain.agent import AgentEvent, AgentEventType, AgentHandle, ChatMessage, CreateOpts
from prodavan.domain.agent.port import AgentProviderPort


class FakeAgentAdapter:
    """Deterministic echo adapter — no external SDK."""

    provider = "fake"

    async def create(self, opts: CreateOpts) -> AgentHandle:
        return AgentHandle(
            id=f"fake_{uuid.uuid4().hex[:12]}",
            provider=self.provider,
            cwd=opts.cwd,
            model=opts.model,
        )

    async def resume(self, handle: AgentHandle, opts: CreateOpts) -> AgentHandle:
        return handle

    async def send(self, handle: AgentHandle, message: ChatMessage) -> AsyncIterator[AgentEvent]:
        prefix = f"[{handle.provider}] "
        yield AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": prefix + message.text})
        yield AgentEvent.now(
            AgentEventType.USAGE,
            {
                "input_tokens": max(1, len(message.text.split())),
                "output_tokens": max(1, len(message.text)),
                "provider": handle.provider,
                "model": handle.model,
            },
        )
        yield AgentEvent.now(AgentEventType.DONE, {"reason": "completed"})

    async def cancel(self, handle: AgentHandle) -> None:
        return None

    async def close(self, handle: AgentHandle) -> None:
        return None


def as_port(adapter: FakeAgentAdapter) -> AgentProviderPort:
    return adapter
