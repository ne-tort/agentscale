"""Unit tests — company runtime Redis cache helpers (C-CACHE)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from prodavan.application.admin.company_runtime_cache import (
    agent_policy_cache_key,
    get_cached_agent_policy,
    invalidate_company_runtime_cache,
    policy_from_cache_dict,
    policy_to_cache_dict,
    set_cached_agent_policy,
    subscription_cache_key,
)
from prodavan.domain.admin import CompanyAgentRuntimePolicy


def test_policy_cache_roundtrip() -> None:
    policy = CompanyAgentRuntimePolicy(
        tool_preset="workspace_dev",
        preferred_provider="openai",
        platform_fallback=False,
        model_allowlist=["gpt-4o"],
        max_agent_tokens_month=1000,
        max_tokens_per_run=200,
        max_cost_usd_month=Decimal("12.50"),
        max_attachment_mb=10,
        idle_pause_after_hours=24,
    )
    restored = policy_from_cache_dict(policy_to_cache_dict(policy))
    assert restored.tool_preset == policy.tool_preset
    assert restored.preferred_provider == policy.preferred_provider
    assert restored.platform_fallback is False
    assert restored.model_allowlist == ["gpt-4o"]
    assert restored.max_cost_usd_month == Decimal("12.50")
    assert restored.idle_pause_after_hours == 24


def test_cache_keys_stable() -> None:
    assert agent_policy_cache_key("co_1") == "prodavan:company:co_1:agent_policy"
    assert subscription_cache_key("co_1") == "prodavan:company:co_1:subscription"


@pytest.mark.asyncio
async def test_get_set_invalidate_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[str, str] = {}

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str, *, ttl_sec: int | None = 300) -> bool:
        store[key] = value
        return True

    async def _delete(key: str) -> bool:
        store.pop(key, None)
        return True

    monkeypatch.setattr(
        "prodavan.application.admin.company_runtime_cache.cache_get",
        _get,
    )
    monkeypatch.setattr(
        "prodavan.application.admin.company_runtime_cache.cache_set",
        _set,
    )
    monkeypatch.setattr(
        "prodavan.application.admin.company_runtime_cache.cache_delete",
        _delete,
    )

    policy = CompanyAgentRuntimePolicy(preferred_provider="anthropic")
    await set_cached_agent_policy("co_x", policy)
    cached = await get_cached_agent_policy("co_x")
    assert cached is not None
    assert cached.preferred_provider == "anthropic"
    await invalidate_company_runtime_cache("co_x")
    assert await get_cached_agent_policy("co_x") is None
