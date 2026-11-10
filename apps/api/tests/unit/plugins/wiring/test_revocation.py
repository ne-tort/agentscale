"""F11: ``DurableGrantRevocation`` — БД как источник истины, fail-closed.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio

from prodavan.application.plugins.identity.revocation import GrantRevocation, RevocationPort
from prodavan.application.plugins.identity.types import (
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
)
from prodavan.application.plugins.wiring.config import PluginPlatformConfigError
from prodavan.application.plugins.wiring.revocation import (
    GRANT_STATUS_ACTIVE,
    GRANT_STATUS_REVOKED,
    DurableGrantRevocation,
    GrantRevocationUnavailable,
    GrantSnapshot,
    GrantStorePort,
)
from tests.unit.plugins.wiring.conftest import FakeGrantRow, FakeGrantStore


def _now() -> datetime:
    return datetime(2026, 11, 8, 6, 0, 0, tzinfo=UTC)


async def test_active_grant_from_db_is_active(grant_store: FakeGrantStore) -> None:
    rev = DurableGrantRevocation(grant_store, clock=_now)
    assert isinstance(grant_store, GrantStorePort)
    assert await rev.is_active("grant_01", now=_now()) is True
    assert await rev.is_revoked("grant_01") is False
    # Удовлетворяет портам F1/F2 структурно (``is_active``/``revoke``/
    # ``is_revoked``/``check`` объявлены явно); Protocol без @runtime_checkable
    # проверяем по наличию методов, а не isinstance.
    assert isinstance(rev, RevocationPort)  # F1: @runtime_checkable
    for method in ("is_active", "revoke", "is_revoked", "check"):
        assert callable(getattr(rev, method, None))


async def test_missing_port_is_refused_at_construction() -> None:
    """Нет интеграции с БД = явная ошибка, а не in-memory allow-all."""
    with pytest.raises(PluginPlatformConfigError, match="GrantStorePort"):
        DurableGrantRevocation(None)


async def test_db_failure_does_not_silently_pass(grant_store: FakeGrantStore) -> None:
    """Ключевой тест: недоступность БД НЕ переживается молча."""
    rev = DurableGrantRevocation(grant_store, clock=_now)
    grant_store.fail_get = True
    assert await rev.is_active("grant_01", now=_now()) is False
    assert await rev.is_revoked("grant_01") is True
    # Обратная совместимость с GrantRevocation задачи 02: check -> REVOKED.
    wrapped = GrantRevocation(rev)
    with pytest.raises(PluginTokenError) as ei:
        await wrapped.check(PluginClaims(grant_id="grant_01"))
    assert ei.value.code == PluginTokenErrorCode.REVOKED


async def test_expired_cache_does_not_survive_db_outage(grant_store: FakeGrantStore) -> None:
    """Просроченный кэш + упавшая БД = отказ, а не «переживём на старом ответе»."""
    current = _now()
    rev = DurableGrantRevocation(grant_store, cache_ttl_seconds=1.0, clock=lambda: current)
    assert await rev.is_active("grant_01", now=current) is True  # кэш заполнен
    current = current + timedelta(seconds=2)
    grant_store.fail_get = True
    assert await rev.is_active("grant_01", now=current) is False


async def test_revoke_deactivates_status_in_db(grant_store: FakeGrantStore) -> None:
    rev = DurableGrantRevocation(grant_store, clock=_now)
    await rev.revoke("grant_01")
    assert grant_store.deactivate_calls == ["grant_01"]
    assert grant_store._rows["grant_01"].status == GRANT_STATUS_REVOKED
    assert await rev.is_active("grant_01", now=_now()) is False


async def test_revoke_failure_raises_instead_of_silent_success(grant_store: FakeGrantStore) -> None:
    grant_store.fail_deactivate = True
    rev = DurableGrantRevocation(grant_store, clock=_now)
    with pytest.raises(GrantRevocationUnavailable, match="NOT confirmed"):
        await rev.revoke("grant_01")


async def test_revoke_unconfirmed_by_store_raises(grant_store: FakeGrantStore) -> None:
    grant_store.deactivate_result = False
    rev = DurableGrantRevocation(grant_store, clock=_now)
    with pytest.raises(GrantRevocationUnavailable, match="did not confirm"):
        await rev.revoke("grant_01")


async def test_revoke_empty_grant_id_is_rejected(grant_store: FakeGrantStore) -> None:
    rev = DurableGrantRevocation(grant_store, clock=_now)
    with pytest.raises(ValueError, match="must not be empty"):
        await rev.revoke("   ")


async def test_unknown_or_missing_grant_is_not_active(grant_store: FakeGrantStore) -> None:
    rev = DurableGrantRevocation(grant_store, clock=_now)
    assert await rev.is_active("grant_unknown", now=_now()) is False
    assert await rev.is_active("", now=_now()) is False
    assert await rev.is_active(None, now=_now()) is False  # type: ignore[arg-type]


async def test_unknown_status_is_fail_closed(grant_store: FakeGrantStore) -> None:
    grant_store._rows["grant_01"] = FakeGrantRow(status="suspended")
    rev = DurableGrantRevocation(grant_store, clock=_now)
    assert await rev.is_active("grant_01", now=_now()) is False


async def test_revoked_at_marker_denies_even_if_status_active(grant_store: FakeGrantStore) -> None:
    grant_store._rows["grant_01"] = FakeGrantRow(
        status=GRANT_STATUS_ACTIVE, revoked_at=_now()
    )
    rev = DurableGrantRevocation(grant_store, clock=_now)
    assert await rev.is_active("grant_01", now=_now()) is False


async def test_not_yet_issued_and_expired_grants_deny(grant_store: FakeGrantStore) -> None:
    now = _now()
    grant_store._rows["future"] = FakeGrantRow(
        grant_id="future", issued_at=now + timedelta(hours=1)
    )
    grant_store._rows["past"] = FakeGrantRow(
        grant_id="past", expires_at=now - timedelta(seconds=1)
    )
    rev = DurableGrantRevocation(grant_store, clock=lambda: now)
    assert await rev.is_active("future", now=now) is False
    assert await rev.is_active("past", now=now) is False


async def test_naive_now_is_fail_closed(grant_store: FakeGrantStore) -> None:
    rev = DurableGrantRevocation(grant_store, clock=_now)
    assert await rev.is_active("grant_01", now=datetime(2026, 11, 8, 6, 0, 0)) is False  # type: ignore[arg-type]


async def test_naive_timestamp_from_store_is_fail_closed(grant_store: FakeGrantStore) -> None:
    grant_store._rows["grant_01"] = FakeGrantRow(expires_at=datetime(2027, 1, 1))
    rev = DurableGrantRevocation(grant_store, clock=_now)
    assert await rev.is_active("grant_01", now=_now()) is False


async def test_check_method_matches_transport_deps_contract(grant_store: FakeGrantStore) -> None:
    """``app.state.plugin_revocation`` можно использовать без обёртки задачи 02."""
    rev = DurableGrantRevocation(grant_store, clock=_now)
    claims = PluginClaims(grant_id="grant_01")
    assert await rev.check(claims) is claims
    await rev.revoke("grant_01")
    with pytest.raises(PluginTokenError) as ei:
        await rev.check(claims)
    assert ei.value.code == PluginTokenErrorCode.REVOKED


async def test_no_bump_pod_bridge_generation_used(grant_store: FakeGrantStore) -> None:
    """Отзыв плагина не дёргает агентский механизм (критерий №12, ADR-008).

    Инвариант = НЕТ вызова/атрибута/импорта ``bump_pod_bridge_generation``
    в ИСПОЛНЯЕМОМ коде. Имя встречается в docstring'ах как явный запрет
    («НЕ использует ...»), поэтому проверка «подстрока в исходнике» даёт
    ложное срабатывание на самом отрицании. Проверяем AST (идентификаторы
    вне строковых констант) + co_names методов, сохраняя инвариант, а не
    ослабляя его до «убрать упоминание из документации».
    """
    import ast
    import inspect
    import textwrap

    forbidden = "bump_pod_bridge_generation"
    source = textwrap.dedent(inspect.getsource(DurableGrantRevocation))
    tree = ast.parse(source)
    identifiers = {
        name
        for node in ast.walk(tree)
        for name in (
            [node.id] if isinstance(node, ast.Name) else
            [node.attr] if isinstance(node, ast.Attribute) else
            [node.name, node.asname] if isinstance(node, ast.alias) else
            []
        )
        if name
    }
    assert forbidden not in identifiers, (
        f"{forbidden} используется в исполняемом коде DurableGrantRevocation"
    )
    # Дополнительно: рантайм-имена всех методов класса (на случай динамики,
    # которую AST исходника класса может не покрыть).
    for attr in vars(DurableGrantRevocation).values():
        code = getattr(getattr(attr, "__func__", attr), "__code__", None)
        if code is not None:
            assert forbidden not in code.co_names
    # Отзыв опирается на durable-хранилище, а не на агентский Pod/Redis.
    assert "deactivate" in DurableGrantRevocation.revoke.__code__.co_names
    assert "grant_store" in source or "deactivate" in source


async def test_grant_snapshot_active_property() -> None:
    assert GrantSnapshot(status="active").active is True
    assert GrantSnapshot(status="REVOKED").active is False
