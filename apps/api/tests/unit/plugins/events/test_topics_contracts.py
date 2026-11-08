"""Unit-тесты констант топиков и контрактов событий плагинов (задача 10).

Проверяется: каноническое имя топика, DLQ-суффикс, ``topic_for``,
fail-closed ``partition_key``, обязательность ``ensure_topic`` при
``auto_create_topics_enabled=false``, frozen-контракт ``PluginEvent`` без raw
payload, идемпотентность по ``source_event_id`` и зафиксированный конфиг
консьюмера (acceptance №20). Тесты НЕ запускаются (verification freeze).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.events import (
    CONSUMER_AUTO_OFFSET_RESET,
    CONSUMER_ENABLE_AUTO_COMMIT,
    MAX_DELIVERY_ATTEMPTS,
    PLUGIN_DLQ_SUFFIX,
    PLUGIN_EVENTS_TOPIC,
    DeliveryAttempt,
    DeliveryOutcome,
    DlqRecord,
    IdempotencyKey,
    PluginEvent,
    PluginEventError,
    PluginEventErrorCode,
    Subscription,
    canonical_digest,
    consumer_config,
    delivery_outcome_for_attempt,
    dlq_topic,
    dlq_topic_for,
    ensure_dlq_topic,
    ensure_topic,
    partition_key,
    partition_key_bytes,
    topic_for,
)
from prodavan.application.plugins.events.contracts import PLUGIN_EVENT_SCHEMA_VERSION

from .conftest import TEST_DIGEST, FakeTopicAdmin, make_event, make_subscription


# ---------------------------------------------------------------------------
# topics.py
# ---------------------------------------------------------------------------


def test_canonical_topic_and_dlq_suffix() -> None:
    """§7.3: один топик событий, DLQ через суффикс .dlq."""

    assert PLUGIN_EVENTS_TOPIC == "agentscale.plugin.events"
    assert PLUGIN_DLQ_SUFFIX == ".dlq"
    assert topic_for() == PLUGIN_EVENTS_TOPIC
    assert topic_for(None) == PLUGIN_EVENTS_TOPIC
    assert dlq_topic_for() == "agentscale.plugin.events.dlq"
    assert dlq_topic(PLUGIN_EVENTS_TOPIC) == f"{PLUGIN_EVENTS_TOPIC}{PLUGIN_DLQ_SUFFIX}"


def test_topic_for_plugin_is_namespaced_and_validated() -> None:
    """Выделенный топик плагина + fail-closed на некорректном plugin_id."""

    assert topic_for("mod_equipment") == "agentscale.plugin.events.mod_equipment"
    assert dlq_topic_for("mod_equipment") == "agentscale.plugin.events.mod_equipment.dlq"
    for bad in ("", "  ", "UPPER", "../escape", "a b", "x" * 200):
        with pytest.raises(PluginEventError):
            topic_for(bad)


def test_dlq_topic_rejects_double_suffix() -> None:
    """Защита от цикла DLQ -> DLQ."""

    with pytest.raises(PluginEventError) as exc:
        dlq_topic("agentscale.plugin.events.dlq")
    assert exc.value.code is PluginEventErrorCode.INVALID_CONTRACT


def test_partition_key_is_company_id_not_hash_or_body() -> None:
    """§7.3: ключ партиции = company_id; без хэширования и без тела."""

    assert partition_key("co_42") == "co_42"
    assert partition_key_bytes("co_42") == b"co_42"
    # Разные компании — разные ключи; одна компания — стабильный ключ.
    assert partition_key("co_42") == partition_key("co_42")
    assert partition_key("co_42") != partition_key("co_43")
    for bad in ("", "   ", None, 42):
        with pytest.raises(PluginEventError):
            partition_key(bad)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_topic_mandatory_when_auto_create_disabled() -> None:
    """auto_create_topics_enabled=false (kafka.yaml:221): топик создаём ЯВНО."""

    admin = FakeTopicAdmin()
    await ensure_topic(admin, PLUGIN_EVENTS_TOPIC)
    assert admin.created == [("agentscale.plugin.events", 1, 1)]
    # Повторная проверка после create (fail-closed) действительно выполнена.
    assert admin.exists_calls.count(PLUGIN_EVENTS_TOPIC) == 2


@pytest.mark.asyncio
async def test_ensure_dlq_topic_created_explicitly() -> None:
    """DLQ не создаётся init-Job'ом — готовим его через тот же порт."""

    admin = FakeTopicAdmin()
    name = await ensure_dlq_topic(admin, PLUGIN_EVENTS_TOPIC)
    assert name == "agentscale.plugin.events.dlq"
    assert ("agentscale.plugin.events.dlq", 1, 1) in admin.created


