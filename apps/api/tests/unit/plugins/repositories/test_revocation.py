"""Отзыв по ``grant_id`` и TTL-кэш ``DbGrantRevocation`` (критерий №12).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.application.plugins.identity.revocation import RevocationPort
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.revocation import (
    DbGrantRevocation,
    _CacheEntry,
)

from .conftest import NOW, FakeSession, grant_record, grant_row


def _repo(*, rows: list | None = None) -> SqlCapabilityGrantRepository:
    session = FakeSession(rows=rows)
    return SqlCapabilityGrantRepository(session=session, now=lambda: NOW)


def test_db_revocation_satisfies_both_port_protocols() -> None:
    """Структурно совместим с портами задач 02 и 03.

    ``GrantRevocationPort`` НЕ помечен ``@runtime_checkable``, поэтому
    ``isinstance`` с ним недопустим — проверяем сигнатуру метода напрямую.
    """
    import inspect

    rev = DbGrantRevocation(_repo(), ttl_seconds=30, clock=lambda: NOW)
    assert isinstance(rev, RevocationPort)
    signature = inspect.signature(rev.is_active)
    assert "grant_id" in signature.parameters
    assert "now" in signature.parameters
    assert callable(rev.is_active)


@pytest.mark.asyncio
async def test_revoke_by_grant_id_deactivates_row_and_old_token() -> None:
    """Кр. 12: после revoke старый ``PluginClaims`` по grant_id отклоняется."""
    from prodavan.application.plugins.identity.types import PluginClaims, PluginTokenError

    row = grant_row(id="pcg_01", status="active")
    repo = _repo(rows=[row])
    rev = DbGrantRevocation(repo, ttl_seconds=30, clock=lambda: NOW)

    # Прогрев кэша авторитетным ответом из БД.
    assert await rev.is_active_async("pcg_01", now=NOW) is True
    assert rev.is_active("pcg_01", now=NOW) is True

    # Sync-отзыв инвалидирует кэш НЕМЕДЛЕННО (не дожидаясь TTL).
    rev.revoke("pcg_01")
    assert rev.is_active("pcg_01", now=NOW) is False
    assert repo.get_by_id("pcg_01").status is GrantStatus.REVOKED

    claims = PluginClaims(
        sub="plugin:mod_equipment:co_123:inst_abc",
        company_id="co_123",
        plugin_id="mod_equipment",
        plugin_instance_id="inst_abc",
        manifest_version="1.2.0",
        artifact_digest="sha256:" + "a" * 64,
        grant_id="pcg_01",
        scopes=("cache",),
        quota_ref="co_123/plan_pro",
        jti="a" * 32,
        iat=1_700_000_000,
        exp=1_700_000_300,
    )
    with pytest.raises(PluginTokenError):
        rev.check(claims)


def test_cache_is_invalidated_immediately_on_revoke_not_after_ttl() -> None:
    """Инвалидация при revoke: кэш не «живёт» до истечения TTL."""
    repo = _repo()
    rev = DbGrantRevocation(repo, ttl_seconds=3600, clock=lambda: NOW)
    # Вручную прогреваем запись «активен» (имитация async-refresh).
    repo._snapshot["pcg_01"] = grant_record(grant_id="pcg_01")
    rev._cache["pcg_01"] = _CacheEntry(
        active=True, expires_at=NOW + timedelta(seconds=3600)
    )
    assert rev.is_active("pcg_01", now=NOW) is True

    rev.revoke("pcg_01")
    assert rev.is_active("pcg_01", now=NOW) is False
    assert rev.stats["hits"] >= 1  # до revoke ответ был из кэша


def test_cache_miss_is_fail_closed_not_allow() -> None:
    """Промах кэша = ``False`` (нет «разрешить по умолчанию»)."""
    rev = DbGrantRevocation(_repo(), ttl_seconds=30, clock=lambda: NOW)
    assert rev.is_active("unknown_grant", now=NOW) is False
    assert rev.needs_refresh is True
    assert rev.stats["misses"] >= 1


def test_expired_ttl_entry_is_fail_closed() -> None:
    """Протухшая запись кэша = отказ + пометка на async-refresh."""
    repo = _repo()
    rev = DbGrantRevocation(repo, ttl_seconds=1, clock=lambda: NOW)
    rev._cache["pcg_01"] = _CacheEntry(
        active=True, expires_at=NOW - timedelta(seconds=1)
    )
    assert rev.is_active("pcg_01", now=NOW) is False
    assert rev.needs_refresh is True


def test_naive_datetime_is_fail_closed() -> None:
    """Naive ``now`` неоднозначен → ``False`` (контракт задачи 03)."""
    from datetime import datetime

    rev = DbGrantRevocation(_repo(), clock=lambda: NOW)
    assert rev.is_active("pcg_01", now=datetime(2026, 1, 1)) is False


@pytest.mark.asyncio
async def test_refresh_rebuilds_cache_from_db() -> None:
    """Async-refresh берёт авторитетный ответ из БД и снимает needs_refresh."""
    rows = [grant_row(id="pcg_01", status="active"), grant_row(id="pcg_02", status="revoked")]
    repo = _repo(rows=rows)
    rev = DbGrantRevocation(repo, ttl_seconds=30, clock=lambda: NOW)

    assert await rev.refresh() is None
    assert rev.needs_refresh is False
    assert rev.is_active("pcg_01", now=NOW) is True
    assert rev.is_active("pcg_02", now=NOW) is False


@pytest.mark.asyncio
async def test_is_revoked_async_and_revoke_async_port_shapes() -> None:
    """Стык с ``admin.ports.RevocationPort`` (async revoke/is_revoked)."""
    repo = _repo(rows=[grant_row(id="pcg_01", status="active")])
    rev = DbGrantRevocation(repo, clock=lambda: NOW)

    assert await rev.is_revoked_async(grant_id="pcg_01") is False
    # Durable-отзыв подтверждён (FakeSession.execute -> rowcount=1).
    assert await rev.revoke_async(grant_id="pcg_01", reason="test") is True
    assert rev.is_active("pcg_01", now=NOW) is False


def test_revoke_of_unknown_grant_never_becomes_active() -> None:
    """Отзыв неизвестного grant: кэш всё равно помечен revoked (fail-closed)."""
    rev = DbGrantRevocation(_repo(), clock=lambda: NOW)
    rev.revoke("ghost")
    assert rev.is_active("ghost", now=NOW) is False
    assert rev.is_revoked("ghost") is True
