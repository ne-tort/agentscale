"""Unit-тесты квот capability-запросов плагинов (F2).

acceptance №10: превышение ``ops_per_minute`` → ``429`` + ``QUOTA_EXCEEDED``.
acceptance №21: один плагин не может исчерпать квоту компании — ключ строго
per-``(company, plugin_instance, artifact_digest)``, НЕ per-company и НЕ
per-project (``tenant_infra/quota.py:93-107`` для плагинов неприменим).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (верификация заморожена, план §0.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.plugins.capabilities.contracts import TypedContractError
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    PluginQuotaKey,
    QuotaOp,
    QuotaReason,
    quota_key_from_claims,
)

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def key(
    *, company: str = "co_123", instance: str = "inst_abc", digest: str = DIGEST
) -> PluginQuotaKey:
    return quota_key_from_claims(
        company_id=company, plugin_instance_id=instance, artifact_digest=digest
    )


# ---------------------------------------------------------------------------
# per-(company, instance, digest) изоляция (acceptance №21)
# ---------------------------------------------------------------------------


def test_quota_key_requires_full_triple_and_has_no_project_id() -> None:
    """Ключ обязан содержать все три компонента; ``project_id`` в нём нет."""
    import dataclasses

    fields = {item.name for item in dataclasses.fields(PluginQuotaKey)}
    assert fields == {"company_id", "plugin_instance_id", "artifact_digest"}
    assert "project_id" not in fields
    assert key().cache_key().startswith("rl:plugin:co_123:inst_abc:sha256:")


def test_quota_key_rejects_missing_or_invalid_artifact_digest() -> None:
    """Нельзя построить «квоту компании», забыв digest/instance."""
    with pytest.raises(TypedContractError):
        PluginQuotaKey(company_id="co_123", plugin_instance_id="", artifact_digest=DIGEST)
    with pytest.raises(TypedContractError):
        PluginQuotaKey(company_id="co_123", plugin_instance_id="inst_abc", artifact_digest="")
    with pytest.raises(TypedContractError):
        PluginQuotaKey(company_id="co_123", plugin_instance_id="inst_abc", artifact_digest="md5:abc")


def test_two_instances_of_one_company_have_independent_counters() -> None:
    """Два инстанса ОДНОЙ компании: лимит 1 на инстанс, оба получают allow."""
    quota = InMemoryQuota(PluginQuota(ops_per_minute=1), now=NOW)
    first = key(instance="inst_abc")
    second = key(instance="inst_def")

    assert quota.enforce(first, QuotaOp.OPS, 1).allowed is True
    # Тот же company_id, но другой instance: счётчик НЕ исчерпан.
    assert quota.enforce(second, QuotaOp.OPS, 1).allowed is True
    # Повтор первого инстанса уже отказывает, второй всё ещё доступен.
    assert quota.enforce(first, QuotaOp.OPS, 1).code is QuotaReason.QUOTA_EXCEEDED
    assert quota.usage(first, QuotaOp.OPS) == 1
    assert quota.usage(second, QuotaOp.OPS) == 1


def test_one_noisy_plugin_cannot_exhaust_company_quota() -> None:
    """Кр. 21: «шумный» инстанс тратит весь свой лимит, сосед продолжает работать."""
    quota = InMemoryQuota(PluginQuota(ops_per_minute=3, max_bytes=100), now=NOW)
    noisy = key(instance="inst_noisy")
    quiet = key(instance="inst_quiet")

    for _ in range(3):
        assert quota.enforce(noisy, QuotaOp.OPS, 1).allowed is True
    noisy_decision = quota.enforce(noisy, QuotaOp.OPS, 1)
    assert noisy_decision.code is QuotaReason.QUOTA_EXCEEDED
    assert noisy_decision.http_status == 429

    # Компания НЕ исчерпана: у тихого инстанса свой счётчик.
    quiet_decision = quota.enforce(quiet, QuotaOp.OPS, 1)
    assert quiet_decision.allowed is True
    assert quiet_decision.http_status == 200
    assert quiet_decision.key.to_tuple() != noisy_decision.key.to_tuple()


def test_new_artifact_digest_starts_a_fresh_counter() -> None:
    """Смена артефакта (новая версия) не наследует счётчик старого digest'а."""
    quota = InMemoryQuota(PluginQuota(ops_per_minute=1), now=NOW)
    old = key(digest=DIGEST)
    new = key(digest=OTHER_DIGEST)

    assert quota.enforce(old, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(new, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(old, QuotaOp.OPS, 1).code is QuotaReason.QUOTA_EXCEEDED


def test_different_companies_are_isolated() -> None:
    quota = InMemoryQuota(PluginQuota(ops_per_minute=1), now=NOW)
    assert quota.enforce(key(company="co_A"), QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(key(company="co_B"), QuotaOp.OPS, 1).allowed is True


def test_other_quota_dimensions_are_also_per_instance() -> None:
    """max_bytes/keys/object_bytes/egress_bytes ключуются той же тройкой."""
    quota = InMemoryQuota(PluginQuota(max_bytes=10, max_keys=1, max_object_bytes=10, max_egress_bytes=10))
    first, second = key(instance="inst_a"), key(instance="inst_b")
    for op, amount in (
        (QuotaOp.BYTES, 10),
        (QuotaOp.KEYS, 1),
        (QuotaOp.OBJECT_BYTES, 10),
        (QuotaOp.EGRESS_BYTES, 10),
    ):
        assert quota.enforce(first, op, amount).allowed is True
        assert quota.enforce(second, op, 1).allowed is True


# ---------------------------------------------------------------------------
# QUOTA_EXCEEDED / 429 (acceptance №10)
# ---------------------------------------------------------------------------


def test_ops_per_minute_exceeded_is_429_and_quota_exceeded() -> None:
    quota = InMemoryQuota(PluginQuota(ops_per_minute=2), now=NOW)
    ident = key()

    assert quota.enforce(ident, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(ident, QuotaOp.OPS, 1).allowed is True
    exceeded = quota.enforce(ident, QuotaOp.OPS, 1)

    assert exceeded.allowed is False
    assert exceeded.code is QuotaReason.QUOTA_EXCEEDED
    assert exceeded.code.value == "QUOTA_EXCEEDED"
    assert exceeded.http_status == 429
    assert exceeded.retry_after_seconds is not None
    assert exceeded.remaining == 0
    assert exceeded.used == 2


def test_hard_limit_exceeded_is_also_429() -> None:
    """max_rows/bytes — жёсткий лимит, а не observability/alert (§4.4)."""
    quota = InMemoryQuota(PluginQuota(max_bytes=5))
    decision = quota.enforce(key(), QuotaOp.BYTES, 6)
    assert decision.code is QuotaReason.QUOTA_EXCEEDED
    assert decision.http_status == 429


def test_denied_enforcement_does_not_consume_the_limit() -> None:
    """Отказ не «доедает» счётчик: превышение не меняет used."""
    quota = InMemoryQuota(PluginQuota(ops_per_minute=1), now=NOW)
    ident = key()
    assert quota.enforce(ident, QuotaOp.OPS, 1).allowed is True
    assert quota.enforce(ident, QuotaOp.OPS, 99).allowed is False
    assert quota.usage(ident, QuotaOp.OPS) == 1


def test_sliding_window_expires_after_60_seconds() -> None:
    """Скользящее окно 60 с: старые события перестают учитываться."""
    quota = InMemoryQuota(PluginQuota(ops_per_minute=1), now=NOW)
    ident = key()
    assert quota.enforce(ident, QuotaOp.OPS, 1).allowed is True
    quota._now = NOW + timedelta(seconds=1)  # в пределах окна — всё ещё deny
    assert quota.enforce(ident, QuotaOp.OPS, 1).allowed is False
    quota._now = NOW + timedelta(seconds=61)
    assert quota.enforce(ident, QuotaOp.OPS, 1).allowed is True


def test_invalid_amount_fails_closed() -> None:
    quota = InMemoryQuota()
    decision = quota.enforce(key(), QuotaOp.OPS, -1)
    assert decision.allowed is False
    assert decision.http_status == 503


def test_quota_values_must_be_positive() -> None:
    """«0 = без лимита» запрещён: отсутствие лимита = конфигурационная ошибка."""
    with pytest.raises(TypedContractError):
        PluginQuota(ops_per_minute=0)
    with pytest.raises(TypedContractError):
        PluginQuota(max_bytes=-1)
