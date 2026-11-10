"""Raw-record DLQ: poison records, безопасные digest и ручной commit.

Фейк RawDlqPort существует только для проверки application-контракта. Он не
является production-реализацией durable DLQ. Тесты проходят в текущем прогоне
(`pytest tests/unit/plugins`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from prodavan.application.plugins.events import (
    DeliveryClaimError,
    DeliveryOutcome,
    DeliveryOutcomeUncertainError,
    PluginEventError,
    PluginEventErrorCode,
    RawDlqRecord,
    RawRecordIdempotencyKey,
)

from .conftest import (
    TEST_EVENT_SIGNER,
    FakeRecord,
    build_harness,
    make_event,
    make_subscription,
    record_of,
)


@pytest.mark.asyncio
async def test_poison_decode_goes_to_raw_dlq_then_commits() -> None:
    """Decode failure -> подтверждённый raw DLQ -> commit, без fictitious event."""

    poison = b'{"event_id":"untrusted","payload":'
    consumed = FakeRecord(
        value={},
        raw_value=poison,
        partition=3,
        offset=91,
    )
    h = build_harness(records=[consumed])

    original_commit = h.consumer.commit

    async def commit_after_raw_dlq(offset: int) -> None:
        assert len(h.raw_dlq.records) == 1, "commit обязан идти после raw DLQ"
        await original_commit(offset)

    h.consumer.commit = commit_after_raw_dlq  # type: ignore[method-assign]

    outcome = await h.dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.DLQ
    assert h.consumer.commits == [91]
    assert h.consumer.seeks == []
    assert len(h.raw_dlq.records) == 1
    raw = h.raw_dlq.records[0]
    assert isinstance(raw, RawDlqRecord)
    assert raw.topic == "agentscale.plugin.events"
    assert raw.partition == 3
    assert raw.offset == 91
    assert raw.consumer_group == "plugin-mod_equipment-sub_1"
    assert raw.error_kind == "JSONDecodeError"
    assert raw.payload_digest == f"sha256:{hashlib.sha256(poison).hexdigest()}"
    assert raw.idempotency_key == RawRecordIdempotencyKey(
        topic="agentscale.plugin.events",
        partition=3,
        offset=91,
        consumer_group="plugin-mod_equipment-sub_1",
    )

    # Ни валидный PluginEvent, ни его handler/DLQ/idempotency не подменяются.
    assert h.handler.calls == []
    assert h.wake.wakes == []
    assert h.dlq.records == []
    assert h.idempotency.remembered == []
    assert h.audit.entries == []


@pytest.mark.asyncio
async def test_raw_dlq_failure_does_not_commit_and_retry_recovers() -> None:
    """DLQ failure -> seek + исключение без commit; retry успешно коммитит."""

    consumed = FakeRecord(value=b"not-json", offset=23)
    h = build_harness(records=[consumed])
    h.raw_dlq.fail = True

    with pytest.raises(PluginEventError) as exc:
        await h.dispatcher.consume(make_subscription())

    assert exc.value.code is PluginEventErrorCode.DLQ_FAILED
    assert h.consumer.commits == []
    assert h.consumer.seeks == [23]
    assert h.raw_dlq.records == []
    assert len(h.raw_dlq.publish_calls) == 1

    h.raw_dlq.fail = False
    h.consumer.records.append(consumed)
    outcome = await h.dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.DLQ
    assert h.consumer.commits == [23]
    assert h.consumer.seeks == [23]
    assert len(h.raw_dlq.records) == 1


@pytest.mark.asyncio
async def test_raw_dlq_retry_after_commit_failure_does_not_duplicate() -> None:
    """Повтор после commit failure подтверждает тот же natural key без дубля."""

    consumed = FakeRecord(value=b"{broken", partition=2, offset=34)
    h = build_harness(records=[consumed])
    h.consumer.fail_commit = True

    with pytest.raises(PluginEventError) as exc:
        await h.dispatcher.consume(make_subscription())

    assert exc.value.code is PluginEventErrorCode.COMMIT_FAILED
    assert h.consumer.commits == []
    assert h.consumer.seeks == [34]
    assert len(h.raw_dlq.records) == 1

    h.consumer.fail_commit = False
    h.consumer.records.append(consumed)
    outcome = await h.dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.DLQ
    assert h.consumer.commits == [34]
    assert len(h.raw_dlq.publish_calls) == 2, "повтор подтверждает durable запись"
    assert len(h.raw_dlq.records) == 1, "natural key не допускает дубликат"
    assert h.raw_dlq.publish_calls[0].idempotency_key == (
        h.raw_dlq.publish_calls[1].idempotency_key
    )


@pytest.mark.asyncio
async def test_raw_dlq_retry_after_lost_acknowledgement_does_not_duplicate() -> None:
    """Неопределённый DLQ failure после записи -> retry по natural key, без дубля."""

    consumed = FakeRecord(value=b"{broken", partition=2, offset=35)
    h = build_harness(records=[consumed])
    h.raw_dlq.fail_after_write = True

    with pytest.raises(PluginEventError) as exc:
        await h.dispatcher.consume(make_subscription())

    assert exc.value.code is PluginEventErrorCode.DLQ_FAILED
    assert h.consumer.commits == []
    assert h.consumer.seeks == [35]
    assert len(h.raw_dlq.records) == 1, "запись есть, но подтверждение потеряно"

    h.raw_dlq.fail_after_write = False
    h.consumer.records.append(consumed)
    outcome = await h.dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.DLQ
    assert h.consumer.commits == [35]
    assert len(h.raw_dlq.publish_calls) == 2
    assert len(h.raw_dlq.records) == 1


@pytest.mark.asyncio
async def test_malformed_raw_dlq_never_leaks_payload_or_identity() -> None:
    """Validation failure хранит только digest/error/coordinates, не PII/event_id."""

    secret = "person@example.invalid:super-secret-token"
    untrusted_event_id = "do-not-promote-to-identity"
    poison = {
        "event_id": untrusted_event_id,
        "payload": {"token": secret},
    }
    h = build_harness(records=[FakeRecord(value=poison, partition=1, offset=45)])

    outcome = await h.dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.DLQ
    assert h.consumer.commits == [45]
    assert len(h.raw_dlq.records) == 1
    raw = h.raw_dlq.records[0]
    expected = json.dumps(
        poison,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert raw.payload_digest == f"sha256:{hashlib.sha256(expected).hexdigest()}"
    assert raw.error_kind == "PluginEventError"

    wire = raw.to_wire()
    assert set(wire) == {
        "topic",
        "partition",
        "offset",
        "consumer_group",
        "payload_digest",
        "error_kind",
        "failed_at",
    }
    exposed = json.dumps(wire, ensure_ascii=False) + repr(raw)
    assert secret not in exposed
    assert untrusted_event_id not in exposed
    assert "payload" not in wire
    assert "event_id" not in wire
    assert "source_event_id" not in wire
    assert h.dlq.records == []
    assert h.audit.entries == []


@pytest.mark.asyncio
async def test_valid_wire_bytes_keep_handler_ack_semantics() -> None:
    """Новый decode path не меняет ACK валидного PluginEvent."""

    event = make_event()
    wire = json.dumps(
        TEST_EVENT_SIGNER.signed_wire(event, topic="agentscale.plugin.events"),
        separators=(",", ":"),
    ).encode("utf-8")
    h = build_harness(
        records=[FakeRecord(value={}, raw_value=wire, offset=56)],
    )

    outcome = await h.dispatcher.consume(make_subscription())

    assert outcome is DeliveryOutcome.ACK
    assert h.handler.calls == [event]
    assert h.consumer.commits == [56]
    assert h.raw_dlq.publish_calls == []
    assert h.dlq.records == []


@pytest.mark.asyncio
async def test_raw_pre_publish_assignment_loss_is_retryable_without_dlq_or_commit() -> None:
    h = build_harness(records=[FakeRecord(value=b"not-json", offset=80)])
    h.consumer.ownership = False
    with pytest.raises(DeliveryClaimError) as caught:
        await h.dispatcher.consume(make_subscription())
    assert caught.value.side_effects_uncertain is False
    assert h.raw_dlq.publish_calls == []
    assert h.consumer.commits == []
    assert h.consumer.seeks == [80]


@pytest.mark.asyncio
async def test_raw_post_publish_assignment_loss_is_uncertain_without_commit() -> None:
    h = build_harness(records=[FakeRecord(value=b"not-json", offset=81)])
    original_publish = h.raw_dlq.publish

    async def publish_then_rebalance(record: Any) -> bool:
        result = await original_publish(record)
        h.consumer.ownership = False
        return result

    h.raw_dlq.publish = publish_then_rebalance  # type: ignore[method-assign]
    with pytest.raises(DeliveryOutcomeUncertainError) as caught:
        await h.dispatcher.consume(make_subscription())
    assert caught.value.side_effects_uncertain is True
    assert len(h.raw_dlq.records) == 1
    assert h.consumer.commits == []
    assert h.consumer.seeks == [81]


@pytest.mark.asyncio
async def test_seen_store_error_is_not_duplicate_or_raw_dlq() -> None:
    """seen DB error пробрасывается: ни false duplicate, ни commit, ни raw DLQ."""

    h = build_harness(records=[record_of(make_event(), offset=67)])
    h.idempotency.seen_error = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await h.dispatcher.consume(make_subscription())

    assert h.consumer.commits == []
    assert h.handler.calls == []
    assert h.audit.entries == []
    assert h.raw_dlq.publish_calls == []
    assert h.idempotency.remembered == []
