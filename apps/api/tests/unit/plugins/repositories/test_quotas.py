"""``RedisPluginQuota``: per-instance/digest изоляция и deny при недоступности Redis.

acceptance №10 (429 + QUOTA_EXCEEDED), №21 (один плагин не исчерпывает квоту
компании). Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

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


def key(*, company: str = "co_123", instance: str = "inst_abc", digest: str = DIGEST) -> PluginQuotaKey:
    return PluginQuotaKey(
        company_id=company, plugin_instance_id=instance, artifact_digest=digest
    )


def _quota(*, available: bool = True, ops: int = 1) -> RedisPluginQuota:
    return RedisPluginQuota(
        FakeRedis(available=available),
        quota=PluginQuota(ops_per_minute=ops),
        clock=lambda: NOW,
    )


def test_redis_quota_implements_quota_port() -> None:
    quota = _quota()
    assert isinstance(quota, QuotaPort)
    assert WINDOW_SECONDS == 60


def test_cache_key_uses_full_triple_not_company_only() -> None:
    """Ключ = ``PluginQuotaKey.cache_key()`` (вся тройка, без project_id)."""
    quota = _quota()
    ident = key()
    assert ident.cache_key() == f"rl:plugin:co_123:inst_abc:{DIGEST}"
    assert quota._cache_key(ident, QuotaOp.OPS).startswith(ident.cache_key())
    assert quota._cache_key(ident, QuotaOp.BYTES).startswith(ident.cache_key())
    # Разные операции не смешивают счётчики.
    assert quota._cache_key(ident, QuotaOp.OPS) != quota._cache_key(ident, QuotaOp.BYTES)


def test_quota_is_per_instance_not_per_company() -> None:
    """Кр. 21: два инстанса одной компании имеют НЕзависимые счётчики."""
    quota = _quota(ops=1)
    first, second = key(instance="inst_a"), key(instance="inst_b")
    assert quota.enforce(first, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(second, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(first, QuotaOp.OPS, 1).code is QuotaReason.QUOTA_EXCEEDED
    # Тот же digest, тот же company, но другой instance — всё ещё доступен.
    assert quota.enforce(second, QuotaOp.OPS, 0).allowed is True


def test_quota_is_per_digest_too() -> None:
    """Смена артефакта начинает новый отсчёт (не наследует лимит)."""
    quota = _quota(ops=1)
    old, new = key(digest=DIGEST), key(digest=OTHER_DIGEST)
    assert quota.enforce(old, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(new, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(old, QuotaOp.OPS, 1).code is QuotaReason.QUOTA_EXCEEDED


def test_exceeded_is_429_and_does_not_consume_limit() -> None:
    quota = _quota(ops=1)
    assert quota.enforce(key(), QuotaOp.OPS, 1).allowed is True
    decision: QuotaDecision = quota.enforce(key(), QuotaOp.OPS, 1)
    assert decision.allowed is False
    assert decision.code is QuotaReason.QUOTA_EXCEEDED
    assert decision.http_status == 429
    assert decision.used == 1
    assert decision.remaining == 0
    assert decision.retry_after_seconds is not None


def test_hard_counters_are_also_per_instance() -> None:
    quota = RedisPluginQuota(
        FakeRedis(), quota=PluginQuota(max_bytes=5), clock=lambda: NOW
    )
    first, second = key(instance="inst_a"), key(instance="inst_b")
    assert quota.enforce(first, QuotaOp.BYTES, 5).allowed is True
    assert quota.enforce(second, QuotaOp.BYTES, 5).allowed is True
    assert quota.enforce(first, QuotaOp.BYTES, 1).code is QuotaReason.QUOTA_EXCEEDED


def test_redis_unavailable_denies_with_quota_exceeded() -> None:
    """ГЛАВНОЕ: недоступность Redis → deny/QUOTA_EXCEEDED, НЕ «разрешить»."""
    quota = _quota(available=False)
    decision = quota.enforce(key(), QuotaOp.OPS, 1)
    assert isinstance(decision, QuotaDecision)
    assert decision.allowed is False
    assert decision.code is QuotaReason.QUOTA_EXCEEDED
    assert decision.http_status == 429
    assert "unavailable" in (decision.reason or "")


def test_redis_unavailable_denies_for_every_op() -> None:
    quota = _quota(available=False)
    for op in QuotaOp:
        assert quota.enforce(key(), op, 1).allowed is False


def test_broken_redis_reply_denies() -> None:
    """Неожиданный ответ Lua/исключение eval → deny, не allow."""

    class BrokenRedis:
        def eval(self, *args: object, **kwargs: object) -> object:
            return "unexpected"

    quota = RedisPluginQuota(BrokenRedis(), clock=lambda: NOW)
    assert quota.enforce(key(), QuotaOp.OPS, 1).allowed is False


def test_missing_client_denies() -> None:
    class DeadManager:
        @property
        def client(self) -> None:
            return None

    quota = RedisPluginQuota(DeadManager(), clock=lambda: NOW)
    assert quota.enforce(key(), QuotaOp.OPS, 1).allowed is False


def test_constructor_rejects_missing_redis() -> None:
    with pytest.raises(ValueError):
        RedisPluginQuota(None)  # type: ignore[arg-type]


def test_invalid_amount_fails_closed() -> None:
    quota = _quota()
    decision = quota.enforce(key(), QuotaOp.OPS, -1)
    assert decision.allowed is False
    assert decision.code is QuotaReason.QUOTA_KEY_INVALID


@pytest.mark.asyncio
async def test_enforce_async_unavailable_denies() -> None:
    """Async-вариант сохраняет ту же fail-closed семантику."""
    quota = _quota(available=False)
    decision = await quota.enforce_async(key(), QuotaOp.OPS, 1)
    assert decision.allowed is False
    assert decision.code is QuotaReason.QUOTA_EXCEEDED
