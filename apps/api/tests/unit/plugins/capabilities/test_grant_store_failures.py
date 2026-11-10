"""Недоступность durable grant-store: сердце fail-closed логики сервиса.

``PluginCapabilityService`` обязан отказать, если БД с grant'ами недоступна.
Ветка существовала (``grant_store_unavailable`` -> GRANT_MISSING), но её не
проверял ни один тест: ``FakeGrantStore.fail`` объявлен и не использовался,
поэтому регрессия «упавший store превращается в allow» прошла бы молча.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.capability_gateway_service import DecisionCode
from prodavan.application.plugins.capabilities.audit import InMemoryAudit
from prodavan.application.plugins.capabilities.contracts import CacheGetRequest
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import InMemoryQuota, PluginQuota
from prodavan.application.plugins.capabilities.service import PluginCapabilityService

from test_service import (  # noqa: E402 - same-directory test module
    ActiveGrant,
    EchoCacheAdapter,
    FakeClaims,
    FakeGrantRecord,
    cache_request,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FailingGrantStore:
    """Durable-источник, который недоступен: любой вызов бросает."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_by_grant_id(self, *, grant_id: str):
        self.calls += 1
        raise RuntimeError("database unavailable")


def _service(store) -> tuple[PluginCapabilityService, EchoCacheAdapter]:
    registry = AdapterRegistry()
    cache = EchoCacheAdapter()
    registry.register("cache", cache)
    service = PluginCapabilityService(
        registry=registry,
        grant_store=store,
        quota=InMemoryQuota(now=NOW),
        audit=InMemoryAudit(),  # type: ignore[arg-type]
        revocation=ActiveGrant(active=True),  # type: ignore[arg-type]
    )
    return service, cache


def _request() -> CacheGetRequest:
    return cache_request()


async def test_unavailable_grant_store_denies_and_skips_the_adapter() -> None:
    store = FailingGrantStore()
    service, cache = _service(store)
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="cache.get",
        request=_request(), now=NOW,
    )
    assert result.allowed is False
    # Недоступность store обязана выглядеть как отсутствие grant'а, а не как allow.
    assert result.code is DecisionCode.GRANT_MISSING
    # Store вызывался, адаптер — нет: отказ до побочных эффектов.
    assert store.calls == 1
    assert cache.calls == 0


async def test_revoked_record_is_denied_as_revoked_not_missing() -> None:
    record = FakeGrantRecord(
        status="revoked", revoked_at=NOW - timedelta(minutes=1),
    )

    class _Store:
        async def get_by_grant_id(self, *, grant_id: str):
            return record

    service, cache = _service(_Store())
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="cache.get",
        request=_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.GRANT_REVOKED
    assert cache.calls == 0


async def test_expired_record_is_denied_as_expired() -> None:
    record = FakeGrantRecord(expires_at=NOW - timedelta(seconds=1))

    class _Store:
        async def get_by_grant_id(self, *, grant_id: str):
            return record

    service, cache = _service(_Store())
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="cache.get",
        request=_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.GRANT_EXPIRED
    assert cache.calls == 0
