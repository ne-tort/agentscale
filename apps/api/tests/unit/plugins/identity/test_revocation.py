"""F1: revocation по ``grant_id`` (критерий №12) + контракт ``is_active`` задачи 03.

Тесты **написаны, но не запускаются** (verification freeze).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.identity import (
    GrantRevocation,
    InMemoryRevocation,
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
    RevocationPort,
)


def _claims(grant_id: str = "grant_01") -> PluginClaims:
    return PluginClaims(
        sub="plugin:mod_equipment:co_123:inst_abc",
        company_id="co_123",
        plugin_id="mod_equipment",
        plugin_instance_id="inst_abc",
        manifest_version="1.2.0",
        artifact_digest="sha256:" + "a" * 64,
        grant_id=grant_id,
        scopes=("cache",),
        quota_ref="co_123/plan_pro",
        jti="a" * 32,
        iat=1_700_000_000,
        exp=1_700_000_300,
    )


# --- порт (задача 03) --------------------------------------------------------

def test_in_memory_revocation_implements_port_structurally() -> None:
    port: RevocationPort = InMemoryRevocation()
    assert isinstance(port, RevocationPort)


def test_grant_revocation_exposes_is_active_contract() -> None:
    rev = GrantRevocation(InMemoryRevocation())
    assert isinstance(rev.port, RevocationPort)
    assert rev.is_active("grant_01") is True
    assert rev.is_active("grant_01", now=1_700_000_100) is True
    assert rev.is_active("grant_01", now=datetime.now(UTC)) is True


def test_is_active_accepts_naive_datetime_as_fail_closed() -> None:
    rev = GrantRevocation(InMemoryRevocation())
    # Naive datetime = неоднозначное время → fail-closed (False), а не «активен».
    assert rev.is_active("grant_01", now=datetime(2026, 1, 1)) is False


def test_is_active_false_when_port_missing() -> None:
    # «Отсутствие интеграции = явная ошибка, а не молчаливый успех» (план §0.5).
    rev = GrantRevocation()
    assert rev.port is None
    assert rev.is_active("grant_01") is False
    assert rev.is_revoked("grant_01") is True


def test_is_active_false_when_port_raises() -> None:
    class BrokenPort:
        def is_active(self, grant_id: str, *, now) -> bool:
            raise RuntimeError("DB down")

        def is_revoked(self, grant_id: str) -> bool:
            raise RuntimeError("DB down")

        def revoke(self, grant_id: str) -> None:
            raise RuntimeError("DB down")

    rev = GrantRevocation(BrokenPort())  # type: ignore[arg-type]
    # Хранилище недоступно ≠ «grant активен».
    assert rev.is_active("grant_01") is False
    assert rev.is_revoked("grant_01") is True


def test_is_active_false_for_empty_grant_id() -> None:
    rev = GrantRevocation(InMemoryRevocation())
    assert rev.is_active("") is False
    assert rev.is_active("   ") is False


# --- revoke → check падает ---------------------------------------------------

def test_revoke_then_check_raises_revoked() -> None:
    port = InMemoryRevocation()
    rev = GrantRevocation(port)
    claims = _claims()

    assert rev.check(claims) is claims            # до отзыва — активен
    rev.revoke(claims.grant_id)
    assert port.is_revoked("grant_01") is True
    assert port.is_active("grant_01", now=0) is False

    # Токен ещё не истёк (exp в будущем), но grant деактивирован → отказ.
    with pytest.raises(PluginTokenError) as ei:
        rev.check(claims)
    assert ei.value.code == PluginTokenErrorCode.REVOKED


def test_revoke_is_immediate_and_not_bump_pod_bridge_generation() -> None:
    """Отзыв не зависит от TTL и не трогает pod_bridge generation/Redis."""
    port = InMemoryRevocation()
    rev = GrantRevocation(port)
    claims = _claims()
    rev.revoke("grant_01")
    with pytest.raises(PluginTokenError) as ei:
        # ``now`` заведомо внутри срока жизни токена.
        rev.check(claims)
    assert ei.value.code == PluginTokenErrorCode.REVOKED
    # В модуле нет ни импорта bridge, ни обращений к generation/Redis.
    from prodavan.application.plugins.identity import revocation as module

    assert "bump_pod_bridge_generation" not in dir(module)
    assert "pod_identity" not in module.__name__


def test_revocation_is_per_grant_only() -> None:
    port = InMemoryRevocation()
    rev = GrantRevocation(port)
    rev.revoke("grant_01")
    assert rev.is_active("grant_02") is True
    assert rev.check(_claims("grant_02")).grant_id == "grant_02"


def test_initially_revoked_grants_are_inactive() -> None:
    port = InMemoryRevocation(initially_revoked=["grant_01"])
    assert port.is_active("grant_01", now=0) is False
    assert port.is_revoked("grant_01") is True
    with pytest.raises(PluginTokenError):
        GrantRevocation(port).check(_claims())


def test_activate_restores_grant_for_tests() -> None:
    port = InMemoryRevocation(initially_revoked=["grant_01"])
    port.activate("grant_01")
    assert port.is_active("grant_01", now=0) is True
    assert GrantRevocation(port).check(_claims()).grant_id == "grant_01"


def test_bind_port_after_construction() -> None:
    rev = GrantRevocation()
    assert rev.is_active("grant_01") is False
    rev.bind(InMemoryRevocation())
    assert rev.is_active("grant_01") is True


def test_revoke_without_port_is_fail_closed_error() -> None:
    rev = GrantRevocation()
    with pytest.raises(PluginTokenError) as ei:
        rev.revoke("grant_01")
    assert ei.value.code == PluginTokenErrorCode.REVOKED


def test_revoke_empty_grant_id_is_rejected() -> None:
    rev = GrantRevocation(InMemoryRevocation())
    with pytest.raises(PluginTokenError) as ei:
        rev.revoke("  ")
    assert ei.value.code == PluginTokenErrorCode.MISSING_CLAIM


def test_check_with_empty_grant_id_claims_is_revoked() -> None:
    rev = GrantRevocation(InMemoryRevocation())
    with pytest.raises(PluginTokenError) as ei:
        rev.check(_claims("   "))
    assert ei.value.code == PluginTokenErrorCode.REVOKED


def test_in_memory_port_rejects_empty_grant_id_on_revoke() -> None:
    port = InMemoryRevocation()
    with pytest.raises(ValueError, match="empty"):
        port.revoke("")


def test_plain_object_with_is_active_satisfies_port() -> None:
    """Задача 03 может передать свой объект без наследования (структурный тип)."""

    class DbBackedPort:
        def __init__(self) -> None:
            self.seen_now: list[float] = []

        def is_active(self, grant_id: str, *, now) -> bool:
            self.seen_now.append(float(now))
            return grant_id == "grant_01"

        def is_revoked(self, grant_id: str) -> bool:
            return not self.is_active(grant_id, now=0)

        def revoke(self, grant_id: str) -> None:
            return None

    db = DbBackedPort()
    assert isinstance(db, RevocationPort)
    rev = GrantRevocation(db)
    assert rev.is_active("grant_01", now=1_700_000_100) is True
    assert db.seen_now == [1_700_000_100.0]
    assert rev.is_active("grant_02") is False