@pytest.mark.asyncio
async def test_ensure_topic_fails_closed_without_admin() -> None:
    """Нет TopicAdminPort — публикация запрещена, а не 'надеемся на auto-create'."""

    with pytest.raises(PluginEventError) as exc:
        await ensure_topic(None, PLUGIN_EVENTS_TOPIC)  # type: ignore[arg-type]
    assert exc.value.code is PluginEventErrorCode.MISSING_DEPENDENCY


@pytest.mark.asyncio
async def test_ensure_topic_fails_closed_when_create_did_not_happen() -> None:
    """create_topic ничего не создал -> отказ, а не молчаливое продолжение."""

    admin = FakeTopicAdmin()
    admin.create_topic = _noop_create  # type: ignore[method-assign]
    with pytest.raises(PluginEventError) as exc:
        await ensure_topic(admin, "agentscale.plugin.events.custom")
    assert exc.value.code is PluginEventErrorCode.TOPIC_NOT_ENSURED


async def _noop_create(topic: str, *, num_partitions: int, replication_factor: int) -> None:
    return None


@pytest.mark.asyncio
async def test_ensure_topic_rejects_rf_greater_than_one() -> None:
    """RF=1 single-node Redpanda: RF>1 даёт under-replicated топик."""

    admin = FakeTopicAdmin()
    with pytest.raises(PluginEventError) as exc:
        await ensure_topic(admin, PLUGIN_EVENTS_TOPIC, replication_factor=3)
    assert exc.value.code is PluginEventErrorCode.INVALID_CONTRACT
    assert admin.created == []


@pytest.mark.asyncio
async def test_ensure_topic_wraps_unexpected_admin_errors() -> None:
    """Ошибка Admin API -> TOPIC_NOT_ENSURED, без тихого best-effort."""

    admin = FakeTopicAdmin(fail_on_create=True)
    with pytest.raises(PluginEventError) as exc:
        await ensure_topic(admin, PLUGIN_EVENTS_TOPIC)
    assert exc.value.code is PluginEventErrorCode.TOPIC_NOT_ENSURED


# ---------------------------------------------------------------------------
# contracts.py: PluginEvent
# ---------------------------------------------------------------------------


def test_plugin_event_is_frozen_and_has_no_raw_payload() -> None:
    """Frozen/slots + только payload_digest (§4.5, criterion 11)."""

    event = make_event()
    assert dataclasses.is_dataclass(event)
    assert event.__dataclass_params__.frozen
    assert "payload_digest" in {f.name for f in dataclasses.fields(event)}
    assert "payload" not in {f.name for f in dataclasses.fields(event)}
    assert "url" not in {f.name for f in dataclasses.fields(event)}
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.event_id = "other"  # type: ignore[misc]
    wire = event.to_wire()
    assert wire["payload_digest"] == TEST_DIGEST
    assert "payload" not in wire
    assert "url" not in wire


def test_plugin_event_roundtrip_wire_ignores_extra_raw_payload() -> None:
    """Даже если в записи приехал payload, в контракт он не попадает."""

    event = make_event()
    data = {**event.to_wire(), "payload": {"secret": "do-not-log"}, "url": "https://x"}
    restored = PluginEvent.from_wire(data)
    assert restored.event_id == event.event_id
    assert restored.payload_digest == TEST_DIGEST
    assert not hasattr(restored, "payload")


def test_plugin_event_rejects_invalid_digest_or_schema() -> None:
    """Fail-closed: некорректный digest / schema_version / пустые поля."""

    assert make_event().to_wire()["payload_digest"] == TEST_DIGEST  # валидный кейс
    with pytest.raises(PluginEventError):
        PluginEvent(**{**_fields(make_event()), "payload_digest": "not-a-digest"})
    with pytest.raises(PluginEventError) as exc:
        PluginEvent(**{**_fields(make_event()), "schema_version": 2})
    assert exc.value.code is PluginEventErrorCode.UNSUPPORTED_SCHEMA


def _fields(event: PluginEvent) -> dict:
    return {f.name: getattr(event, f.name) for f in dataclasses.fields(event)}


def test_plugin_event_requires_aware_datetime() -> None:
    with pytest.raises(PluginEventError):
        PluginEvent(
            **{**_fields(make_event()), "occurred_at": datetime(2026, 11, 8, 6, 0)}
        )


def test_canonical_digest_is_stable_and_rejects_non_json() -> None:
    first = canonical_digest({"b": 2, "a": 1})
    second = canonical_digest({"a": 1, "b": 2})
    assert first == second
    assert first.startswith("sha256:") and len(first) == 71
    with pytest.raises(PluginEventError):
        canonical_digest({"bad": object()})  # type: ignore[arg-type]


