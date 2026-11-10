"""Real retry/seek and consumer-group rebalance acceptance.

All rebalance cases are mandatory. A stale backend side effect, a masked
ownership error, or a failed recovery is an ordinary test failure, never xfail.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from prodavan.application.plugins.events.contracts import (
    DeliveryClaimError,
    DeliveryOutcome,
    IdempotencyKey,
)
from prodavan.application.plugins.events.errors import PluginEventError
from prodavan.infrastructure.plugins.adapters.kafka_adapters import (
    AiokafkaConsumerAdapter,
    KafkaAdapterError,
)

from .conftest import skip_without_endpoint

skip_without_endpoint()

pytestmark = [pytest.mark.integration, pytest.mark.kafka_plugin_acceptance]


@pytest.mark.asyncio
async def test_retry_seek_then_ack_uses_real_backend_and_manual_commit(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    harness.backend.fail_next(2)

    assert await harness.consume_next() is DeliveryOutcome.ACK
    assert harness.backend.attempts == 3
    assert len([item for item in harness.backend.observations if item.accepted]) == 1
    assert harness.wake.wake_calls == 3
    assert harness.consumer.seeks == [0, 0]
    assert harness.consumer.commits == [0]
    assert harness.raw_dlq._keys == set()
    assert harness.sql_state.audit.rows_written == 1
    claim = await harness.claim_snapshot(event)
    assert claim["completed"] is True
    assert claim["generation"] == 1
    assert claim["acquire_count"] == 1
    assert claim["complete_count"] == 1
    assert claim["release_count"] == 0
    assert claim["lease_exists"] is True


@pytest.mark.asyncio
async def test_three_handler_failures_write_valid_dlq_before_commit(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    harness.backend.fail_next(3)

    assert await harness.consume_next() is DeliveryOutcome.DLQ
    assert harness.backend.attempts == 3
    assert harness.wake.wake_calls == 3
    assert harness.consumer.seeks == [0, 0]
    assert harness.consumer.commits == [0]
    assert harness.raw_dlq._keys == set()

    records = await harness.read_dlq(1)
    harness.assert_safe_dlq(records)
    record = json.loads(bytes(records[0].value))
    assert record["event_id"] == event.event_id
    assert record["run_id"] == event.run_id
    assert record["attempts"] == 3
    assert record["error_kind"] == "PluginEventError"
    assert record["topic"] == harness.identity.topic
    claim = await harness.claim_snapshot(event)
    assert claim["completed"] is False
    assert claim["generation"] == 1
    assert claim["acquire_count"] == 1
    assert claim["complete_count"] == 0
    assert claim["release_count"] == 1
    assert claim["lease_exists"] is False


@pytest.mark.asyncio
async def test_rebalance_after_delivery_before_commit_replays_without_handler_duplicate(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    harness.backend.gate_next_request()

    first_task = asyncio.create_task(harness.consume_next())
    await harness.backend.wait_for_gated_request()
    secondary = await harness.create_secondary_dispatcher()
    # A second member is not guaranteed to win the only partition. Close the
    # gated first member to force a real LeaveGroup/rebalance deterministically,
    # then verify loss on its actual raw client before releasing the gate.
    await harness.consumer.close()
    await _wait_until_partition_lost(
        harness, harness.consumer, successor=secondary._consumer
    )

    await harness.backend.release_gated_request()
    with pytest.raises((PluginEventError, KafkaAdapterError)):
        await first_task
    assert harness.consumer.commits == []

    # The first real backend response happened before the failed commit. The
    # durable SQL idempotency row is already present, so replay through the new
    # owner is an ACK without a second wake or backend call.
    assert await harness.consume_next(dispatcher=secondary) is DeliveryOutcome.ACK
    assert harness.backend.attempts == 1
    assert harness.wake.wake_calls == 1
    assert secondary._consumer.commits == [0]
    assert harness.raw_dlq._keys == set()
    claim = await harness.claim_snapshot(event)
    assert claim["completed"] is True
    assert claim["generation"] == 2
    assert claim["acquire_count"] == 2
    assert claim["complete_count"] == 1
    assert claim["release_count"] == 1
    assert claim["lease_exists"] is False


@pytest.mark.asyncio
async def test_rebalance_after_wake_before_handler_blocks_stale_delivery_then_recovers(harness) -> None:
    event = harness.new_event()
    assert await harness.publish_valid(event) is True
    harness.wake.gate_after_wake()

    first_task = asyncio.create_task(harness.consume_next())
    await harness.wake.wait_for_gated_wake()
    secondary = await harness.create_secondary_dispatcher()
    # A second member is not guaranteed to win the only partition. Close the
    # gated first member to force a real LeaveGroup/rebalance deterministically,
    # then verify loss on its actual raw client before releasing the gate.
    await harness.consumer.close()
    await _wait_until_partition_lost(
        harness, harness.consumer, successor=secondary._consumer
    )

    # The real WakeResult already exists, but handler.handle has not started.
    # Releasing it now must hit the public ConsumerOwnershipPort fence before
    # any backend side effect. A different exception type is a normal failure.
    await harness.wake.release_gated_wake()
    with pytest.raises(DeliveryClaimError) as caught:
        await first_task
    assert caught.value.retryable is True
    assert caught.value.side_effects_uncertain is False
    assert harness.wake.wake_calls == 1
    assert harness.wake.wake_denials == 0
    assert harness.backend.attempts == 0
    assert harness.consumer.commits == []
    assert harness.valid_dlq_count == 0
    assert harness.raw_dlq._keys == set()

    before_recovery = await harness.claim_snapshot(event)
    assert before_recovery["completed"] is False
    assert before_recovery["generation"] == 1
    assert before_recovery["acquire_count"] == 1
    assert before_recovery["complete_count"] == 0
    idem_key = IdempotencyKey(
        subscription_id=harness.identity.subscription_id,
        source_event_id=event.source_id,
    )
    assert await harness.sql_state.idempotency.seen(idem_key) is False

    # Release only the abandoned owner token through the production store.
    # This does not fabricate completion: the new owner must acquire generation2
    # and perform the sole real backend delivery before its own ACK/commit.
    assert await harness.release_abandoned_claim_for(event) is True
    assert await harness.consume_next(dispatcher=secondary) is DeliveryOutcome.ACK
    assert harness.backend.attempts == 1
    assert len(harness.backend.observations) == 1
    assert harness.backend.observations[0].accepted is True
    assert harness.wake.wake_calls == 2
    assert secondary._consumer.commits == [0]
    assert harness.valid_dlq_count == 0
    assert harness.raw_dlq._keys == set()

    recovered = await harness.claim_snapshot(event)
    assert recovered["completed"] is True
    assert recovered["generation"] == 2
    assert recovered["acquire_count"] == 2
    assert recovered["complete_count"] == 1
    assert recovered["release_count"] == 1
    assert recovered["lease_exists"] is True
    assert recovered["owned_key_count"] == 3


async def _wait_until_partition_lost(
    harness,
    adapter: AiokafkaConsumerAdapter,
    *,
    successor: AiokafkaConsumerAdapter | None = None,
) -> None:
    raw = adapter._consumer
    async with asyncio.timeout(harness.config.rebalance_timeout_seconds):
        if raw is None:
            # stop() may retain the last raw assignment snapshot. The adapter
            # fence is already closed; require real assignment to the successor.
            if successor is None:
                return
            while True:
                successor_raw = successor._consumer
                if successor_raw is not None and successor_raw.assignment():
                    return
                await asyncio.sleep(0.1)
        while raw.assignment():
            await asyncio.sleep(0.1)
