"""Тесты Kafka/Redpanda-адаптеров (задача 10) — ПИШЕМ, НЕ ЗАПУСКАЕМ.

Покрытие по ТЗ и acceptance №20:
* конфиг из ``consumer_config()``, НЕ из as-built ``KAFKA_CONSUMER_MODE=kick``;
* консьюмер НЕ коммитит до успеха (ACK/DLQ), при RETRY/ошибке → ``seek``;
* ``ensure_topic`` обязателен при ``auto_create_topics_enabled=false``
  (основной топик + ``.dlq``);
* WakeHook вызывается ДО handler'а;
* DLQ сохраняет ``event_id``/``run_id`` и не содержит raw payload.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from prodavan.application.plugins.events.contracts import (
    CONSUMER_AUTO_OFFSET_RESET,
    CONSUMER_ENABLE_AUTO_COMMIT,
    MAX_DELIVERY_ATTEMPTS,
    DeliveryOutcome,
    DlqRecord,
    PluginEventErrorCode,
    consumer_config,
)
from prodavan.application.plugins.events.errors import PluginEventError
from prodavan.application.plugins.events.dispatch import PluginEventDispatcher
from prodavan.application.plugins.events.topics import (
    PLUGIN_EVENTS_TOPIC,
    dlq_topic,
    ensure_dlq_topic,
    ensure_topic,
)
from prodavan.infrastructure.plugins.adapters.kafka_adapters import (
    REQUIRED_PLUGIN_TOPICS,
    AiokafkaConsumerAdapter,
    AiokafkaProducerAdapter,
    KafkaAdapterError,
    KafkaConsumedRecord,
    KafkaDlqAdapter,
    KafkaTopicAdminAdapter,
    consumer_settings_from_contract,
)
from prodavan.infrastructure.plugins.adapters.stores import in_memory_event_ports

from .conftest import (
    TEST_DIGEST,
    FakeActivator,
    FakeConsumerRecord,
    FakeKafkaAdmin,
    FakeKafkaConsumer,
    FakeKafkaProducer,
    FakeNewTopic,
    make_event,
    make_subscription,
)


def _install_fake_aiokafka(monkeypatch: Any) -> None:
    """Подменить модули aiokafka, чтобы тест не требовал живого брокера."""
    structs = types.ModuleType("aiokafka.structs")

    class OffsetAndMetadata:
        def __init__(self, offset: int, metadata: str) -> None:
            self.offset = offset
            self.metadata = metadata

    class TopicPartition:
        def __init__(self, topic: str, partition: int) -> None:
            self.topic = topic
            self.partition = partition

    structs.OffsetAndMetadata = OffsetAndMetadata  # type: ignore[attr-defined]
    structs.TopicPartition = TopicPartition  # type: ignore[attr-defined]
    admin_mod = types.ModuleType("aiokafka.admin")
    admin_mod.NewTopic = FakeNewTopic  # type: ignore[attr-defined]
    aiokafka_mod = types.ModuleType("aiokafka")
    aiokafka_mod.admin = admin_mod  # type: ignore[attr-defined]
    aiokafka_mod.structs = structs  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiokafka", aiokafka_mod)
    monkeypatch.setitem(sys.modules, "aiokafka.admin", admin_mod)
    monkeypatch.setitem(sys.modules, "aiokafka.structs", structs)


# ---------------------------------------------------------------------------
# Конфиг задачи 10, НЕ as-built kick
# ---------------------------------------------------------------------------


def test_consumer_config_is_dispatch_earliest_manual_commit() -> None:
    config = consumer_settings_from_contract()
    assert config["auto_offset_reset"] == CONSUMER_AUTO_OFFSET_RESET == "earliest"
    assert config["enable_auto_commit"] is CONSUMER_ENABLE_AUTO_COMMIT is False
    assert config["consumer_mode"] == "dispatch"
    assert config["max_delivery_attempts"] == MAX_DELIVERY_ATTEMPTS == 3
    assert "kick" not in str(config["consumer_mode"])
    assert config["topic"] == PLUGIN_EVENTS_TOPIC
    # Возвращается НОВЫЙ dict: вызывающий не может испортить канонические значения.
    config["enable_auto_commit"] = True
    assert consumer_config()["enable_auto_commit"] is False


@pytest.mark.asyncio
async def test_consumer_start_kwargs_are_earliest_manual_and_group_from_subscription(
    monkeypatch: Any,
) -> None:
    _install_fake_aiokafka(monkeypatch)
    sub = make_subscription(consumer_group="plugin-mod_equipment-sub_1")
    fake = FakeKafkaConsumer()
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="agentscale-kafka:9092",
        subscription=sub,
        consumer=fake,
    )
    await adapter.start()
    kwargs = adapter.start_kwargs
    assert kwargs["enable_auto_commit"] is False
    assert kwargs["auto_offset_reset"] == "earliest"
    # group_id = subscription.consumer_group, НЕ общий prodavan-api-triggers.
    assert kwargs["group_id"] == "plugin-mod_equipment-sub_1"
    assert adapter.settings["consumer_mode"] == "dispatch"
    assert adapter.settings["topic"] == PLUGIN_EVENTS_TOPIC


@pytest.mark.asyncio
async def test_consumer_refuses_kick_mode_config(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092",
        subscription=make_subscription(),
        consumer=FakeKafkaConsumer(),
        config={"consumer_mode": "kick", "enable_auto_commit": True, "auto_offset_reset": "latest"},
    )
    with pytest.raises(KafkaAdapterError):
        await adapter.start()


def test_required_plugin_topics_are_main_and_dlq() -> None:
    assert REQUIRED_PLUGIN_TOPICS == (
        PLUGIN_EVENTS_TOPIC,
        dlq_topic(PLUGIN_EVENTS_TOPIC),
    )
    assert REQUIRED_PLUGIN_TOPICS[1] == "agentscale.plugin.events.dlq"


# ---------------------------------------------------------------------------
# ensure_topic обязателен при auto_create_topics_enabled=false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_topic_is_mandatory_with_auto_create_false(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    admin = KafkaTopicAdminAdapter(bootstrap_servers="kafka:9092", admin=FakeKafkaAdmin())
    await admin.start()

    # ДО ensure_topic топиков нет: auto_create=false, «сами» они не появятся.
    assert await admin.topic_exists(PLUGIN_EVENTS_TOPIC) is False
    assert await admin.topic_exists(dlq_topic(PLUGIN_EVENTS_TOPIC)) is False

    created = await admin.ensure_plugin_topics()
    assert created == (PLUGIN_EVENTS_TOPIC, "agentscale.plugin.events.dlq")
    assert await admin.topic_exists(PLUGIN_EVENTS_TOPIC) is True
    assert await admin.topic_exists(dlq_topic(PLUGIN_EVENTS_TOPIC)) is True
    # RF=1 (single-node Redpanda), partitions>=1.
    assert all(rf == 1 for _, _, rf in admin.created)


@pytest.mark.asyncio
async def test_ensure_topic_second_check_refuses_silent_failure(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)

    class _LyingAdmin(FakeKafkaAdmin):
        async def create_topic(
            self, topic: str, *, num_partitions: int, replication_factor: int
        ) -> None:
            return  # «успешно» ничего не создали: повторная проверка обязана отказать

        async def topic_exists(self, topic: str) -> bool:
            return False

    with pytest.raises(PluginEventError) as exc:
        await ensure_topic(_LyingAdmin(), PLUGIN_EVENTS_TOPIC)
    assert exc.value.code is PluginEventErrorCode.TOPIC_NOT_ENSURED


@pytest.mark.asyncio
async def test_ensure_topic_failure_is_fail_closed(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    admin = KafkaTopicAdminAdapter(
        bootstrap_servers="kafka:9092", admin=FakeKafkaAdmin(fail_create=True)
    )
    await admin.start()
    with pytest.raises(PluginEventError) as exc:
        await ensure_topic(admin, PLUGIN_EVENTS_TOPIC)
    assert exc.value.code is PluginEventErrorCode.TOPIC_NOT_ENSURED
    with pytest.raises(PluginEventError):
        await ensure_dlq_topic(admin, PLUGIN_EVENTS_TOPIC)


@pytest.mark.asyncio
async def test_admin_refuses_rf_greater_than_one(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    admin = KafkaTopicAdminAdapter(bootstrap_servers="kafka:9092", admin=FakeKafkaAdmin())
    await admin.start()
    with pytest.raises(KafkaAdapterError):
        await admin.create_topic(PLUGIN_EVENTS_TOPIC, num_partitions=1, replication_factor=3)


# ---------------------------------------------------------------------------
# poll / commit / seek: коммит ТОЛЬКО после успеха
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_does_not_commit_on_empty_poll(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    fake = FakeKafkaConsumer(records=[])
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()
    assert await adapter.poll(timeout_ms=10) is None
    assert fake.commits == []
    assert fake.seeks == []


@pytest.mark.asyncio
async def test_consumer_poll_wraps_bytes_into_mapping(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    wire = make_event().to_wire()
    fake = FakeKafkaConsumer(records=[FakeConsumerRecord(value=json.dumps(wire).encode())])
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()
    record = await adapter.poll()
    assert record is not None
    assert isinstance(record, KafkaConsumedRecord)
    assert record.value["event_id"] == "evt-1"
    assert record.offset == 17
    assert fake.commits == []  # poll НЕ коммитит


@pytest.mark.asyncio
async def test_commit_is_explicit_and_only_after_success(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    fake = FakeKafkaConsumer(records=[FakeConsumerRecord(value=b'{}')])
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()
    record = await adapter.poll()
    assert record is not None
    assert fake.commits == []  # ДО успеха коммита нет
    await adapter.commit(record.offset)
    assert len(fake.commits) == 1
    committed = list(fake.commits[0].values())[0]
    assert committed.offset == record.offset + 1  # следующий читаемый offset


@pytest.mark.asyncio
async def test_commit_failure_is_propagated_and_seek_is_used(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    fake = FakeKafkaConsumer(records=[FakeConsumerRecord(value=b'{}')], fail_commit=True)
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()
    record = await adapter.poll()
    assert record is not None
    with pytest.raises(KafkaAdapterError):
        await adapter.commit(record.offset)
    await adapter.seek(record.offset)
    assert fake.seeks[-1][1] == record.offset


@pytest.mark.asyncio
async def test_dispatcher_does_not_commit_before_success(monkeypatch: Any) -> None:
    """Интеграция с PluginEventDispatcher: RETRY → seek, коммита нет."""
    _install_fake_aiokafka(monkeypatch)
    event = make_event()
    fake = FakeKafkaConsumer(records=[FakeConsumerRecord(value=json.dumps(event.to_wire()).encode())])
    consumer = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await consumer.start()

    class _FailingHandler:
        def __init__(self) -> None:
            self.calls: list[Any] = []
            self.timeline: list[str] = []

        async def handle(self, ev: Any, *, subscription: Any) -> None:
            self.calls.append(ev)
            self.timeline.append("handler")
            raise RuntimeError("backend 503")

    handler = _FailingHandler()
    ports = in_memory_event_ports(
        activator=FakeActivator(),
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(make_subscription(), active=True)

    class _Admin(FakeKafkaAdmin):
        def __init__(self) -> None:
            super().__init__()
            self.existing = set(REQUIRED_PLUGIN_TOPICS)

    dispatcher = PluginEventDispatcher(
        producer=FakeKafkaProducerPort(),
        consumer=consumer,
        topic_admin=_Admin(),
        dlq=_FakeDlq(),
        subscriptions=ports.subscriptions,
        published_events=ports.published_events,
        idempotency=ports.idempotency,
        audit=ports.audit,
        handler=handler,
        wake_hook=ports.wake_hook,
    )
    outcome = await dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.DLQ  # 3 RETRY → DLQ
    # Коммит ТОЛЬКО после подтверждённой записи в DLQ.
    assert len(fake.commits) == 1
    assert fake.seeks, "каждый RETRY обязан откатывать позицию через seek"
    assert len(handler.calls) == MAX_DELIVERY_ATTEMPTS


@pytest.mark.asyncio
async def test_wake_hook_is_called_before_handler(monkeypatch: Any) -> None:
    """WakeHook ДО handler (§7.3/§7.4) и до любого commit."""
    _install_fake_aiokafka(monkeypatch)
    timeline: list[str] = []
    event = make_event()
    fake = FakeKafkaConsumer(records=[FakeConsumerRecord(value=json.dumps(event.to_wire()).encode())])
    consumer = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await consumer.start()
    activator = FakeActivator()

    class _RecordingActivator:
        async def wake(self, instance_id: str, *, timeout: float | None = None) -> Any:
            timeline.append("wake")
            return await activator.wake(instance_id, timeout=timeout)

    class _Handler:
        async def handle(self, ev: Any, *, subscription: Any) -> None:
            timeline.append("handler")

    ports = in_memory_event_ports(
        activator=_RecordingActivator(),
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(make_subscription(), active=True)

    class _Admin(FakeKafkaAdmin):
        def __init__(self) -> None:
            super().__init__()
            self.existing = set(REQUIRED_PLUGIN_TOPICS)

    dispatcher = PluginEventDispatcher(
        producer=FakeKafkaProducerPort(),
        consumer=consumer,
        topic_admin=_Admin(),
        dlq=_FakeDlq(),
        subscriptions=ports.subscriptions,
        published_events=ports.published_events,
        idempotency=ports.idempotency,
        audit=ports.audit,
        handler=_Handler(),
        wake_hook=ports.wake_hook,
    )
    await dispatcher.consume(make_subscription())
    assert timeline[0] == "wake"
    assert timeline.index("wake") < timeline.index("handler")
    assert len(fake.commits) == 1


# ---------------------------------------------------------------------------
# Producer / DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_producer_partition_key_is_company_id_and_flush_is_called() -> None:
    raw = FakeKafkaProducer()
    producer = AiokafkaProducerAdapter(bootstrap_servers="kafka:9092", producer=raw)
    await producer.start()
    event = make_event()
    await producer.send(
        topic=PLUGIN_EVENTS_TOPIC,
        key=event.company_id,
        value=event.to_wire(),
        headers={"event_id": event.event_id},
    )
    await producer.flush()
    assert raw.sent[0]["key"] == b"co_42"
    wire = json.loads(raw.sent[0]["value"].decode("utf-8"))
    assert wire["payload_digest"] == TEST_DIGEST
    assert "payload" not in wire  # raw payload в шину не уходит (§4.5)
    assert raw.flushes == 1


@pytest.mark.asyncio
async def test_producer_failure_propagates_fail_closed() -> None:
    raw = FakeKafkaProducer(fail=True)
    producer = AiokafkaProducerAdapter(bootstrap_servers="kafka:9092", producer=raw)
    await producer.start()
    with pytest.raises(KafkaAdapterError):
        await producer.send(topic=PLUGIN_EVENTS_TOPIC, key="co_42", value={"event_id": "e"})


@pytest.mark.asyncio
async def test_dlq_publish_keeps_event_id_and_run_id_without_raw_payload() -> None:
    raw = FakeKafkaProducer()
    dlq = KafkaDlqAdapter(producer=raw, topic=PLUGIN_EVENTS_TOPIC)
    event = make_event()
    record = DlqRecord(
        event=event,
        subscription_id="sub_1",
        topic=PLUGIN_EVENTS_TOPIC,
        dlq_topic=dlq_topic(PLUGIN_EVENTS_TOPIC),
        attempts=MAX_DELIVERY_ATTEMPTS,
        error_kind="RuntimeError",
    )
    await dlq.publish(record)
    assert raw.sent[0]["topic"] == dlq.dlq_topic_name == "agentscale.plugin.events.dlq"
    assert raw.sent[0]["key"] == b"co_42"
    wire = json.loads(raw.sent[0]["value"].decode("utf-8"))
    assert wire["event_id"] == "evt-1"
    assert wire["run_id"] == "run_77"
    assert wire["payload_digest"] == TEST_DIGEST
    assert "payload" not in wire
    assert raw.flushes == 1  # запись подтверждена ДО коммита offset'а


# ---------------------------------------------------------------------------
# Вспомогательные двойники
# ---------------------------------------------------------------------------


class FakeKafkaProducerPort:
    async def send(self, *, topic: str, key: str, value: dict[str, Any], headers: Any = None) -> None:
        return None

    async def flush(self) -> None:
        return None


class _FakeDlq:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def publish(self, record: Any) -> None:
        self.records.append(record)


__all__: list[str] = []