def test_source_event_id_used_for_consumer_idempotency() -> None:
    """Ключ консьюмера = (subscription_id, source_event_id), НЕ event_id/URL/hash."""

    event = make_event(event_id="platform-1", source_event_id="source-abc")
    key = IdempotencyKey(subscription_id="sub_1", source_event_id=event.source_id)
    assert key.as_tuple() == ("sub_1", "source-abc")
    assert event.source_id == "source-abc"
    # При ре-паблише platform event_id меняется, source остаётся тем же.
    event2 = make_event(event_id="platform-2", source_event_id="source-abc")
    assert IdempotencyKey("sub_1", event.source_id) == IdempotencyKey("sub_1", event2.source_id)
    # По умолчанию source_event_id = event_id (платформенный продюсер).
    assert make_event(event_id="only-id").source_id == "only-id"


# ---------------------------------------------------------------------------
# contracts.py: DeliveryOutcome / Subscription / DlqRecord / config
# ---------------------------------------------------------------------------


def test_delivery_outcome_normalization_retry_then_dlq() -> None:
    """Ровно 3 попытки: 1,2 -> RETRY, 3 -> DLQ, ACK -> ACK."""

    for n in (1, 2):
        attempt = DeliveryAttempt(outcome=DeliveryOutcome.RETRY, attempt=n, error_kind="TimeoutError")
        assert delivery_outcome_for_attempt(attempt) is DeliveryOutcome.RETRY
    third = DeliveryAttempt(outcome=DeliveryOutcome.RETRY, attempt=3, error_kind="TimeoutError")
    assert delivery_outcome_for_attempt(third) is DeliveryOutcome.DLQ
    ack = DeliveryAttempt(outcome=DeliveryOutcome.ACK, attempt=1)
    assert delivery_outcome_for_attempt(ack) is DeliveryOutcome.ACK
    assert MAX_DELIVERY_ATTEMPTS == 3


def test_delivery_attempt_rejects_ack_with_error_and_bad_attempt() -> None:
    with pytest.raises(PluginEventError):
        DeliveryAttempt(outcome=DeliveryOutcome.ACK, attempt=1, error_kind="X")
    with pytest.raises(PluginEventError):
        DeliveryAttempt(outcome=DeliveryOutcome.RETRY, attempt=4, error_kind="X")
    with pytest.raises(PluginEventError):
        DeliveryAttempt(outcome="ACK", attempt=1)  # type: ignore[arg-type]


def test_subscription_validation() -> None:
    sub = make_subscription()
    assert sub.accepts("offer.created")
    assert not sub.accepts("price.updated")
    assert sub.consumer_group == "plugin-mod_equipment-sub_1"
    with pytest.raises(PluginEventError):
        Subscription(id="", plugin_id="p", event_types=("a",), consumer_group="g")
    with pytest.raises(PluginEventError):
        Subscription(id="s", plugin_id="p", event_types=(), consumer_group="g")


def test_dlq_record_preserves_event_id_and_run_id_without_payload() -> None:
    """DLQ-событие сохраняет event_id и run_id, но не raw payload."""

    event = make_event(event_id="evt-dlq", run_id="run-dlq")
    record = DlqRecord(
        event=event,
        subscription_id="sub_1",
        topic=PLUGIN_EVENTS_TOPIC,
        dlq_topic=dlq_topic_for(),
        attempts=MAX_DELIVERY_ATTEMPTS,
        error_kind="RuntimeError",
    )
    wire = record.to_wire()
    assert wire["event_id"] == "evt-dlq"
    assert wire["run_id"] == "run-dlq"
    assert wire["payload_digest"] == TEST_DIGEST
    assert "payload" not in wire
    with pytest.raises(PluginEventError):
        DlqRecord(
            event=event,
            subscription_id="sub_1",
            topic=PLUGIN_EVENTS_TOPIC,
            dlq_topic=dlq_topic_for(),
            attempts=2,
            error_kind="RuntimeError",
        )


def test_consumer_config_fixed_earliest_and_no_auto_commit() -> None:
    """Acceptance №20: earliest + enable_auto_commit=False зафиксированы в коде."""

    cfg = consumer_config()
    assert cfg["auto_offset_reset"] == "earliest"
    assert cfg["enable_auto_commit"] is False
    assert cfg["consumer_mode"] == "dispatch"
    assert cfg["topic"] == "agentscale.plugin.events"
    assert cfg["max_delivery_attempts"] == 3
    assert CONSUMER_AUTO_OFFSET_RESET == "earliest"
    assert CONSUMER_ENABLE_AUTO_COMMIT is False
    # Возвращается копия: адаптер не может silently изменить канон.
    cfg["enable_auto_commit"] = True
    assert consumer_config()["enable_auto_commit"] is False


def test_schema_version_constant() -> None:
    assert PLUGIN_EVENT_SCHEMA_VERSION == 1
