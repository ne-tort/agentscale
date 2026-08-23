"""AgentProviderPort — coding agent backends (L08)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from prodavan.domain.agent import AgentEvent, AgentHandle, ChatMessage, CreateOpts


class AgentProviderPort(Protocol):
    async def create(self, opts: CreateOpts) -> AgentHandle: ...

    async def resume(self, handle: AgentHandle, opts: CreateOpts) -> AgentHandle: ...

    async def send(self, handle: AgentHandle, message: ChatMessage) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, handle: AgentHandle) -> None: ...

    async def close(self, handle: AgentHandle) -> None: ...
