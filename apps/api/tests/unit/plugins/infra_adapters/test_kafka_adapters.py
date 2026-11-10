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

import asyncio
import hashlib
import json
import sys
import types
from typing import Any

import pytest

from prodavan.application.plugins.events.contracts import (
    CONSUMER_AUTO_OFFSET_RESET,
    CONSUMER_ENABLE_AUTO_COMMIT,
    MAX_DELIVERY_ATTEMPTS,
    DeliveryClaim,
    DeliveryOutcome,
    DlqRecord,
    IdempotencyKey,
    PluginEventErrorCode,
    consumer_config,
)
from prodavan.application.plugins.events.errors import PluginEventError
from prodavan.application.plugins.events.dispatch import (
    HmacPluginEventSigner,
    PluginEventDispatcher,
)
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
    FakeSecretStore,
    FakeNewTopic,
    make_event,
    make_subscription,
)


TEST_EVENT_SECRET_REF = "vault://unit-kafka/plugin-events"
TEST_EVENT_SIGNER = HmacPluginEventSigner(
    secret_store=FakeSecretStore(
        {TEST_EVENT_SECRET_REF: "unit-kafka-test-only-event-signing-key-32-bytes"}
    ),
    secret_ref=TEST_EVENT_SECRET_REF,
)


def _signed_json(event: Any) -> bytes:
    """Kafka bytes с обязательной подписью; plaintext fallback не используется."""

    return json.dumps(
        TEST_EVENT_SIGNER.signed_wire(event, topic=PLUGIN_EVENTS_TOPIC)
    ).encode("utf-8")


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


class _ClaimFake:
    """Test-only lease fake; production wiring uses RedisDeliveryClaimStore."""

    def __init__(self) -> None:
        self.active: dict[Any, Any] = {}
        self.completed: set[Any] = set()
        self.generation = 0

    async def acquire(self, key: IdempotencyKey, *, lease_seconds: float) -> Any:
        if key in self.active and key not in self.completed:
            return None
        self.generation += 1
        claim = DeliveryClaim(
            key=key,
            owner_token=f"test-owner-{self.generation}",
            generation=self.generation,
            lease_seconds=lease_seconds,
            completed=key in self.completed,
        )
        self.active[key] = claim
        return claim

    async def renew(self, claim: Any) -> bool:
        return self.active.get(claim.key) == claim

    async def complete(self, claim: Any) -> bool:
        if self.active.get(claim.key) != claim:
            return False
        self.completed.add(claim.key)
        return True

    async def release(self, claim: Any) -> bool:
        if self.active.get(claim.key) == claim:
            self.active.pop(claim.key, None)
            return True
        return False


def _build_dispatcher(
    *,
    consumer: Any,
    ports: Any,
    handler: Any,
    raw_dlq: Any = None,
    signer: Any = TEST_EVENT_SIGNER,
    claims: Any = None,
) -> Any:
    """Собрать dispatcher только с явными signer и durable RawDlq double."""

    class _Admin(FakeKafkaAdmin):
        def __init__(self) -> None:
            super().__init__()
            self.existing = set(REQUIRED_PLUGIN_TOPICS)

    return PluginEventDispatcher(
        producer=FakeKafkaProducerPort(),
        consumer=consumer,
        topic_admin=_Admin(),
        dlq=_FakeDlq(),
        raw_dlq=raw_dlq if raw_dlq is not None else _FakeRawDlq(),
        subscriptions=ports.subscriptions,
        published_events=ports.published_events,
        idempotency=ports.idempotency,
        delivery_claims=claims if claims is not None else _ClaimFake(),
        audit=ports.audit,
        handler=handler,
        wake_hook=ports.wake_hook,
        signer=signer,
    )


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
async def test_consumer_poll_timeout_preserves_pending_getone_and_late_record(
    monkeypatch: Any,
) -> None:
    _install_fake_aiokafka(monkeypatch)
    fake = FakeKafkaConsumer(block_poll=True)
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()

    assert await adapter.poll(timeout_ms=1) is None
    assert fake.getone_calls == 1
    assert fake.getone_cancellations == 0
    fake.release_poll(FakeConsumerRecord(value=b'{}'))
    record = await adapter.poll(timeout_ms=1000)
    assert record is not None and record.offset == 17
    assert fake.getone_calls == 1, "следующий poll обязан переиспользовать pending getone"
    assert fake.commits == []
    assert fake.seeks == []
    await adapter.close()


