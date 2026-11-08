"""Общие заглушки портов для unit-тестов dispatch-контура событий плагинов.

Фейки НЕ запускают Kafka/Redpanda, НЕ пишут в БД и НЕ импортируют outbox:
они фиксируют порядок вызовов, чтобы acceptance-критерий №20 можно было
проверить чисто на контрактах (verification freeze, тесты пишем, не запускаем).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prodavan.application.plugins.events.contracts import (
    PLUGIN_EVENT_SCHEMA_VERSION,
    IdempotencyKey,
    PluginEvent,
    Subscription,
)
from prodavan.application.plugins.events.ports import ConsumedRecord

TEST_DIGEST = "sha256:" + ("a1b2c3d4" * 8)


# ---------------------------------------------------------------------------
# Вспомогательные builders
# ---------------------------------------------------------------------------


def make_event(
    *,
    event_id: str = "evt-1",
    source_event_id: str | None = None,
    event_type: str = "offer.created",
    company_id: str = "co_42",
    subscription_id: str = "sub_1",
    plugin_id: str | None = "mod_equipment",
    run_id: str = "run_77",
) -> PluginEvent:
    """Валидное событие БЕЗ raw payload (только payload_digest)."""

    return PluginEvent(
        event_id=event_id,
        source_event_id=source_event_id,
        event_type=event_type,
        source="offers",
        company_id=company_id,
        plugin_id=plugin_id,
        subscription_id=subscription_id,
        occurred_at=datetime(2026, 11, 8, 6, 0, tzinfo=UTC),
        payload_digest=TEST_DIGEST,
        run_id=run_id,
        correlation_id="corr_77",
        schema_version=PLUGIN_EVENT_SCHEMA_VERSION,
    )


def make_subscription(
    *,
    subscription_id: str = "sub_1",
    event_types: tuple[str, ...] = ("offer.created",),
    consumer_group: str = "plugin-mod_equipment-sub_1",
) -> Subscription:
    return Subscription(
        id=subscription_id,
        plugin_id="mod_equipment",
        event_types=event_types,
        consumer_group=consumer_group,
    )


# ---------------------------------------------------------------------------
# ConsumedRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeRecord:
    """Замена aiokafka.ConsumerRecord; value — уже wire-dict события."""

    value: dict[str, Any]
    topic: str = "agentscale.plugin.events"
    partition: int = 0
    offset: int = 17
    key: bytes | None = b"co_42"


def record_of(event: PluginEvent, *, offset: int = 17) -> FakeRecord:
    return FakeRecord(value=event.to_wire(), offset=offset)


# ---------------------------------------------------------------------------
# Порты
# ---------------------------------------------------------------------------


@dataclass
class FakeTopicAdmin:
    """Администратор топиков: создаёт по запросу и помнит, что создано."""

    existing: set[str] = field(default_factory=set)
    created: list[tuple[str, int, int]] = []
    exists_calls: list[str] = []
    fail_on_create: bool = False

    async def topic_exists(self, topic: str) -> bool:
        self.exists_calls.append(topic)
        return topic in self.existing

    async def create_topic(
        self, topic: str, *, num_partitions: int, replication_factor: int
    ) -> None:
        if self.fail_on_create:
            raise RuntimeError("admin unavailable")
        self.created.append((topic, num_partitions, replication_factor))
        self.existing.add(topic)


@dataclass
class FakeProducer:
    sent: list[dict[str, Any]] = []
    flushes: int = 0
    fail: bool = False

    async def send(
        self,
        *,
        topic: str,
        key: str,
        value: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        if self.fail:
            raise RuntimeError("broker down")
        self.sent.append({"topic": topic, "key": key, "value": value, "headers": headers})

    async def flush(self) -> None:
        if self.fail:
            raise RuntimeError("broker down")
        self.flushes += 1


@dataclass
class FakeConsumer:
    """Консьюмер с ЯВНОЙ фиксацией commit/seek/poll (acceptance №20)."""

    records: list[ConsumedRecord | None]
    commits: list[int] = field(default_factory=list)
    seeks: list[int] = field(default_factory=list)
    polls: int = 0
    fail_commit: bool = False

    async def poll(self, *, timeout_ms: int = 1000) -> ConsumedRecord | None:
        self.polls += 1
        if not self.records:
            return None
        return self.records.pop(0)

    async def commit(self, offset: int) -> None:
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits.append(offset)

    async def seek(self, offset: int) -> None:
        self.seeks.append(offset)

    async def close(self) -> None:
        return None


@dataclass
class FakeDlq:
    records: list[Any] = []
    fail: bool = False

    async def publish(self, record: Any) -> None:
        if self.fail:
            raise RuntimeError("dlq down")
        self.records.append(record)


@dataclass
class FakeSubscriptions:
    subscription: Subscription | None = None
    active: bool = True

    async def get(self, subscription_id: str) -> Subscription | None:
        return self.subscription

    async def is_active(self, subscription_id: str) -> bool:
        return self.active


@dataclass
class FakeSeenStore:
    """Одно хранилище и для продюсера (str), и для консьюмера (ключ-кортеж)."""

    seen_keys: set[Any] = field(default_factory=set)
    remembered: list[Any] = field(default_factory=list)

    async def seen(self, key: Any) -> bool:
        return key in self.seen_keys

    async def remember(self, key: Any) -> None:
        self.remembered.append(key)
        self.seen_keys.add(key)


@dataclass
class FakeAudit:
    entries: list[tuple[str, dict[str, Any]]] = []
    fail: bool = False

    async def append(self, event_type: str, *, details: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("audit down")
        self.entries.append((event_type, dict(details)))


@dataclass
class FakeHandler:
    """Обработчик: fails_at задаёт, какие попытки падают (с 1)."""

    calls: list[PluginEvent] = field(default_factory=list)
    fails_at: set[int] = field(default_factory=set)
    error: Exception = field(default_factory=lambda: RuntimeError("backend 503"))

    async def handle(self, event: PluginEvent, *, subscription: Subscription) -> None:
        self.calls.append(event)
        if len(self.calls) in self.fails_at:
            raise self.error


@dataclass
class FakeWake:
    wakes: list[PluginEvent] = field(default_factory=list)
    fail: bool = False

    async def wake(self, event: PluginEvent, *, subscription: Subscription) -> None:
        if self.fail:
            raise RuntimeError("not ready")
        self.wakes.append(event)


class RecordingWake:
    """Callable wake_hook: фиксирует порядок относительно handler."""

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.wakes: list[PluginEvent] = []

    async def __call__(self, event: PluginEvent, subscription: Subscription) -> None:
        self.timeline.append("wake")
        self.wakes.append(event)


@dataclass
class Harness:
    """Связка всех фейков + готовый диспетчер."""

    admin: FakeTopicAdmin
    producer: FakeProducer
    consumer: FakeConsumer
    dlq: FakeDlq
    subscriptions: FakeSubscriptions
    published: FakeSeenStore
    idempotency: FakeSeenStore
    audit: FakeAudit
    handler: FakeHandler
    wake: FakeWake
    dispatcher: Any


def build_harness(
    *,
    records: list[ConsumedRecord | None] | None = None,
    subscription: Subscription | None = None,
    handler: FakeHandler | None = None,
    wake: Any | None = None,
) -> Harness:
    """Собрать диспетчер со всеми обязательными портами (fail-closed)."""

    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    sub = subscription or make_subscription()
    admin = FakeTopicAdmin()
    producer = FakeProducer()
    consumer = FakeConsumer(records=list(records or []))
    dlq = FakeDlq()
    subscriptions = FakeSubscriptions(subscription=sub)
    published = FakeSeenStore()
    idempotency = FakeSeenStore()
    audit = FakeAudit()
    handler = handler or FakeHandler()
    wake = wake if wake is not None else FakeWake()
    dispatcher = PluginEventDispatcher(
        producer=producer,
        consumer=consumer,
        topic_admin=admin,
        dlq=dlq,
        subscriptions=subscriptions,
        published_events=published,
        idempotency=idempotency,
        audit=audit,
        handler=handler,
        wake_hook=wake,
    )
    return Harness(
        admin=admin,
        producer=producer,
        consumer=consumer,
        dlq=dlq,
        subscriptions=subscriptions,
        published=published,
        idempotency=idempotency,
        audit=audit,
        handler=handler,
        wake=wake,
        dispatcher=dispatcher,
    )


__all__ = [
    "TEST_DIGEST",
    "FakeAudit",
    "FakeConsumer",
    "FakeDlq",
    "FakeHandler",
    "FakeProducer",
    "FakeRecord",
    "FakeSeenStore",
    "FakeSubscriptions",
    "FakeTopicAdmin",
    "FakeWake",
    "Harness",
    "RecordingWake",
    "build_harness",
    "make_event",
    "make_subscription",
    "record_of",
]
