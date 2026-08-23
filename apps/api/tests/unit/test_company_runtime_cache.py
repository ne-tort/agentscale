"""Unit tests — company runtime Redis cache helpers (C-CACHE)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from prodavan.application.admin.company_runtime_cache import (
    agent_policy_cache_key,
    get_cached_agent_policy,
    invalidate_company_runtime_cache,
    policy_from_cache_dict,
    policy_to_cache_dict,
    quota_cache_key,
    quota_from_cache_dict,
    quota_to_cache_dict,
    refresh_subscription_cached_state,
    set_cached_agent_policy,
    subscription_cache_key,
)
from prodavan.domain.admin import CompanyAgentRuntimePolicy, CompanyCabinetQuota


def test_policy_cache_roundtrip_omits_hmac_secrets() -> None:
    policy = CompanyAgentRuntimePolicy(
        tool_preset="workspace_dev",
        preferred_provider="openai",
        platform_fallback=False,
        model_allowlist=["gpt-4o"],
        max_agent_tokens_month=1000,
        max_tokens_per_run=200,
        max_cost_usd_month=Decimal("12.50"),
        max_attachment_mb=10,
        webhook_hmac_secret="super-secret",
        telegram_hmac_secret="tg-secret",
        idle_pause_after_hours=24,
    )
    cached = policy_to_cache_dict(policy)
    assert "webhook_hmac_secret" not in cached
    assert "telegram_hmac_secret" not in cached
    restored = policy_from_cache_dict(cached)
    assert restored.webhook_hmac_secret is None
    assert restored.telegram_hmac_secret is None
    assert restored.max_cost_usd_month == Decimal("12.50")
    assert restored.idle_pause_after_hours == 24


def test_quota_cache_roundtrip() -> None:
    quota = CompanyCabinetQuota(max_cabinets=3, max_packages_per_cabinet=7, max_bundle_import_mb=15)
    restored = quota_from_cache_dict(quota_to_cache_dict(quota))
    assert restored == quota


def test_refresh_subscription_recomputes_expired() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    refreshed = refresh_subscription_cached_state(
        {
            "subscription_ends_at": past,
            "subscription_lifetime": False,
            "subscription_expired": False,
            "subscription_expiring_soon": False,
        }
    )
    assert refreshed["subscription_expired"] is True


def test_cache_keys_stable() -> None:
    assert agent_policy_cache_key("co_1") == "prodavan:company:co_1:agent_policy"
    assert subscription_cache_key("co_1") == "prodavan:company:co_1:subscription"
    assert quota_cache_key("co_1") == "prodavan:company:co_1:quota"


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

    policy = CompanyAgentRuntimePolicy(preferred_provider="anthropic", webhook_hmac_secret="x")
    await set_cached_agent_policy("co_x", policy)
    raw = store[agent_policy_cache_key("co_x")]
    assert "webhook_hmac_secret" not in raw
    cached = await get_cached_agent_policy("co_x")
    assert cached is not None
    assert cached.preferred_provider == "anthropic"
    assert cached.webhook_hmac_secret is None
    await invalidate_company_runtime_cache("co_x")
    assert await get_cached_agent_policy("co_x") is None
