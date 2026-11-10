"""Unit-тесты портов (Protocol) шины событий плагинов (задача 10).

Проверяем, что ``ConsumerPort`` объявлен с ЯВНЫМИ ``commit`` / ``seek`` /
``poll`` (иначе ручной commit невозможен), что все обязательные порты
присутствуют, и что ``runtime_checkable``-протоколы распознают фейки из
``conftest``. Тесты НЕ запускаются (verification freeze).
"""

from __future__ import annotations

import inspect

from prodavan.application.plugins.events import (
    AuditPort,
    ConsumerPort,
    DeliveryClaimPort,
    DlqPort,
    IdempotencyStorePort,
    ProducerPort,
    PublishedEventStorePort,
    RawDlqPort,
    SubscriptionStorePort,
    TopicAdminPort,
    WakeHookPort,
)

from .conftest import (
    FakeAudit,
    FakeConsumer,
    FakeDeliveryClaims,
    FakeDlq,
    FakeHandler,
    FakeProducer,
    FakeRawDlq,
    FakeSeenStore,
    FakeSubscriptions,
    FakeTopicAdmin,
    FakeWake,
    build_harness,
)


def test_consumer_port_declares_manual_commit_seek_poll() -> None:
    """acceptance №20: ручной commit/seek/poll — ЯВНЫЕ методы протокола."""

    for name in ("poll", "commit", "seek", "close"):
        assert hasattr(ConsumerPort, name), f"ConsumerPort.{name} обязателен"
    commit_sig = inspect.signature(ConsumerPort.commit)
    assert "offset" in commit_sig.parameters, "commit(offset) с явным offset"
    seek_sig = inspect.signature(ConsumerPort.seek)
    assert "offset" in seek_sig.parameters, "seek(offset) с явным offset"


def test_all_required_ports_exist() -> None:
    """Полный набор портов из задания + вспомогательные (fail-closed)."""

    ports = (
        ProducerPort,
        ConsumerPort,
        DeliveryClaimPort,
        DlqPort,
        SubscriptionStorePort,
        AuditPort,
        TopicAdminPort,
        IdempotencyStorePort,
        PublishedEventStorePort,
        RawDlqPort,
        WakeHookPort,
    )
    for port in ports:
        assert hasattr(port, "_is_protocol") and port._is_protocol


def test_producer_send_signature_accepts_topic_key_value() -> None:
    sig = inspect.signature(ProducerPort.send)
    for name in ("topic", "key", "value", "headers"):
        assert name in sig.parameters


def test_fakes_satisfy_runtime_protocols() -> None:
    """Фейки из conftest структурно совместимы с протоколами."""

    h = build_harness()
    assert isinstance(h.producer, ProducerPort)
    assert isinstance(h.consumer, ConsumerPort)
    assert isinstance(h.admin, TopicAdminPort)
    assert isinstance(h.dlq, DlqPort)
    assert isinstance(h.raw_dlq, RawDlqPort)
    assert isinstance(h.subscriptions, SubscriptionStorePort)
    assert isinstance(h.published, PublishedEventStorePort)
    assert isinstance(h.idempotency, IdempotencyStorePort)
    assert isinstance(h.claims, DeliveryClaimPort)
    assert isinstance(h.audit, AuditPort)
    from prodavan.application.plugins.events import EventHandlerPort

    assert isinstance(h.handler, EventHandlerPort)
    assert isinstance(h.wake, WakeHookPort)
    assert isinstance(FakeSeenStore(), IdempotencyStorePort)
    assert isinstance(FakeDeliveryClaims(), DeliveryClaimPort)
    assert isinstance(FakeSubscriptions(), SubscriptionStorePort)
    assert isinstance(FakeProducer(), ProducerPort)
    assert isinstance(FakeConsumer(records=[]), ConsumerPort)
    assert isinstance(FakeDlq(), DlqPort)
    assert isinstance(FakeRawDlq(), RawDlqPort)
    assert isinstance(FakeAudit(), AuditPort)
    assert isinstance(FakeTopicAdmin(), TopicAdminPort)
    assert isinstance(FakeWake(), WakeHookPort)
    assert isinstance(FakeHandler(), EventHandlerPort)


def test_no_raw_payload_parameter_in_ports() -> None:
    """Ни один метод порта не принимает bytes/raw payload (§4.5, criterion 11)."""

    from prodavan.application.plugins.events import ports as ports_module

    forbidden_params = {"payload", "body", "raw", "content", "data"}
    for _, obj in vars(ports_module).items():
        if not getattr(obj, "_is_protocol", False):
            continue
        for name, member in vars(obj).items():
            if name.startswith("_") or not callable(member):
                continue
            params = set(inspect.signature(member).parameters)
            params.discard("self")
            assert not (params & forbidden_params), f"{obj.__name__}.{name}: raw payload"