@pytest.mark.asyncio
async def test_consumer_close_cancels_pending_getone(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    fake = FakeKafkaConsumer(block_poll=True)
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()
    assert await adapter.poll(timeout_ms=1) is None

    await adapter.close()
    assert fake.getone_calls == 1
    assert fake.getone_cancellations == 1
    assert fake.commits == []
    assert fake.seeks == []


@pytest.mark.asyncio
async def test_consumer_poll_external_cancellation_is_not_timeout_or_adapter_error(
    monkeypatch: Any,
) -> None:
    _install_fake_aiokafka(monkeypatch)
    fake = FakeKafkaConsumer(block_poll=True)
    adapter = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=make_subscription(), consumer=fake
    )
    await adapter.start()
    task = asyncio.create_task(adapter.poll(timeout_ms=60_000))
    async with asyncio.timeout(1):
        while fake.getone_calls == 0:
            await asyncio.sleep(0)
    assert fake.getone_calls == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.getone_cancellations == 0
    fake.release_poll(FakeConsumerRecord(value=b'{}'))
    record = await adapter.poll(timeout_ms=1000)
    assert record is not None and record.offset == 17
    assert fake.getone_calls == 1
    assert fake.commits == []
    assert fake.seeks == []
    await adapter.close()


@pytest.mark.asyncio
async def test_consumer_poll_wraps_bytes_into_mapping(monkeypatch: Any) -> None:
    _install_fake_aiokafka(monkeypatch)
    wire = TEST_EVENT_SIGNER.signed_wire(
        make_event(), topic=PLUGIN_EVENTS_TOPIC
    )
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
    assert fake.getone_calls == 1  # реальная сигнатура без unsupported timeout_ms
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
    fake = FakeKafkaConsumer(
        records=[FakeConsumerRecord(value=_signed_json(event))]
    )
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

    dispatcher = _build_dispatcher(
        consumer=consumer, ports=ports, handler=handler
    )
    outcome = await dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.DLQ  # 2 RETRY → DLQ на 3-й попытке
    # Коммит ТОЛЬКО после подтверждённой записи в DLQ.
    assert len(fake.commits) == 1
    assert len(fake.seeks) == MAX_DELIVERY_ATTEMPTS - 1, (
        "каждый RETRY обязан откатывать позицию через seek"
    )
    assert len(handler.calls) == MAX_DELIVERY_ATTEMPTS


@pytest.mark.asyncio
async def test_wake_hook_is_called_before_handler(monkeypatch: Any) -> None:
    """WakeHook ДО handler (§7.3/§7.4) и до любого commit."""
    _install_fake_aiokafka(monkeypatch)
    timeline: list[str] = []
    event = make_event()
    fake = FakeKafkaConsumer(
        records=[FakeConsumerRecord(value=_signed_json(event))]
    )
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

    dispatcher = _build_dispatcher(
        consumer=consumer, ports=ports, handler=_Handler()
    )
    outcome = await dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.ACK
    assert timeline[0] == "wake"
    assert timeline.index("wake") < timeline.index("handler")
    assert len(fake.commits) == 1


