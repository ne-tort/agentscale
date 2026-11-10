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
    ConsumerAssignment,
    DeliveryClaim,
    IdempotencyKey,
    PluginEvent,
    RawDlqRecord,
    RawRecordIdempotencyKey,
    Subscription,
)
from prodavan.application.plugins.events.ports import ConsumedRecord
from prodavan.application.plugins.events.dispatch import HmacPluginEventSigner

TEST_DIGEST = "sha256:" + ("a1b2c3d4" * 8)


class _EventSecretStore:
    def get(self, ref: str) -> str:
        assert ref == "vault://unit/plugin-events"
        return "unit-test-only-event-signing-key-32-bytes"


TEST_EVENT_SIGNER = HmacPluginEventSigner(
    secret_store=_EventSecretStore(), secret_ref="vault://unit/plugin-events"
)


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
    """Замена ConsumerRecord: wire-dict либо исходные bytes для raw DLQ."""

    value: Any
    topic: str = "agentscale.plugin.events"
    partition: int = 0
    offset: int = 17
    key: bytes | None = b"co_42"
    raw_value: bytes | None = None


def record_of(event: PluginEvent, *, offset: int = 17) -> FakeRecord:
    return FakeRecord(
        value=TEST_EVENT_SIGNER.signed_wire(
            event, topic="agentscale.plugin.events"
        ),
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Порты
# ---------------------------------------------------------------------------


@dataclass
class FakeTopicAdmin:
    """Администратор топиков: создаёт по запросу и помнит, что создано."""

    existing: set[str] = field(default_factory=set)
    created: list[tuple[str, int, int]] = field(default_factory=list)
    exists_calls: list[str] = field(default_factory=list)
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
    sent: list[dict[str, Any]] = field(default_factory=list)
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
    fail_seek: bool = False
    ownership: bool = True

    def delivery_ownership(self, record: ConsumedRecord) -> ConsumerAssignment:
        return ConsumerAssignment(
            topic=record.topic,
            partition=record.partition,
            owned=self.ownership,
            offset=record.offset,
        )

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
        if self.fail_seek:
            raise RuntimeError("revoked partition seek failed")

    async def close(self) -> None:
        return None


@dataclass
class FakeDlq:
    records: list[Any] = field(default_factory=list)
    fail: bool = False

    async def publish(self, record: Any) -> None:
        if self.fail:
            raise RuntimeError("dlq down")
        self.records.append(record)


@dataclass
class FakeRawDlq:
    """Только unit-fake: durable RawDlqPort в production здесь не подменяется."""

    records: list[RawDlqRecord] = field(default_factory=list)
    publish_calls: list[RawDlqRecord] = field(default_factory=list)
    seen_keys: set[RawRecordIdempotencyKey] = field(default_factory=set)
    fail: bool = False
    fail_after_write: bool = False

    async def publish(self, record: RawDlqRecord) -> bool:
        self.publish_calls.append(record)
        if self.fail:
            raise RuntimeError("raw dlq down")
        key = record.idempotency_key
        if key in self.seen_keys:
            return False
        self.seen_keys.add(key)
        self.records.append(record)
        if self.fail_after_write:
            raise RuntimeError("raw dlq acknowledgement lost")
        return True


@dataclass
class FakeSubscriptions:
    subscription: Subscription | None = None
    active: bool = True

    async def get(self, subscription_id: str) -> Subscription | None:
        return self.subscription

    async def is_active(self, subscription_id: str) -> bool:
        return self.active


@dataclass
class FakeDeliveryClaims:
    """Unit-only lease fake; production uses RedisDeliveryClaimStore."""

    active: dict[IdempotencyKey, DeliveryClaim] = field(default_factory=dict)
    completed: set[IdempotencyKey] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    generation: int = 0
    lose_next_renew: bool = False

    async def acquire(
        self, key: IdempotencyKey, *, lease_seconds: float
    ) -> DeliveryClaim | None:
        self.calls.append("acquire")
        if key in self.active and key not in self.completed:
            return None
        self.generation += 1
        claim = DeliveryClaim(
            key=key,
            owner_token=f"owner-{self.generation}",
            generation=self.generation,
            lease_seconds=lease_seconds,
            completed=key in self.completed,
        )
        self.active[key] = claim
        return claim

    async def renew(self, claim: DeliveryClaim) -> bool:
        self.calls.append("renew")
        if self.lose_next_renew:
            self.lose_next_renew = False
            self.active.pop(claim.key, None)
            return False
        return self.active.get(claim.key) == claim

    async def complete(self, claim: DeliveryClaim) -> bool:
        self.calls.append("complete")
        if self.active.get(claim.key) != claim:
            return False
        self.completed.add(claim.key)
        return True

    async def release(self, claim: DeliveryClaim) -> bool:
        self.calls.append("release")
        if self.active.get(claim.key) != claim:
            return False
        self.active.pop(claim.key, None)
        return True


@dataclass
class FakeSeenStore:
    """Одно хранилище и для продюсера (str), и для консьюмера (ключ-кортеж)."""

    seen_keys: set[Any] = field(default_factory=set)
    remembered: list[Any] = field(default_factory=list)
    seen_error: Exception | None = None

    async def seen(self, key: Any) -> bool:
        if self.seen_error is not None:
            raise self.seen_error
        return key in self.seen_keys

    async def remember(self, key: Any) -> None:
        self.remembered.append(key)
        self.seen_keys.add(key)


@dataclass
class FakeAudit:
    entries: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    async def append(self, event_type: str, *, details: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("audit down")
        self.entries.append((event_type, dict(details)))


@dataclass
class FakeHandler:
    """Обработчик: fails_at задаёт, какие попытки падают (с 1)."""

    event_signer: Any = TEST_EVENT_SIGNER
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
    raw_dlq: FakeRawDlq
    subscriptions: FakeSubscriptions
    published: FakeSeenStore
    idempotency: FakeSeenStore
    claims: FakeDeliveryClaims
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
    delivery_lease_seconds: float = 30.0,
    claims: FakeDeliveryClaims | None = None,
) -> Harness:
    """Собрать диспетчер со всеми обязательными портами (fail-closed)."""

    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    sub = subscription or make_subscription()
    admin = FakeTopicAdmin()
    producer = FakeProducer()
    consumer = FakeConsumer(records=list(records or []))
    dlq = FakeDlq()
    raw_dlq = FakeRawDlq()
    subscriptions = FakeSubscriptions(subscription=sub)
    published = FakeSeenStore()
    idempotency = FakeSeenStore()
    claims = claims if claims is not None else FakeDeliveryClaims()
    audit = FakeAudit()
    handler = handler or FakeHandler()
    wake = wake if wake is not None else FakeWake()
    dispatcher = PluginEventDispatcher(
        producer=producer,
        consumer=consumer,
        topic_admin=admin,
        dlq=dlq,
        raw_dlq=raw_dlq,
        subscriptions=subscriptions,
        published_events=published,
        idempotency=idempotency,
        delivery_claims=claims,
        delivery_lease_seconds=delivery_lease_seconds,
        audit=audit,
        handler=handler,
        wake_hook=wake,
    )
    return Harness(
        admin=admin,
        producer=producer,
        consumer=consumer,
        dlq=dlq,
        raw_dlq=raw_dlq,
        subscriptions=subscriptions,
        published=published,
        idempotency=idempotency,
        claims=claims,
        audit=audit,
        handler=handler,
        wake=wake,
        dispatcher=dispatcher,
    )


__all__ = [
    "TEST_DIGEST",
    "TEST_EVENT_SIGNER",
    "FakeAudit",
    "FakeConsumer",
    "FakeDeliveryClaims",
    "FakeDlq",
    "FakeHandler",
    "FakeProducer",
    "FakeRawDlq",
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
