"""Real signed-event acceptance: ACK/wake, raw DLQ and durable duplicate ACK.

These tests are intentionally not executed in this migration-freeze change.
"""

from __future__ import annotations

import json
import uuid

import pytest

from prodavan.application.plugins.events.contracts import DeliveryOutcome

from .conftest import skip_without_endpoint

skip_without_endpoint()

pytestmark = [pytest.mark.integration, pytest.mark.kafka_plugin_acceptance]


@pytest.mark.asyncio
async def test_signed_event_is_woken_delivered_and_acknowledged(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    assert await harness.consume_next() is DeliveryOutcome.ACK

    assert harness.backend.attempts == 1
    assert len(harness.backend.observations) == 1
    observation = harness.backend.observations[0]
    assert observation.accepted is True
    assert observation.auth_ok is True
    assert observation.event_signature_ok is True
    assert observation.event_id == event.event_id
    assert harness.wake.wake_calls == 1
    assert harness.wake.wake_denials == 0
    assert harness.consumer.commits == [0]
    assert harness.consumer.seeks == []
    assert harness.raw_dlq._keys == set()
    assert harness.sql_state.audit.rows_written == 1
    claim = await harness.claim_snapshot(event)
    assert claim["completed"] is True
    assert claim["generation"] == 1
    assert claim["acquire_count"] == 1
    assert claim["complete_count"] == 1
    assert claim["release_count"] == 0
    assert claim["lease_exists"] is True
    assert claim["owned_key_count"] == 3
    assert harness.config.event_secret_ref != harness.config.claims_secret_ref


@pytest.mark.asyncio
async def test_unsigned_tampered_and_malformed_records_go_to_raw_dlq(harness) -> None:
    unsigned = harness.new_event()
    unsigned_wire = harness.signer.signed_wire(unsigned, topic=harness.identity.topic)
    unsigned_wire.pop("event_signature", None)
    unsigned_wire.pop("event_signature_version", None)
    await harness.publish_raw(json.dumps(unsigned_wire, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    tampered = harness.new_event()
    tampered_wire = harness.signer.signed_wire(tampered, topic=harness.identity.topic)
    tampered_wire["payload_digest"] = "sha256:" + uuid.uuid4().hex
    await harness.publish_raw(json.dumps(tampered_wire, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    malformed = b'{"event_id":"untrusted","payload":"' + harness.new_event().event_id.encode("ascii")
    harness.sensitive.add(malformed.decode("ascii", errors="ignore"))
    await harness.publish_raw(malformed)

    for _ in range(3):
        assert await harness.consume_next() is DeliveryOutcome.DLQ

    assert harness.consumer.commits == [0, 1, 2]
    assert harness.consumer.seeks == []
    assert harness.backend.attempts == 0
    assert harness.wake.wake_calls == 0

    records = await harness.read_dlq(3)
    harness.assert_safe_dlq(records)
    raw_records = [json.loads(bytes(record.value)) for record in records]
    assert all(record["consumer_group"] == harness.identity.consumer_group for record in raw_records)
    assert all(record["topic"] == harness.identity.topic for record in raw_records)
    assert all("event_id" not in record for record in raw_records)
    assert all("source_event_id" not in record for record in raw_records)
    assert len(harness.raw_dlq._keys) == 3
    assert harness.delivery_claims.owned_key_count == 0


@pytest.mark.asyncio
async def test_duplicate_delivery_acks_once_across_real_consumer_restart(harness) -> None:
    event = harness.new_event()
    signed = harness.signed_bytes(event)
    await harness.publish_raw(signed)
    await harness.publish_raw(signed)

    assert await harness.consume_next() is DeliveryOutcome.ACK
    assert harness.backend.attempts == 1
    assert harness.wake.wake_calls == 1
    assert harness.consumer.commits == [0]

    await harness.restart_consumer()
    assert await harness.consume_next() is DeliveryOutcome.ACK
    assert harness.backend.attempts == 1
    assert harness.wake.wake_calls == 1
    assert harness.consumer.commits == [1]
    assert harness.sql_state.audit.rows_written == 2
    claim = await harness.claim_snapshot(event)
    assert claim["completed"] is True
    assert claim["generation"] == 2
    assert claim["acquire_count"] == 2
    assert claim["complete_count"] == 1
    assert claim["release_count"] == 1
    assert claim["lease_exists"] is False
    assert claim["owned_key_count"] == 3
