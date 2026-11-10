from __future__ import annotations

from types import SimpleNamespace

import pytest

from prodavan.application.plugins.events.topics import PLUGIN_EVENTS_TOPIC, dlq_topic
from prodavan.infrastructure.plugins.adapters.build import build_kafka_event_adapters

from .production_boundaries import (
    StatefulKafkaAdmin,
    StatefulKafkaConsumer,
    StatefulKafkaProducer,
)

pytestmark = pytest.mark.integration


class RawDlqDouble:
    async def publish(self, record: object) -> bool:
        return True


class ClaimsDouble:
    async def acquire(self, key: object, *, lease_seconds: float) -> object | None:
        return None

    async def renew(self, claim: object) -> bool:
        return True

    async def complete(self, claim: object) -> bool:
        return True

    async def release(self, claim: object) -> bool:
        return True


@pytest.mark.asyncio
async def test_kafka_factory_uses_real_adapters_and_explicit_topic_lifecycle() -> None:
    producer = StatefulKafkaProducer()
    consumer = StatefulKafkaConsumer()
    admin = StatefulKafkaAdmin()
    await producer.start()
    adapters = await build_kafka_event_adapters(
        bootstrap_servers="redpanda-test-only:9092",
        subscription=SimpleNamespace(consumer_group="composition-subscription"),
        producer=producer,
        consumer=consumer,
        admin=admin,
        raw_dlq=RawDlqDouble(),
        delivery_claims=ClaimsDouble(),
    )
    assert admin.topics == {PLUGIN_EVENTS_TOPIC, dlq_topic(PLUGIN_EVENTS_TOPIC)}
    assert adapters.consumer.group_id == "composition-subscription"
    assert adapters.consumer.settings["auto_offset_reset"] == "earliest"
    assert adapters.consumer.settings["enable_auto_commit"] is False
    assert adapters.consumer.start_kwargs["group_id"] == "composition-subscription"
    assert adapters.consumer.start_kwargs["enable_auto_commit"] is False

    await adapters.producer.send(
        topic=PLUGIN_EVENTS_TOPIC,
        key="co_acceptance",
        value={"event_id": "evt_composition", "payload_digest": "sha256:" + "a" * 64},
    )
    await adapters.producer.flush()
    assert producer.flushes == 1
    assert len(producer.messages) == 1
    message = producer.messages[0]
    assert message["topic"] == PLUGIN_EVENTS_TOPIC
    assert message["key"] == b"co_acceptance"

    await adapters.close()
    await producer.stop()
    await consumer.stop()
    await admin.close()
    assert producer.started is False
    assert consumer.stopped is True
    assert admin.closed is True
