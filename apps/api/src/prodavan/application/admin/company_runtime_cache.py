"""Company runtime Redis cache (C-CACHE) — agent policy + subscription + quota peek."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_delete, cache_get, cache_key, cache_set
from prodavan.domain.admin import (
    DEFAULT_CABINET_QUOTA,
    CompanyAgentRuntimePolicy,
    CompanyCabinetQuota,
    subscription_read_model,
)

_POLICY_TTL = 120
_SUB_TTL = 60
_QUOTA_TTL = 120


def agent_policy_cache_key(company_id: str) -> str:
    return cache_key("company", company_id, "agent_policy")


def subscription_cache_key(company_id: str) -> str:
    return cache_key("company", company_id, "subscription")


def quota_cache_key(company_id: str) -> str:
    return cache_key("company", company_id, "quota")


def policy_to_cache_dict(policy: CompanyAgentRuntimePolicy) -> dict[str, Any]:
    """Serialize policy for Redis — never store HMAC secrets."""
    return {
        "tool_preset": policy.tool_preset,
        "preferred_provider": policy.preferred_provider,
        "platform_fallback": policy.platform_fallback,
        "model_allowlist": list(policy.model_allowlist or []),
        "max_agent_tokens_month": policy.max_agent_tokens_month,
        "max_tokens_per_run": policy.max_tokens_per_run,
        "max_cost_usd_month": str(policy.max_cost_usd_month) if policy.max_cost_usd_month is not None else None,
        "max_attachment_mb": policy.max_attachment_mb,
        "idle_pause_after_hours": policy.idle_pause_after_hours,
    }


def policy_from_cache_dict(data: dict[str, Any]) -> CompanyAgentRuntimePolicy:
    cost = data.get("max_cost_usd_month")
    return CompanyAgentRuntimePolicy(
        tool_preset=str(data.get("tool_preset") or "workspace_dev"),
        preferred_provider=data.get("preferred_provider"),
        platform_fallback=bool(data.get("platform_fallback", True)),
        model_allowlist=list(data.get("model_allowlist") or []),
        max_agent_tokens_month=data.get("max_agent_tokens_month"),
        max_tokens_per_run=data.get("max_tokens_per_run"),
        max_cost_usd_month=Decimal(cost) if cost not in (None, "") else None,
        max_attachment_mb=int(data.get("max_attachment_mb") or 20),
        webhook_hmac_secret=None,
        telegram_hmac_secret=None,
        idle_pause_after_hours=data.get("idle_pause_after_hours"),
    )


def quota_to_cache_dict(quota: CompanyCabinetQuota) -> dict[str, Any]:
    return {
        "max_cabinets": quota.max_cabinets,
        "max_packages_per_cabinet": quota.max_packages_per_cabinet,
        "max_bundle_import_mb": quota.max_bundle_import_mb,
    }


def quota_from_cache_dict(data: dict[str, Any]) -> CompanyCabinetQuota:
    return CompanyCabinetQuota(
        max_cabinets=int(data.get("max_cabinets") or DEFAULT_CABINET_QUOTA.max_cabinets),
        max_packages_per_cabinet=int(
            data.get("max_packages_per_cabinet")
            if data.get("max_packages_per_cabinet") is not None
            else DEFAULT_CABINET_QUOTA.max_packages_per_cabinet
        ),
        max_bundle_import_mb=int(data.get("max_bundle_import_mb") or DEFAULT_CABINET_QUOTA.max_bundle_import_mb),
    )


def refresh_subscription_cached_state(cached: dict[str, Any]) -> dict[str, object]:
    """Recompute expired/expiring flags from cached ends_at (avoid stale TTL window)."""
    lifetime = bool(cached.get("subscription_lifetime"))
    ends_raw = cached.get("subscription_ends_at")
    ends_at: datetime | None = None
    if ends_raw:
        try:
            ends_at = datetime.fromisoformat(str(ends_raw))
        except ValueError:
            ends_at = None
    return subscription_read_model(
        ends_at=ends_at,
        lifetime=lifetime,
        now=datetime.now(UTC),
        expiring_days=settings.admin_metrics_subscription_expiring_days,
    )


async def get_cached_agent_policy(company_id: str) -> CompanyAgentRuntimePolicy | None:
    raw = await cache_get(agent_policy_cache_key(company_id))
    if not raw:
        return None
    try:
        return policy_from_cache_dict(json.loads(raw))
    except Exception:
        return None


async def set_cached_agent_policy(company_id: str, policy: CompanyAgentRuntimePolicy) -> None:
    await cache_set(
        agent_policy_cache_key(company_id),
        json.dumps(policy_to_cache_dict(policy), ensure_ascii=False),
        ttl_sec=_POLICY_TTL,
    )


async def get_cached_subscription(company_id: str) -> dict[str, Any] | None:
    raw = await cache_get(subscription_cache_key(company_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def set_cached_subscription(company_id: str, state: dict[str, Any]) -> None:
    await cache_set(
        subscription_cache_key(company_id),
        json.dumps(state, ensure_ascii=False, default=str),
        ttl_sec=_SUB_TTL,
    )


async def get_cached_quota(company_id: str) -> CompanyCabinetQuota | None:
    raw = await cache_get(quota_cache_key(company_id))
    if not raw:
        return None
    try:
        return quota_from_cache_dict(json.loads(raw))
    except Exception:
        return None


async def set_cached_quota(company_id: str, quota: CompanyCabinetQuota) -> None:
    await cache_set(
        quota_cache_key(company_id),
        json.dumps(quota_to_cache_dict(quota), ensure_ascii=False),
        ttl_sec=_QUOTA_TTL,
    )


async def invalidate_company_runtime_cache(company_id: str) -> None:
    await cache_delete(agent_policy_cache_key(company_id))
    await cache_delete(subscription_cache_key(company_id))
    await cache_delete(quota_cache_key(company_id))