@pytest.mark.asyncio
async def test_dispatcher_retry_then_ack_signed_record(monkeypatch: Any) -> None:
    """Первая ошибка даёт RETRY/seek без commit, вторая — ACK и один commit."""

    _install_fake_aiokafka(monkeypatch)
    event = make_event()
    fake = FakeKafkaConsumer(
        records=[FakeConsumerRecord(value=_signed_json(event))]
    )
    consumer = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092",
        subscription=make_subscription(),
        consumer=fake,
    )
    await consumer.start()
    activator = FakeActivator()

    class _RetryOnceHandler:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def handle(self, ev: Any, *, subscription: Any) -> None:
            self.calls.append(ev)
            if len(self.calls) == 1:
                raise RuntimeError("temporary backend 503")

    handler = _RetryOnceHandler()
    raw_dlq = _FakeRawDlq()
    ports = in_memory_event_ports(
        activator=activator,
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(make_subscription(), active=True)
    dispatcher = _build_dispatcher(
        consumer=consumer, ports=ports, handler=handler, raw_dlq=raw_dlq
    )

    outcome = await dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.ACK
    assert len(handler.calls) == 2, "RETRY повторяет тот же подписанный event"
    assert len(activator.wake_calls) == 2, "wake предшествует каждой попытке"
    assert len(fake.seeks) == 1, "первая попытка завершилась RETRY/seek"
    assert len(fake.commits) == 1, "commit разрешён только после ACK"
    assert raw_dlq.publish_calls == []


@pytest.mark.asyncio
async def test_malformed_kafka_record_goes_to_raw_dlq_then_commits(
    monkeypatch: Any,
) -> None:
    """Битые исходные bytes не становятся PluginEvent и уходят в raw DLQ."""

    _install_fake_aiokafka(monkeypatch)
    poison = b'{"event_id":"untrusted","payload":'
    fake = FakeKafkaConsumer(
        records=[FakeConsumerRecord(value=poison, partition=3, offset=91)]
    )
    subscription = make_subscription()
    consumer = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=subscription, consumer=fake
    )
    await consumer.start()
    activator = FakeActivator()

    class _Handler:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def handle(self, ev: Any, *, subscription: Any) -> None:
            self.calls.append(ev)

    handler = _Handler()
    raw_dlq = _FakeRawDlq()
    ports = in_memory_event_ports(
        activator=activator,
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(subscription, active=True)
    dispatcher = _build_dispatcher(
        consumer=consumer, ports=ports, handler=handler, raw_dlq=raw_dlq
    )

    outcome = await dispatcher.consume(subscription)

    assert outcome is DeliveryOutcome.DLQ
    assert len(fake.commits) == 1, "source offset коммитится после raw DLQ"
    assert fake.seeks == []
    assert handler.calls == []
    assert activator.wake_calls == []
    assert len(raw_dlq.records) == 1
    raw = raw_dlq.records[0]
    assert raw.topic == PLUGIN_EVENTS_TOPIC
    assert raw.partition == 3
    assert raw.offset == 91
    assert raw.consumer_group == subscription.consumer_group
    assert raw.error_kind == "JSONDecodeError"
    assert raw.payload_digest == f"sha256:{hashlib.sha256(poison).hexdigest()}"
    assert "payload" not in raw.to_wire()
    assert "event_id" not in raw.to_wire()


@pytest.mark.asyncio
async def test_dispatcher_missing_signer_fails_closed_before_poll(
    monkeypatch: Any,
) -> None:
    """Без signer нельзя читать, обрабатывать, DLQ'ить или коммитить запись."""

    _install_fake_aiokafka(monkeypatch)
    event = make_event()
    fake = FakeKafkaConsumer(
        records=[FakeConsumerRecord(value=_signed_json(event))]
    )
    subscription = make_subscription()
    consumer = AiokafkaConsumerAdapter(
        bootstrap_servers="kafka:9092", subscription=subscription, consumer=fake
    )
    await consumer.start()

    class _Handler:
        async def handle(self, ev: Any, *, subscription: Any) -> None:
            raise AssertionError("handler не должен вызываться без signer")

    raw_dlq = _FakeRawDlq()
    ports = in_memory_event_ports(
        activator=FakeActivator(),
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(subscription, active=True)
    dispatcher = _build_dispatcher(
        consumer=consumer,
        ports=ports,
        handler=_Handler(),
        raw_dlq=raw_dlq,
        signer=None,
    )

    with pytest.raises(PluginEventError) as exc:
        await dispatcher.consume(subscription)

    assert exc.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert fake.polls == 0
    assert fake.commits == []
    assert fake.seeks == []
    assert raw_dlq.publish_calls == []


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
        value=TEST_EVENT_SIGNER.signed_wire(event, topic=PLUGIN_EVENTS_TOPIC),
        headers={"event_id": event.event_id},
    )
    await producer.flush()
    assert raw.sent[0]["key"] == b"co_42"
    wire = json.loads(raw.sent[0]["value"].decode("utf-8"))
    assert wire["payload_digest"] == TEST_DIGEST
    assert wire["event_signature_version"] == "hmac-sha256-v1"
    assert wire["event_signature"].startswith("sha256=")
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


class _FakeRawDlq:
    """Test-only durable RawDlqPort: ack bool + dedup по natural key."""

    def __init__(self) -> None:
        self.records: list[Any] = []
        self.publish_calls: list[Any] = []
        self.seen_keys: set[Any] = set()

    async def publish(self, record: Any) -> bool:
        self.publish_calls.append(record)
        key = record.idempotency_key
        if key in self.seen_keys:
            return False
        self.seen_keys.add(key)
        self.records.append(record)
        return True


__all__: list[str] = []
