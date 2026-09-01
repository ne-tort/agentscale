"""Unit tests for agent adapter registry prod vs test paths."""

from __future__ import annotations

import pytest

from prodavan.application.agent.adapter_registry import get_agent_adapter
from prodavan.config.settings import settings
from prodavan.domain.ai_keys import ApiKind
from prodavan.domain.errors import AppError


def test_adapter_registry_uses_fixtures_when_inprocess_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_inprocess_adapters_enabled", True)
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", True)
    adapter = get_agent_adapter(api_kind=ApiKind.CURSOR_SDK)
    assert adapter.provider == "cursor"


def test_adapter_registry_rejects_inprocess_when_runtime_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_inprocess_adapters_enabled", False)
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", True)
    with pytest.raises(AppError) as exc:
        get_agent_adapter(api_kind=ApiKind.CURSOR_SDK)
    assert exc.value.code == "AGENT_ADAPTER_DISABLED"
