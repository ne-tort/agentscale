"""Unit tests — fake agent adapter."""

import pytest

from prodavan.domain.agent import AgentEventType, ChatMessage, CreateOpts, default_tool_policy
from prodavan.infrastructure.agent.fake_adapter import FakeAgentAdapter


@pytest.mark.asyncio
async def test_fake_adapter_stream() -> None:
    adapter = FakeAgentAdapter()
    opts = CreateOpts(
        cwd="/tmp/ws",
        model="test",
        mcp_servers={},
        api_key="sk-test",
        api_kind="cursor_sdk",
        provider="fake",
        tool_policy=default_tool_policy(),
    )
    handle = await adapter.create(opts)
    events = [e async for e in adapter.send(handle, ChatMessage(text="ping"))]
    types = [e.type for e in events]
    assert AgentEventType.TEXT_DELTA in types
    assert AgentEventType.USAGE in types
    assert types[-1] == AgentEventType.DONE
