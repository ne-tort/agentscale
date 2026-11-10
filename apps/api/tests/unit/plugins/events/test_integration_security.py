"""Signed event transport and exact activator binding tests (not executed)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
import json

import pytest

from prodavan.application.plugins.events.contracts import DeliveryOutcome, IdempotencyKey, Subscription, backend_idempotency_key
from prodavan.application.plugins.events.dispatch import (
    ActivatingPluginEventHandler,
    HmacPluginEventSigner,
)
from prodavan.application.plugins.events.errors import PluginEventError, PluginEventErrorCode
from .conftest import (
    TEST_DIGEST,
    TEST_EVENT_SIGNER,
    FakeRecord,
    build_harness,
    make_event,
    make_subscription,
    record_of,
)

OTHER_DIGEST = "sha256:" + "e" * 64


def test_signer_accepts_k8s_secret_ref_without_plaintext_fallback() -> None:
    store = SimpleNamespace(get=lambda ref: "unit-test-only-event-signing-key-32-bytes")
    signer = HmacPluginEventSigner(
        secret_store=store,
        secret_ref="k8s-secret://agentscale-plugin-events/signing-key",
    )
    event = make_event()
    signature = signer.sign(event, topic="agentscale.plugin.events")
    assert signer.verify(
        event, topic="agentscale.plugin.events", signature=signature
    )
    with pytest.raises(PluginEventError):
        HmacPluginEventSigner(
            secret_store=store, secret_ref="k8s-secret://missing-key"
        )


@pytest.mark.asyncio
async def test_signed_event_ack_and_unsigned_or_tampered_events_go_raw_dlq() -> None:
    event = make_event()
    signed = build_harness(records=[record_of(event)])
    assert await signed.dispatcher.consume(make_subscription()) is DeliveryOutcome.ACK
    assert len(signed.handler.calls) == 1

    unsigned = build_harness(records=[FakeRecord(value=event.to_wire(), offset=71)])
    assert await unsigned.dispatcher.consume(make_subscription()) is DeliveryOutcome.DLQ
    assert unsigned.handler.calls == []
    assert unsigned.wake.wakes == []
    assert unsigned.consumer.commits == [71]
    assert len(unsigned.raw_dlq.records) == 1

    tampered_wire = TEST_EVENT_SIGNER.signed_wire(
        event, topic="agentscale.plugin.events"
    )
    tampered_wire["payload_digest"] = OTHER_DIGEST
    tampered = build_harness(records=[FakeRecord(value=tampered_wire, offset=72)])
    assert await tampered.dispatcher.consume(make_subscription()) is DeliveryOutcome.DLQ
    assert tampered.handler.calls == []
    assert tampered.consumer.commits == [72]


@pytest.mark.asyncio
async def test_unknown_topic_never_reaches_handler_or_derived_dlq() -> None:
    event = make_event()
    record = FakeRecord(
        value=TEST_EVENT_SIGNER.signed_wire(
            event, topic="agentscale.plugin.events"
        ),
        topic="agentscale.plugin.unknown",
        offset=91,
    )
    harness = build_harness(records=[record])
    with pytest.raises(PluginEventError) as caught:
        await harness.dispatcher.consume(make_subscription())
    assert caught.value.code is PluginEventErrorCode.INVALID_EVENT
    assert harness.consumer.seeks == [91]
    assert harness.consumer.commits == []
    assert harness.raw_dlq.publish_calls == []
    assert harness.handler.calls == []


@pytest.mark.asyncio
async def test_publish_carries_verifiable_signature_and_topic_binding() -> None:
    event = make_event()
    harness = build_harness()
    assert await harness.dispatcher.publish(event) is True
    sent = harness.producer.sent[0]
    assert TEST_EVENT_SIGNER.verify(
        event,
        topic="agentscale.plugin.events",
        signature=sent["value"]["event_signature"],
    )
    assert sent["headers"]["event_signature"] == sent["value"]["event_signature"]


def bound_subscription() -> Subscription:
    return Subscription(
        id="co_42:inst_1:sub_1",
        plugin_id="mod_equipment",
        event_types=("offer.created",),
        consumer_group=(
            "tenant:co_42:plugin:mod_equipment:inst_1:group:workers"
        ),
    )


def bound_event() -> Any:
    return make_event(
        company_id="co_42",
        plugin_id="mod_equipment",
        subscription_id="co_42:inst_1:sub_1",
    )


class Grants:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            company_id="co_42",
            plugin_id="mod_equipment",
            plugin_instance_id="inst_1",
            artifact_digest=TEST_DIGEST,
            grant_id="grant_1",
            is_active=True,
        )

    async def get(self, *, company_id: str, plugin_id: str) -> Any:
        if (company_id, plugin_id) != ("co_42", "mod_equipment"):
            return None
        return self.record


class Activator:
    def __init__(self, grants: Grants) -> None:
        self.grants = grants
        self.calls: list[tuple[str, float | None]] = []

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> Any:
        self.calls.append((instance_id, timeout))
        grant = self.grants.record
        return SimpleNamespace(
            plugin_instance_id=instance_id,
            token="unit-only-token",
            jti="jti-1",
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            claims={
                "company_id": grant.company_id,
                "plugin_id": grant.plugin_id,
                "plugin_instance_id": grant.plugin_instance_id,
                "artifact_digest": grant.artifact_digest,
                "grant_id": grant.grant_id,
                "sub": (
                    f"plugin:{grant.plugin_id}:{grant.company_id}:"
                    f"{grant.plugin_instance_id}"
                ),
            },
        )


class Proxy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def forward(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(status_code=204, body=b"", stream=None)


def delivery() -> tuple[ActivatingPluginEventHandler, Grants, Activator, Proxy]:
    grants = Grants()
    activator = Activator(grants)
    proxy = Proxy()
    handler = ActivatingPluginEventHandler(
        activator=activator,
        proxy=proxy,
        grant_store=grants,
        signer=TEST_EVENT_SIGNER,
        wake_timeout=3.0,
    )
    return handler, grants, activator, proxy


@pytest.mark.asyncio
async def test_wake_hook_and_handler_share_exact_instance_digest_and_signed_body() -> None:
    handler, _, activator, proxy = delivery()
    event = bound_event()
    subscription = bound_subscription()

    await handler.wake(event, subscription=subscription)
    await handler.handle(event, subscription=subscription)

    assert activator.calls == [("inst_1", 3.0)]
    assert len(proxy.calls) == 1
    forwarded = proxy.calls[0]
    assert forwarded["plugin_instance_id"] == "inst_1"
    assert forwarded["request"].path == "/events/offer.created"
    wire = json.loads(forwarded["request"].body)
    assert TEST_EVENT_SIGNER.verify(
        event,
        topic="agentscale.plugin.events",
        signature=wire["event_signature"],
    )
    expected_key = backend_idempotency_key(
        IdempotencyKey(
            subscription_id="co_42:inst_1:sub_1",
            source_event_id=event.source_id,
        )
    )
    assert forwarded["request"].headers["Idempotency-Key"] == expected_key
    assert "co_42:inst_1:sub_1" not in expected_key
    assert event.source_id not in expected_key


@pytest.mark.asyncio
async def test_pending_wake_binding_is_isolated_between_parallel_events() -> None:
    handler, _, activator, proxy = delivery()
    subscription = bound_subscription()
    first = bound_event()
    second = make_event(
        event_id="evt-parallel-2",
        source_event_id="source-parallel-2",
        company_id="co_42",
        plugin_id="mod_equipment",
        subscription_id=subscription.id,
    )

    async def deliver(event: Any) -> None:
        await handler.wake(event, subscription=subscription)
        await handler.handle(event, subscription=subscription)

    await asyncio.gather(deliver(first), deliver(second))
    assert len(activator.calls) == 2
    assert len(proxy.calls) == 2


@pytest.mark.asyncio
async def test_abort_clears_only_exact_pending_event() -> None:
    handler, _, _, proxy = delivery()
    subscription = bound_subscription()
    first = bound_event()
    other = make_event(
        event_id="evt-other",
        source_event_id="source-other",
        company_id="co_42",
        plugin_id="mod_equipment",
        subscription_id=subscription.id,
    )
    await handler.wake(first, subscription=subscription)
    handler.abort(other, subscription=subscription)
    await handler.handle(first, subscription=subscription)
    assert len(proxy.calls) == 1

    await handler.wake(first, subscription=subscription)
    handler.abort(first, subscription=subscription)
    with pytest.raises(PluginEventError, match="without its exact wake hook"):
        await handler.handle(first, subscription=subscription)


@pytest.mark.asyncio
async def test_wake_rejects_claim_digest_substitution_before_delivery() -> None:
    handler, _, activator, proxy = delivery()
    original_wake = activator.wake

    async def wrong_claims(instance_id: str, *, timeout: float | None = None) -> Any:
        result = await original_wake(instance_id, timeout=timeout)
        result.claims["artifact_digest"] = OTHER_DIGEST
        return result

    activator.wake = wrong_claims  # type: ignore[method-assign]
    with pytest.raises(PluginEventError, match="artifact digest mismatch"):
        await handler.wake(bound_event(), subscription=bound_subscription())
    assert proxy.calls == []


@pytest.mark.asyncio
async def test_handler_without_exact_prior_wake_is_not_a_success_fallback() -> None:
    handler, _, activator, proxy = delivery()
    with pytest.raises(PluginEventError, match="without its exact wake hook"):
        await handler.handle(bound_event(), subscription=bound_subscription())
    assert activator.calls == []
    assert proxy.calls == []
