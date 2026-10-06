"""Adapter registry — api_kind → AgentProviderPort (L08)."""

from __future__ import annotations

from prodavan.config.settings import settings
from prodavan.domain.agent.errors import agent_adapter_disabled
from prodavan.domain.agent.port import AgentProviderPort
from prodavan.domain.ai_keys import ApiKind
from prodavan.domain.errors import AppError
from prodavan.infrastructure.agent.fake_adapter import FakeAgentAdapter
from prodavan.infrastructure.agent.fixture_cursor_adapter import FixtureCursorAdapter

_PLATFORM_RUNTIME_KINDS = frozenset(
    {
        ApiKind.OPENAI_API,
        ApiKind.ANTHROPIC_API,
        ApiKind.OPENROUTER,
        ApiKind.CUSTOM,
        ApiKind.XAI_OAUTH,
    }
)


def get_agent_adapter(*, api_kind: str, force_fake: bool = False) -> AgentProviderPort:
    if force_fake or settings.agent_inprocess_adapters_enabled:
        if api_kind == ApiKind.CURSOR_SDK:
            return FixtureCursorAdapter()
        if api_kind in {ApiKind.CODEX_SDK, ApiKind.CLAUDE_AGENT_SDK}:
            return FakeAgentAdapter()
        if api_kind in _PLATFORM_RUNTIME_KINDS:
            return FakeAgentAdapter()
        raise AppError(
            code="AGENT_ADAPTER",
            title="Adapter not available",
            status=501,
            detail=f"no in-process adapter for api_kind={api_kind}",
        )
    if settings.pod_agent_runtime_enabled:
        raise agent_adapter_disabled()
    raise AppError(
        code="AGENT_ADAPTER",
        title="Adapter not available",
        status=501,
        detail=f"no adapter for api_kind={api_kind}; enable pod agent-runtime or test adapters",
    )
