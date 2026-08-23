"""Adapter registry — api_kind → AgentProviderPort (L08)."""

from __future__ import annotations

from prodavan.domain.agent.port import AgentProviderPort
from prodavan.domain.ai_keys import ApiKind
from prodavan.domain.errors import AppError
from prodavan.infrastructure.agent.fake_adapter import FakeAgentAdapter
from prodavan.infrastructure.agent.fixture_cursor_adapter import FixtureCursorAdapter


def get_agent_adapter(*, api_kind: str, force_fake: bool = False) -> AgentProviderPort:
    if force_fake:
        return FakeAgentAdapter()
    if api_kind == ApiKind.CURSOR_SDK:
        return FixtureCursorAdapter()
    if api_kind in {ApiKind.CODEX_SDK, ApiKind.CLAUDE_AGENT_SDK}:
        # Stub: same fake with provider label via CreateOpts.provider at session layer
        return FakeAgentAdapter()
    raise AppError(
        code="AGENT_ADAPTER",
        title="Adapter not available",
        status=501,
        detail=f"no adapter for api_kind={api_kind}",
    )
