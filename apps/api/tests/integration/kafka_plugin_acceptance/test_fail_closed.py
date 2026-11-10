"""Unknown subscription/topic, revoked subscription/grant and key fail-closed.

No skip, fake endpoint or fallback success is introduced by these cases.
"""

from __future__ import annotations

import json
import uuid

import pytest

from prodavan.application.plugins.events.contracts import DeliveryOutcome, Subscription
from prodavan.application.plugins.events.dispatch import HmacPluginEventSigner
from prodavan.application.plugins.events.errors import PluginEventError, PluginEventErrorCode

from .conftest import skip_without_endpoint

skip_without_endpoint()

pytestmark = [pytest.mark.integration, pytest.mark.kafka_plugin_acceptance]


@pytest.mark.asyncio
async def test_unknown_subscription_fails_closed_before_poll(harness) -> None:
    unknown = Subscription(
        id=f"{harness.identity.company_id}:{harness.identity.plugin_instance_id}:missing-{uuid.uuid4().hex[:8]}",
        plugin_id=harness.identity.plugin_id,
        event_types=harness.subscription.event_types,
        consumer_group=harness.identity.consumer_group,
    )
    with pytest.raises(PluginEventError) as caught:
        await harness.consume_next(subscription=unknown)
    assert caught.value.code is PluginEventErrorCode.UNKNOWN_SUBSCRIPTION
    assert harness.consumer.polls == 0
    assert harness.consumer.commits == []
    assert harness.consumer.seeks == []
    assert harness.backend.attempts == 0
    assert harness.wake.wake_calls == 0
    assert harness.delivery_claims.owned_key_count == 0


@pytest.mark.asyncio
async def test_revoked_subscription_fails_closed_before_poll(harness) -> None:
    assert await harness.sql_state.subscriptions.revoke(harness.subscription.id) is True
    with pytest.raises(PluginEventError) as caught:
        await harness.consume_next()
    assert caught.value.code is PluginEventErrorCode.UNKNOWN_SUBSCRIPTION
    assert harness.consumer.polls == 0
    assert harness.consumer.commits == []
    assert harness.backend.attempts == 0
    assert harness.wake.wake_calls == 0
    assert harness.delivery_claims.owned_key_count == 0


@pytest.mark.asyncio
async def test_revoked_grant_retries_then_dlq_without_wake_or_backend(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    revoked = await harness.sql_state.grants.deactivate(grant_id=harness.identity.grant_id)
    assert revoked.is_active is False
    assert revoked.revoked_at is not None

    assert await harness.consume_next() is DeliveryOutcome.DLQ
    assert harness.wake.wake_calls == 0
    assert harness.wake.wake_denials == 0
    assert harness.backend.attempts == 0
    assert harness.consumer.seeks == [0, 0]
    assert harness.consumer.commits == [0]

    records = await harness.read_dlq(1)
    harness.assert_safe_dlq(records)
    record = json.loads(bytes(records[0].value))
    assert record["event_id"] == event.event_id
    assert record["source_event_id"] == event.source_id
    assert record["attempts"] == 3
    assert record["dlq_topic"] == harness.identity.dlq_topic
    assert record["error_kind"] == "PluginEventError"
    claim = await harness.claim_snapshot(event)
    assert claim["completed"] is False
    assert claim["generation"] == 1
    assert claim["acquire_count"] == 1
    assert claim["complete_count"] == 0
    assert claim["release_count"] == 1
    assert claim["lease_exists"] is False


@pytest.mark.asyncio
async def test_unknown_topic_is_seeked_and_never_reaches_dlq_or_handler(harness) -> None:
    event = harness.new_event()
    await harness.publish_raw(
        harness.signed_bytes(event),
        topic=harness.identity.unknown_topic,
    )
    await harness.replace_consumer_with_topics(harness.identity.topic, harness.identity.unknown_topic)

    with pytest.raises(PluginEventError) as caught:
        await harness.consume_next()
    assert caught.value.code is PluginEventErrorCode.INVALID_EVENT
    assert harness.consumer.seeks == [0]
    assert harness.consumer.commits == []
    assert harness.raw_dlq._keys == set()
    assert harness.backend.attempts == 0
    assert harness.wake.wake_calls == 0
    assert harness.delivery_claims.owned_key_count == 0


@pytest.mark.asyncio
async def test_missing_event_signing_key_fails_closed_before_kafka_poll(harness) -> None:
    missing_ref = f"file://missing/{uuid.uuid4().hex}"
    with pytest.raises(PluginEventError) as caught:
        signer = HmacPluginEventSigner(
            secret_store=harness.secret_store,
            secret_ref=missing_ref,
        )
        signer.validate()
    assert caught.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert harness.consumer.polls == 0
    assert harness.consumer.commits == []
    assert harness.consumer.seeks == []
    assert harness.backend.attempts == 0
    assert harness.wake.wake_calls == 0
    assert harness.delivery_claims.owned_key_count == 0


@pytest.mark.asyncio
async def test_missing_delivery_claims_denied_before_poll(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    dispatcher = harness.dispatcher_without_delivery_claims()

    with pytest.raises(PluginEventError) as caught:
        await harness.consume_next(dispatcher=dispatcher)

    assert caught.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert harness.consumer.polls == 0
    assert harness.consumer.seeks == []
    assert harness.consumer.commits == []
    assert harness.backend.attempts == 0
    assert harness.wake.wake_calls == 0
    assert harness.delivery_claims.owned_key_count == 0
    assert harness.raw_dlq._keys == set()
