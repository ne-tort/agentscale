"""Focused delivery ownership/lease tests; written, not executed."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from prodavan.application.plugins.events import (
    DeliveryClaimError,
    DeliveryOutcome,
    DeliveryOutcomeUncertainError,
    IdempotencyKey,
)

from .conftest import (
    TEST_EVENT_SIGNER,
    FakeDeliveryClaims,
    FakeHandler,
    build_harness,
    make_event,
    make_subscription,
    record_of,
)
from .test_integration_security import bound_event, bound_subscription, delivery


@pytest.mark.asyncio
async def test_claim_before_wake_and_atomic_revalidate_before_handler() -> None:
    timeline: list[str] = []

    class Claims(FakeDeliveryClaims):
        async def acquire(self, key: IdempotencyKey, *, lease_seconds: float) -> Any:
            timeline.append("acquire")
            return await super().acquire(key, lease_seconds=lease_seconds)

        async def renew(self, claim: Any) -> bool:
            timeline.append("renew")
            return await super().renew(claim)

    class Wake:
        async def wake(self, event: Any, *, subscription: Any) -> None:
            timeline.append("wake")

    class Handler:
        event_signer = TEST_EVENT_SIGNER

        async def handle(self, event: Any, *, subscription: Any) -> None:
            timeline.append("handler")

    h = build_harness(
        records=[record_of(make_event(), offset=1)],
        claims=Claims(),
        wake=Wake(),
        handler=Handler(),
    )
    assert await h.dispatcher.consume(make_subscription()) is DeliveryOutcome.ACK
    assert timeline[:4] == ["acquire", "wake", "renew", "handler"]
    assert h.consumer.commits == [1]


@pytest.mark.asyncio
async def test_rebalance_between_wake_and_handler_blocks_side_effect_and_commit() -> None:
    class RebalancingWake:
        def __init__(self) -> None:
            self.claims: FakeDeliveryClaims | None = None

        async def wake(self, event: Any, *, subscription: Any) -> None:
            assert self.claims is not None
            self.claims.lose_next_renew = True

    wake = RebalancingWake()
    h = build_harness(records=[record_of(make_event(), offset=2)], wake=wake)
    wake.claims = h.claims

    with pytest.raises(DeliveryClaimError) as caught:
        await h.dispatcher.consume(make_subscription())

    assert caught.value.retryable is True
    assert caught.value.side_effects_uncertain is False
    assert h.handler.calls == []
    assert h.consumer.commits == []
    assert h.consumer.seeks == [2]
    assert h.dlq.records == []
    assert h.idempotency.remembered == []


@pytest.mark.asyncio
async def test_revoked_partition_seek_failure_preserves_claim_error_and_replays() -> None:
    class RevokeAfterWake:
        def __init__(self) -> None:
            self.consumer: Any = None
            self.first = True

        async def wake(self, event: Any, *, subscription: Any) -> None:
            if not self.first:
                return
            self.first = False
            assert self.consumer is not None
            self.consumer.ownership = False

    wake = RevokeAfterWake()
    event = make_event()
    h = build_harness(records=[record_of(event, offset=12)], wake=wake)
    wake.consumer = h.consumer
    h.consumer.fail_seek = True

    with pytest.raises(DeliveryClaimError) as caught:
        await h.dispatcher.consume(make_subscription())

    assert caught.value.retryable is True
    assert caught.value.side_effects_uncertain is False
    assert h.handler.calls == []
    assert h.consumer.commits == []
    assert h.consumer.seeks == [12]
    assert h.dlq.records == []
    assert h.raw_dlq.publish_calls == []

    h.consumer.fail_seek = False
    h.consumer.ownership = True
    h.claims.active.clear()
    h.consumer.records.append(record_of(event, offset=12))
    assert await h.dispatcher.consume(make_subscription()) is DeliveryOutcome.ACK
    assert len(h.handler.calls) == 1
    assert h.consumer.commits == [12]


@pytest.mark.asyncio
async def test_assignment_owned_true_to_false_after_wake_retries_with_new_owner_once() -> None:
    handler, _, _, proxy = delivery()
    subscription = bound_subscription()
    event = bound_event()

    class LoseAssignmentAfterWake:
        def __init__(self) -> None:
            self.consumer: Any = None
            self.first = True

        async def wake(self, event: Any, *, subscription: Any) -> None:
            await handler.wake(event, subscription=subscription)
            if not self.first:
                return
            self.first = False
            assert self.consumer is not None
            assert self.consumer.ownership is True
            self.consumer.ownership = False

        def abort(self, event: Any, *, subscription: Any) -> None:
            handler.abort(event, subscription=subscription)

    wake = LoseAssignmentAfterWake()
    h = build_harness(
        records=[record_of(event, offset=11)],
        subscription=subscription,
        handler=handler,
        wake=wake,
    )
    wake.consumer = h.consumer

    with pytest.raises(DeliveryClaimError) as caught:
        await h.dispatcher.consume(subscription)

    assert caught.value.retryable is True
    assert caught.value.side_effects_uncertain is False
    assert proxy.calls == []
    assert h.consumer.commits == []
    assert h.consumer.seeks == [11]
    assert h.dlq.records == []
    assert h.raw_dlq.publish_calls == []
    assert h.idempotency.remembered == []
    assert handler._pending.get() is None

    # The abandoned owner's lease expires; replay must acquire a new claim owner
    # while the restored Kafka assignment passes the same production fence.
    h.claims.active.clear()
    h.consumer.ownership = True
    h.consumer.records.append(record_of(event, offset=11))
    assert await h.dispatcher.consume(subscription) is DeliveryOutcome.ACK

    assert h.claims.generation == 2
    assert len(proxy.calls) == 1
    assert h.consumer.commits == [11]
    assert h.consumer.seeks == [11]
    assert h.dlq.records == []
    assert h.raw_dlq.publish_calls == []
    assert handler._pending.get() is None


@pytest.mark.asyncio
async def test_abandoned_wake_is_aborted_before_valid_retry() -> None:
    class AbortableWake:
        def __init__(self) -> None:
            self.pending: Any = None
            self.abort_calls: list[Any] = []
            self.first = True
            self.claims: FakeDeliveryClaims | None = None

        async def wake(self, event: Any, *, subscription: Any) -> None:
            self.pending = (event.event_id, subscription.id)
            if self.first:
                self.first = False
                assert self.claims is not None
                self.claims.lose_next_renew = True

        def abort(self, event: Any, *, subscription: Any) -> None:
            if self.pending == (event.event_id, subscription.id):
                self.pending = None
                self.abort_calls.append((event.event_id, subscription.id))

    wake = AbortableWake()
    event = make_event()
    h = build_harness(records=[record_of(event, offset=9)], wake=wake)
    wake.claims = h.claims
    with pytest.raises(DeliveryClaimError):
        await h.dispatcher.consume(make_subscription())
    assert wake.abort_calls == [(event.event_id, "sub_1")]
    assert wake.pending is None

    h.consumer.records.append(record_of(event, offset=9))
    assert await h.dispatcher.consume(make_subscription()) is DeliveryOutcome.ACK
    assert len(h.handler.calls) == 1
    assert h.consumer.commits == [9]


@pytest.mark.asyncio
async def test_concurrent_consumers_have_only_one_backend_side_effect() -> None:
    claims = FakeDeliveryClaims()
    entered = asyncio.Event()
    release = asyncio.Event()
    event = make_event()

    class GatedHandler:
        event_signer = TEST_EVENT_SIGNER

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def handle(self, event: Any, *, subscription: Any) -> None:
            self.calls.append(event)
            entered.set()
            await release.wait()

    handler = GatedHandler()
    first = build_harness(
        records=[record_of(event, offset=3)],
        claims=claims,
        handler=handler,
    )
    second = build_harness(
        records=[record_of(event, offset=3)],
        claims=claims,
    )
    task = asyncio.create_task(first.dispatcher.consume(make_subscription()))
    await entered.wait()

    with pytest.raises(DeliveryClaimError):
        await second.dispatcher.consume(make_subscription())
    assert second.handler.calls == []
    assert second.consumer.commits == []
    assert second.consumer.seeks == [3]

    release.set()
    assert await task is DeliveryOutcome.ACK
    assert len(handler.calls) == 1
    assert first.consumer.commits == [3]


@pytest.mark.asyncio
async def test_lost_renewal_during_handler_is_uncertain_not_retry_or_dlq() -> None:
    entered = asyncio.Event()

    class GatedHandler:
        event_signer = TEST_EVENT_SIGNER

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def handle(self, event: Any, *, subscription: Any) -> None:
            self.calls.append(event)
            entered.set()
            await asyncio.Event().wait()

    handler = GatedHandler()
    h = build_harness(
        records=[record_of(make_event(), offset=4)],
        handler=handler,
        delivery_lease_seconds=0.01,
    )
    task = asyncio.create_task(h.dispatcher.consume(make_subscription()))
    await entered.wait()
    h.claims.lose_next_renew = True

    with pytest.raises(DeliveryOutcomeUncertainError) as caught:
        await task

    assert caught.value.retryable is False
    assert caught.value.side_effects_uncertain is True
    assert len(handler.calls) == 1, "no automatic backend retry after uncertainty"
    assert h.consumer.commits == []
    assert h.consumer.seeks == [4]
    assert h.idempotency.remembered == []
    assert h.dlq.records == []
    assert h.raw_dlq.records == []


@pytest.mark.asyncio
async def test_heartbeat_renews_ttl_during_long_handler() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class GatedHandler:
        event_signer = TEST_EVENT_SIGNER

        async def handle(self, event: Any, *, subscription: Any) -> None:
            entered.set()
            await release.wait()

    h = build_harness(
        records=[record_of(make_event(), offset=5)],
        handler=GatedHandler(),
        delivery_lease_seconds=0.01,
    )
    task = asyncio.create_task(h.dispatcher.consume(make_subscription()))
    await entered.wait()
    await asyncio.sleep(0.025)
    assert h.claims.calls.count("renew") >= 2
    release.set()
    assert await task is DeliveryOutcome.ACK
    assert h.consumer.commits == [5]


@pytest.mark.asyncio
async def test_heartbeat_renews_ttl_during_long_wake_and_leaves_no_pending() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    handler, _, activator, proxy = delivery()
    subscription = bound_subscription()
    event = bound_event()
    original_wake = activator.wake

    async def gated_wake(instance_id: str, *, timeout: float | None = None) -> Any:
        entered.set()
        await release.wait()
        return await original_wake(instance_id, timeout=timeout)

    activator.wake = gated_wake  # type: ignore[method-assign]
    h = build_harness(
        records=[record_of(event, offset=12)],
        subscription=subscription,
        handler=handler,
        wake=handler,
        delivery_lease_seconds=0.01,
    )
    pending_after_delivery: list[Any] = []

    async def deliver() -> DeliveryOutcome | None:
        try:
            return await h.dispatcher.consume(subscription)
        finally:
            # Capture in the subscription task's ContextVar context, not the
            # parent test context, so stale pending state cannot hide here.
            pending_after_delivery.append(handler._pending.get())

    task = asyncio.create_task(deliver())
    await entered.wait()
    await asyncio.sleep(0.025)
    renewals_during_wake = h.claims.calls.count("renew")
    backend_calls_during_wake = len(proxy.calls)
    release.set()

    assert await task is DeliveryOutcome.ACK
    assert renewals_during_wake >= 2
    assert backend_calls_during_wake == 0
    assert len(proxy.calls) == 1
    assert h.consumer.commits == [12]
    assert h.dlq.records == []
    assert h.raw_dlq.publish_calls == []
    assert pending_after_delivery == [None]


@pytest.mark.asyncio
async def test_completed_claim_dedups_backend_even_when_sql_marker_needs_repair() -> None:
    event = make_event(source_event_id="source-stable")
    h = build_harness(records=[record_of(event, offset=6)])
    assert await h.dispatcher.consume(make_subscription()) is DeliveryOutcome.ACK
    assert len(h.handler.calls) == 1

    # Simulate a completed Redis claim surviving while the separate SQL marker
    # is missing. Replay must repair SQL without another backend side effect.
    h.idempotency.seen_keys.clear()
    h.idempotency.remembered.clear()
    h.consumer.records.append(record_of(event, offset=7))
    assert await h.dispatcher.consume(make_subscription()) is DeliveryOutcome.ACK

    assert len(h.handler.calls) == 1
    assert h.idempotency.remembered == [IdempotencyKey("sub_1", "source-stable")]
    assert h.consumer.commits == [6, 7]
    assert h.audit.entries[-1][1]["duplicate"] is True


@pytest.mark.asyncio
async def test_missing_durable_claim_port_fails_before_wake_and_handler() -> None:
    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    h = build_harness(records=[record_of(make_event(), offset=8)])
    dispatcher = PluginEventDispatcher(
        producer=h.producer,
        consumer=h.consumer,
        topic_admin=h.admin,
        dlq=h.dlq,
        raw_dlq=h.raw_dlq,
        subscriptions=h.subscriptions,
        published_events=h.published,
        idempotency=h.idempotency,
        audit=h.audit,
        handler=h.handler,
        wake_hook=h.wake,
    )
    from prodavan.application.plugins.events import PluginEventError, PluginEventErrorCode

    with pytest.raises(PluginEventError) as caught:
        await dispatcher.consume(make_subscription())
    assert caught.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert h.handler.calls == []
    assert h.wake.wakes == []
    assert h.consumer.commits == []
    assert h.consumer.seeks == []
    assert h.consumer.polls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_value", [b"not-json", {"event_id": "unsigned"}])
async def test_missing_claims_rejected_before_malformed_or_unsigned_delivery(wire_value: Any) -> None:
    from prodavan.application.plugins.events import PluginEventError, PluginEventErrorCode
    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    from .conftest import FakeRecord

    h = build_harness(records=[FakeRecord(value=wire_value, offset=10)])
    dispatcher = PluginEventDispatcher(
        producer=h.producer,
        consumer=h.consumer,
        topic_admin=h.admin,
        dlq=h.dlq,
        raw_dlq=h.raw_dlq,
        subscriptions=h.subscriptions,
        published_events=h.published,
        idempotency=h.idempotency,
        audit=h.audit,
        handler=h.handler,
        wake_hook=h.wake,
    )
    with pytest.raises(PluginEventError) as caught:
        await dispatcher.consume(make_subscription())
    assert caught.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert h.consumer.polls == 0
    assert h.consumer.commits == []
    assert h.raw_dlq.publish_calls == []
    assert h.dlq.records == []
