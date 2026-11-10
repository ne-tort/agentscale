"""Real Kafka signed-event production and independent HMAC verification."""
from __future__ import annotations

import asyncio
import json
import ssl
import uuid

import httpx
import pytest
from aiokafka import AIOKafkaConsumer

from prodavan.application.plugins.events.contracts import PluginEvent
from prodavan.application.plugins.events.dispatch import (
    EVENT_SIGNATURE_FIELD,
    EVENT_SIGNATURE_VERSION,
    EVENT_SIGNATURE_VERSION_FIELD,
    HmacPluginEventSigner,
)
from prodavan.application.plugins.events.topics import PLUGIN_EVENTS_TOPIC
from prodavan.infrastructure.plugins.capabilities.kafka import KafkaSecurityConfig
from prodavan.infrastructure.secrets.file_store import FileSecretStore

from .http_support import allowed, digest_payload, request
from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


async def test_signed_event_is_real_kafka_wire_and_hmac_verified(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    security = KafkaSecurityConfig(
        sasl_username=world.config.kafka_sasl_username,
        sasl_password=world.config.kafka_sasl_password,
        ssl_context=ssl.create_default_context(cafile=world.config.kafka_tls_ca_file),
        sasl_mechanism=world.config.kafka_sasl_mechanism,
    )
    consumer = AIOKafkaConsumer(
        PLUGIN_EVENTS_TOPIC,
        bootstrap_servers=world.config.kafka_bootstrap_servers,
        client_id="entrypoint-observer-" + uuid.uuid4().hex[:12],
        group_id="entrypoint-observer-" + uuid.uuid4().hex,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda raw: raw,
        **security.as_aiokafka_kwargs(),
    )
    await consumer.start()
    try:
        _raw, payload_digest = digest_payload({
            "acceptance_id": uuid.uuid4().hex,
            "message": "signed-event-real-kafka",
        })
        published = allowed(await request(
            http_client,
            "POST",
            world.api_url + "/capabilities/v1/events/publish",
            token=world.state.token,
            payload={
                "topic": PLUGIN_EVENTS_TOPIC,
                "event_type": "acceptance.signed",
                "partition_key": world.config.company_id,
                "payload_digest": payload_digest,
            },
        ))
        assert published["event_id"].startswith("evt_")
        assert published["partition"] == 0

        wire = None
        async with asyncio.timeout(world.config.operation_timeout_seconds):
            while wire is None:
                record = await consumer.getone()
                candidate = json.loads(bytes(record.value).decode("utf-8"))
                if candidate.get("event_id") == published["event_id"]:
                    assert record.partition == 0
                    wire = candidate
        assert wire[EVENT_SIGNATURE_VERSION_FIELD] == EVENT_SIGNATURE_VERSION
        event = PluginEvent.from_wire(wire)
        signer = HmacPluginEventSigner(
            secret_store=FileSecretStore(root=world.secret_root),
            secret_ref=world.environment["PLUGIN_EVENT_SIGNING_SECRET_REF"],
        )
        signer.validate()
        assert signer.verify(
            event,
            topic=PLUGIN_EVENTS_TOPIC,
            signature=wire[EVENT_SIGNATURE_FIELD],
        ) is True
        assert event.company_id == world.config.company_id
        assert event.plugin_id == world.config.plugin_id
        assert event.payload_digest == payload_digest
        assert signer.signed_wire(event, topic=PLUGIN_EVENTS_TOPIC)[EVENT_SIGNATURE_FIELD] == wire[
            EVENT_SIGNATURE_FIELD
        ]
        tampered = dict(wire)
        tampered["payload_digest"] = "sha256:" + "f" * 64
        assert signer.verify(
            PluginEvent.from_wire(tampered),
            topic=PLUGIN_EVENTS_TOPIC,
            signature=wire[EVENT_SIGNATURE_FIELD],
        ) is False
    finally:
        await consumer.stop()
