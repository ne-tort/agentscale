"""RedisPluginQuota unit tests; FakeRedis is not live acceptance evidence."""
from __future__ import annotations

import pytest

from prodavan.application.plugins.capabilities.quotas import (
    PluginQuota,
    PluginQuotaKey,
    QuotaDecision,
    QuotaOp,
    QuotaPort,
    QuotaReason,
)
from prodavan.infrastructure.persistence.repositories.plugins.quotas import (
    WINDOW_SECONDS,
    RedisPluginQuota,
)

from .conftest import DIGEST, NOW, OTHER_DIGEST, FakeRedis

pytestmark = pytest.mark.asyncio
LIMITS = PluginQuota()


def key(*, company: str = "co_123", instance: str = "inst_abc", digest: str = DIGEST) -> PluginQuotaKey:
    return PluginQuotaKey(
        company_id=company, plugin_instance_id=instance, artifact_digest=digest
    )


def _quota(*, available: bool = True) -> RedisPluginQuota:
    return RedisPluginQuota(FakeRedis(available=available), clock=lambda: NOW)


def _small_limits() -> PluginQuota:
    return PluginQuota(
        ops_per_minute=1,
        max_rows=1,
        max_bytes=1,
        max_keys=1,
        max_object_bytes=1,
        max_egress_bytes=1,
    )


async def test_redis_quota_implements_quota_port() -> None:
    assert isinstance(_quota(), QuotaPort)
    assert WINDOW_SECONDS == 60


async def test_cache_key_uses_full_triple_not_company_only() -> None:
    quota = _quota()
    ident = key()
    assert ident.cache_key() == f"rl:plugin:co_123:inst_abc:{DIGEST}"
    assert quota._cache_key(ident, QuotaOp.OPS).startswith(ident.cache_key())
    assert quota._cache_key(ident, QuotaOp.BYTES).startswith(ident.cache_key())
    assert quota._cache_key(ident, QuotaOp.OPS) != quota._cache_key(ident, QuotaOp.BYTES)


@pytest.mark.parametrize("op", list(QuotaOp))
async def test_every_counter_is_per_instance_and_digest(op: QuotaOp) -> None:
    quota = _quota()
    limits = _small_limits()
    first, second = key(instance="inst_a"), key(instance="inst_b")
    other_digest = key(instance="inst_a", digest=OTHER_DIGEST)

    assert (await quota.enforce(first, op, 1, limits)).allowed is True
    assert (await quota.enforce(second, op, 1, limits)).allowed is True
    assert (await quota.enforce(other_digest, op, 1, limits)).allowed is True
    denied = await quota.enforce(first, op, 1, limits)
    assert denied.code is QuotaReason.QUOTA_EXCEEDED
    assert denied.http_status == 429
    assert denied.limit == 1


async def test_exact_invocation_limits_replace_admin60_runtime120_divergence() -> None:
    quota = _quota()
    ident = key()
    admin = PluginQuota(ops_per_minute=1)
    larger_grant = PluginQuota(ops_per_minute=120)

    assert (await quota.enforce(ident, QuotaOp.OPS, 1, admin)).allowed is True
    decision: QuotaDecision = await quota.enforce(ident, QuotaOp.OPS, 1, admin)
    assert decision.allowed is False
    assert decision.limit == 1
    assert decision.used == 1
    assert decision.retry_after_seconds is not None
    # Limits are an argument of this invocation, not mutable Redis singleton state.
    assert (await quota.enforce(ident, QuotaOp.OPS, 1, larger_grant)).allowed is True


async def test_redis_unavailable_denies_for_every_op() -> None:
    quota = _quota(available=False)
    for op in QuotaOp:
        decision = await quota.enforce(key(), op, 1, LIMITS)
        assert decision.allowed is False
        assert decision.code is QuotaReason.QUOTA_EXCEEDED
        assert decision.http_status == 429
        assert "unavailable" in (decision.reason or "")


async def test_broken_redis_reply_denies() -> None:
    class BrokenRedis:
        def eval(self, *args: object, **kwargs: object) -> object:
            return "unexpected"

    quota = RedisPluginQuota(BrokenRedis(), clock=lambda: NOW)
    assert (await quota.enforce(key(), QuotaOp.OPS, 1, LIMITS)).allowed is False


async def test_missing_client_denies() -> None:
    class DeadManager:
        @property
        def client(self) -> None:
            return None

    quota = RedisPluginQuota(DeadManager(), clock=lambda: NOW)
    assert (await quota.enforce(key(), QuotaOp.OPS, 1, LIMITS)).allowed is False


async def test_constructor_rejects_missing_redis() -> None:
    with pytest.raises(ValueError):
        RedisPluginQuota(None)  # type: ignore[arg-type]


async def test_invalid_amount_and_limits_fail_closed() -> None:
    quota = _quota()
    decision = await quota.enforce(key(), QuotaOp.OPS, -1, LIMITS)
    assert decision.allowed is False
    assert decision.code is QuotaReason.QUOTA_KEY_INVALID
    missing = await quota.enforce(key(), QuotaOp.OPS, 1, None)  # type: ignore[arg-type]
    assert missing.allowed is False
    assert missing.code is QuotaReason.QUOTA_KEY_INVALID


async def test_redis_async_success_uses_uuid_member() -> None:
    from uuid import UUID

    redis = FakeRedis()
    quota = RedisPluginQuota(redis, clock=lambda: NOW)
    decision = await quota.enforce(key(), QuotaOp.OPS, 1, LIMITS)

    assert decision.allowed is True
    assert redis.calls == 1
    assert len(redis.members) == 1
    now_ms, raw_uuid = redis.members[0].split("-", 1)
    assert now_ms.isdigit()
    assert str(UUID(raw_uuid)) == raw_uuid
