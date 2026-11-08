"""``build_plugin_repositories``: fail-closed при отсутствии session/redis.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.persistence.repositories.plugins.build import (
    PluginRepositories,
    build_plugin_repositories,
)

from .conftest import FakeRedis, FakeSession, FakeSessionFactory


def test_build_without_session_fails() -> None:
    """ГЛАВНОЕ: сборка без session падает (fail-closed), а не «частично»."""
    with pytest.raises(ValueError, match="session"):
        build_plugin_repositories(None, FakeRedis())  # type: ignore[arg-type]


def test_build_without_redis_fails() -> None:
    """Сборка без redis падает: иначе подключили бы InMemoryQuota."""
    with pytest.raises(ValueError, match="redis"):
        build_plugin_repositories(FakeSessionFactory(), None)  # type: ignore[arg-type]


def test_build_rejects_invalid_session_type() -> None:
    with pytest.raises(ValueError, match="AsyncSession"):
        build_plugin_repositories(object(), FakeRedis())  # type: ignore[arg-type]


def test_build_returns_complete_bundle() -> None:
    """Все 12 полей связки заполнены, ни одного ``None``."""
    bundle = build_plugin_repositories(FakeSessionFactory(), FakeRedis())
    assert isinstance(bundle, PluginRepositories)
    for field_name in (
        "grant_store", "grant_revocation", "quota", "feature_flags",
        "feature_flags_sync", "artifact_store", "subscription_store",
        "idempotency_store", "published_event_store", "dlq_store", "audit",
    ):
        assert getattr(bundle, field_name) is not None


def test_feature_flag_store_is_shared_between_admin_and_strangler() -> None:
    """Один экземпляр: роутер видит запись admin-сервиса сразу."""
    bundle = build_plugin_repositories(FakeSessionFactory(), FakeRedis())
    bundle.feature_flags.warm("co_123", ["mod_equipment"])
    assert bundle.feature_flags_sync.enabled("co_123", "mod_equipment") is True
    assert bundle.grant_store.enabled_modules("co_123") == ("mod_equipment",)
    assert bundle.feature_flags_sync.inner is bundle.feature_flags


def test_bundle_is_immutable() -> None:
    """frozen/slots: «донастроить» связку после сборки нельзя."""
    bundle = build_plugin_repositories(FakeSessionFactory(), FakeRedis())
    with pytest.raises((AttributeError, TypeError)):
        bundle.audit = None  # type: ignore[misc]


def test_build_with_bound_session() -> None:
    """Готовая AsyncSession тоже допустима (транзакция — вызывающего)."""
    bundle = build_plugin_repositories(FakeSession(), FakeRedis())
    assert bundle.grant_store._session is not None  # не коммитим чужую сессию
    assert bundle.audit._session is not None


def test_quota_in_bundle_denies_when_redis_unavailable() -> None:
    """Связка собрана, но Redis упал во время запроса → deny, не allow."""
    from prodavan.application.plugins.capabilities.quotas import (
        PluginQuotaKey,
        QuotaOp,
        QuotaReason,
    )

    bundle = build_plugin_repositories(FakeSessionFactory(), FakeRedis(available=False))
    key = PluginQuotaKey(
        company_id="co_123",
        plugin_instance_id="inst_abc",
        artifact_digest="sha256:" + "a" * 64,
    )
    decision = bundle.quota.enforce(key, QuotaOp.OPS, 1)
    assert decision.allowed is False
    assert decision.code is QuotaReason.QUOTA_EXCEEDED
