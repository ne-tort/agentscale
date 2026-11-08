"""Тесты in-memory портов состояния (dev/тесты; кластер → БД/Redis, задача A10).

ПИШЕМ, НЕ ЗАПУСКАЕМ (verification freeze миграции).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.events.contracts import IdempotencyKey
from prodavan.infrastructure.plugins.adapters.stores import (
    CLUSTER_BACKEND_NOTE,
    InMemoryAudit,
    InMemoryIdempotencyStore,
    InMemoryPublishedEventStore,
    InMemorySubscriptionStore,
    InMemoryWakeHook,
    InstanceMappingError,
    event_instance_resolver,
    in_memory_event_ports,
)

from .conftest import FakeActivator, make_event, make_subscription


def test_cluster_backend_note_is_explicit() -> None:
    """Явная пометка: для кластера — БД/Redis (задача A10)."""
    assert "A10" in CLUSTER_BACKEND_NOTE
    assert "БД/Redis" in CLUSTER_BACKEND_NOTE


@pytest.mark.asyncio
async def test_subscription_unknown_or_revoked_is_fail_closed() -> None:
    store = InMemorySubscriptionStore()
    assert await store.get("sub_1") is None
    assert await store.is_active("sub_1") is False  # нет записи = НЕ активна
    sub = make_subscription()
    store.register(sub, active=True)
    assert await store.get("sub_1") is sub
    assert await store.is_active("sub_1") is True
    store.revoke("sub_1")
    assert await store.is_active("sub_1") is False


@pytest.mark.asyncio
async def test_idempotency_key_is_subscription_plus_source_event() -> None:
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey(subscription_id="sub_1", source_event_id="src-1")
    assert await store.seen(key) is False
    await store.remember(key)
    assert await store.seen(key) is True
    # Другой source_event_id (или другая подписка) — НЕ дубль.
    assert await store.seen(IdempotencyKey("sub_1", "src-2")) is False
    assert await store.seen(IdempotencyKey("sub_2", "src-1")) is False
    # remember идемпотентен (диспечер вызывает и после ACK, и после DLQ).
    await store.remember(key)
    assert len(store.remembered) == 1


@pytest.mark.asyncio
async def test_published_event_store_key_is_separate_from_consumer() -> None:
    store = InMemoryPublishedEventStore()
    assert await store.seen("evt-1") is False
    await store.remember("evt-1")
    assert await store.seen("evt-1") is True
    assert await store.seen("evt-2") is False


@pytest.mark.asyncio
async def test_wake_hook_calls_activator_before_delivery() -> None:
    """WakeHook вызывает activator ДО handler'а; ошибка маппинга = отказ."""
    activator = FakeActivator()
    hook = InMemoryWakeHook(
        activator=activator,
        instance_id_for=event_instance_resolver({("mod_equipment", "co_42"): "inst_abc"}),
    )
    event = make_event()
    sub = make_subscription()
    timeline: list[str] = []
    original_wake = activator.wake

    async def _recorded_wake(instance_id: str, *, timeout: float | None = None):  # type: ignore[no-untyped-def]
        timeline.append("wake")
        return await original_wake(instance_id, timeout=timeout)

    activator.wake = _recorded_wake  # type: ignore[method-assign]
    await hook.wake(event, subscription=sub)
    timeline.append("handler")
    assert timeline == ["wake", "handler"]
    assert hook.calls[0][0] == "inst_abc"
    assert activator.wake_calls == [("inst_abc", None)]


@pytest.mark.asyncio
async def test_wake_hook_unknown_instance_is_fail_closed() -> None:
    hook = InMemoryWakeHook(
        activator=FakeActivator(),
        instance_id_for=event_instance_resolver({}),  # резолвер ничего не знает
    )
    with pytest.raises(InstanceMappingError):
        await hook.wake(make_event(), subscription=make_subscription())


@pytest.mark.asyncio
async def test_in_memory_audit_serves_both_audit_contracts() -> None:
    """Один sink: events.AuditPort.append + gateway AuditAdapter.write."""
    audit = InMemoryAudit()
    await audit.append("plugin_event_received", details={"event_id": "evt-1"})
    await audit.write({"event_type": "plugin_page_opened", "decision": "allow"})
    assert audit.entries[0][0] == "plugin_event_received"
    assert audit.entries[0][1]["event_id"] == "evt-1"
    assert audit.records[0]["decision"] == "allow"
    assert audit.total == 2

    failing = InMemoryAudit(fail=True)
    with pytest.raises(RuntimeError):
        await failing.append("e", details={})
    with pytest.raises(RuntimeError):
        await failing.write({})


@pytest.mark.asyncio
async def test_in_memory_event_ports_are_explicit_and_not_auto_wired() -> None:
    """Набор для тестов собирается ЯВНО; ``build_event_dispatcher`` его не подставляет."""
    ports = in_memory_event_ports(
        activator=FakeActivator(),
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(make_subscription(), active=True)
    assert await ports.subscriptions.is_active("sub_1") is True
    assert isinstance(ports.idempotency, InMemoryIdempotencyStore)
    assert isinstance(ports.published_events, InMemoryPublishedEventStore)
    assert isinstance(ports.audit, InMemoryAudit)
    assert isinstance(ports.wake_hook, InMemoryWakeHook)
