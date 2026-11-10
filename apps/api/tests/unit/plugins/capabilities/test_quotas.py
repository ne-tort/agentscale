"""Unit-тесты exact-grant квот capability-запросов плагинов.

Тесты написаны во время verification freeze и намеренно не запускаются.
InMemoryQuota — только deterministic unit double, не live acceptance evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.plugins.capabilities.contracts import TypedContractError
from prodavan.application.plugins.capabilities.quotas import (
    QUOTA_FIELDS,
    InMemoryQuota,
    PluginQuota,
    PluginQuotaKey,
    QuotaOp,
    QuotaReason,
    QuotaUsage,
    quota_key_from_claims,
)

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)
LIMITS = PluginQuota()
pytestmark = pytest.mark.asyncio


def key(
    *, company: str = "co_123", instance: str = "inst_abc", digest: str = DIGEST
) -> PluginQuotaKey:
    return quota_key_from_claims(
        company_id=company, plugin_instance_id=instance, artifact_digest=digest
    )


async def test_quota_key_requires_full_triple_and_has_no_project_id() -> None:
    import dataclasses

    fields = {item.name for item in dataclasses.fields(PluginQuotaKey)}
    assert fields == {"company_id", "plugin_instance_id", "artifact_digest"}
    assert "project_id" not in fields
    assert key().cache_key().startswith("rl:plugin:co_123:inst_abc:sha256:")


async def test_quota_key_rejects_missing_or_invalid_artifact_digest() -> None:
    with pytest.raises(TypedContractError):
        PluginQuotaKey(company_id="co_123", plugin_instance_id="", artifact_digest=DIGEST)
    with pytest.raises(TypedContractError):
        PluginQuotaKey(company_id="co_123", plugin_instance_id="inst_abc", artifact_digest="")
    with pytest.raises(TypedContractError):
        PluginQuotaKey(company_id="co_123", plugin_instance_id="inst_abc", artifact_digest="md5:abc")


async def test_two_instances_and_digests_have_independent_counters() -> None:
    quota = InMemoryQuota(now=NOW)
    limits = PluginQuota(ops_per_minute=1)
    first = key(instance="inst_a")
    second = key(instance="inst_b")
    new_digest = key(instance="inst_a", digest=OTHER_DIGEST)

    assert (await quota.enforce(first, QuotaOp.OPS, 1, limits)).allowed is True
    assert (await quota.enforce(second, QuotaOp.OPS, 1, limits)).allowed is True
    assert (await quota.enforce(new_digest, QuotaOp.OPS, 1, limits)).allowed is True
    assert (await quota.enforce(first, QuotaOp.OPS, 1, limits)).code is QuotaReason.QUOTA_EXCEEDED
    assert quota.usage(second, QuotaOp.OPS) == 1
    assert quota.usage(new_digest, QuotaOp.OPS) == 1


@pytest.mark.parametrize("op", list(QuotaOp))
async def test_every_quota_dimension_is_per_instance_and_digest(op: QuotaOp) -> None:
    quota = InMemoryQuota(now=NOW)
    limits = PluginQuota(
        ops_per_minute=1,
        max_rows=1,
        max_bytes=1,
        max_keys=1,
        max_object_bytes=1,
        max_egress_bytes=1,
    )
    first = key(instance="inst_a")
    second = key(instance="inst_b")
    other_digest = key(instance="inst_a", digest=OTHER_DIGEST)

    assert (await quota.enforce(first, op, 1, limits)).allowed is True
    assert (await quota.enforce(second, op, 1, limits)).allowed is True
    assert (await quota.enforce(other_digest, op, 1, limits)).allowed is True
    denied = await quota.enforce(first, op, 1, limits)
    assert denied.code is QuotaReason.QUOTA_EXCEEDED
    assert denied.http_status == 429
    assert denied.limit == 1


async def test_different_companies_are_isolated() -> None:
    quota = InMemoryQuota(now=NOW)
    limits = PluginQuota(ops_per_minute=1)
    assert (await quota.enforce(key(company="co_A"), QuotaOp.OPS, 1, limits)).allowed is True
    assert (await quota.enforce(key(company="co_B"), QuotaOp.OPS, 1, limits)).allowed is True


async def test_limits_come_from_each_invocation_not_quota_constructor() -> None:
    quota = InMemoryQuota(now=NOW)
    ident = key()
    admin_one = PluginQuota(ops_per_minute=1)
    other_grant = PluginQuota(ops_per_minute=3)

    assert (await quota.enforce(ident, QuotaOp.OPS, 1, admin_one)).allowed is True
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, admin_one)).allowed is False
    # Тот же storage и key, но новый invocation с другим durable grant не
    # наследует прежний policy object; решение использует переданные лимиты.
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, other_grant)).allowed is True


async def test_denied_enforcement_does_not_consume_the_limit() -> None:
    quota = InMemoryQuota(now=NOW)
    limits = PluginQuota(ops_per_minute=1)
    ident = key()
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, limits)).allowed is True
    assert (await quota.enforce(ident, QuotaOp.OPS, 99, limits)).allowed is False
    assert quota.usage(ident, QuotaOp.OPS) == 1


async def test_sliding_window_expires_after_60_seconds() -> None:
    quota = InMemoryQuota(now=NOW)
    limits = PluginQuota(ops_per_minute=1)
    ident = key()
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, limits)).allowed is True
    quota._now = NOW + timedelta(seconds=1)
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, limits)).allowed is False
    quota._now = NOW + timedelta(seconds=61)
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, limits)).allowed is True


async def test_invalid_amount_and_missing_limits_fail_closed() -> None:
    quota = InMemoryQuota(now=NOW)
    negative = await quota.enforce(key(), QuotaOp.OPS, -1, LIMITS)
    assert negative.allowed is False
    assert negative.http_status == 503
    missing = await quota.enforce(key(), QuotaOp.OPS, 1, None)  # type: ignore[arg-type]
    assert missing.allowed is False
    assert missing.code is QuotaReason.QUOTA_KEY_INVALID


async def test_quota_values_and_grant_shape_must_be_complete_and_positive() -> None:
    with pytest.raises(TypedContractError):
        PluginQuota(ops_per_minute=0)
    with pytest.raises(TypedContractError):
        PluginQuota(max_bytes=-1)
    with pytest.raises(TypedContractError):
        PluginQuota.from_grant(None)
    with pytest.raises(TypedContractError):
        PluginQuota.from_grant({"ops_per_minute": 1})
    raw = {name: 1 for name in QUOTA_FIELDS}
    raw["max_rows"] = True
    with pytest.raises(TypedContractError):
        PluginQuota.from_grant(raw)


async def test_grant_mapping_is_read_without_coercion_or_defaults() -> None:
    raw = {name: 1 for name in QUOTA_FIELDS}
    raw["ops_per_minute"] = 7
    limits = PluginQuota.from_grant(raw)
    assert limits.ops_per_minute == 7
    assert limits.max_rows == 1


async def test_typed_usage_covers_all_six_units_without_implicit_defaults() -> None:
    usage = QuotaUsage(
        ops=1, rows=2, bytes=3, keys=4, object_bytes=5, egress_bytes=6
    )
    assert [usage.amount_for(op) for op in QuotaOp] == [1, 2, 3, 4, 5, 6]
    with pytest.raises(TypedContractError):
        QuotaUsage(
            ops=1, rows=0, bytes=-1, keys=0, object_bytes=0, egress_bytes=0
        )
