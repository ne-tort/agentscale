"""Шина событий плагинов (F7, задача 10, дизайн v4 §7.3/§13.3, criterion 20).

Публичный API зоны: константы топиков, контракты, порты и
:class:`PluginEventDispatcher`.

Что здесь НЕ делается (§13.3 «что заменено в v4»):

* Celery-outbox ``webhook_deliveries`` НЕ используется как шина событий —
  он остаётся только для исходящих вебхуков (``application/external``);
* ``KAFKA_CONSUMER_MODE=kick`` + ``enable_auto_commit=True`` +
  ``auto_offset_reset="latest"`` (as-built ``kafka_manager.py:695-696``)
  НЕ применяется: он теряет события при простое консьюмера. Вместо него —
  dispatch с ручным ``commit``, ``earliest``, DLQ и 3 ретраями;
* raw payload в шину/аудит/DLQ не попадает — только ``payload_digest``.

Русские комментарии, fail-closed, тесты написаны, но НЕ запускались
(verification freeze, «Формат работы» §9).
"""

from __future__ import annotations

from .contracts import (
    AUDIT_PLUGIN_EVENT_DLQ,
    AUDIT_PLUGIN_EVENT_RECEIVED,
    CONSUMER_AUTO_OFFSET_RESET,
    CONSUMER_ENABLE_AUTO_COMMIT,
    MAX_DELIVERY_ATTEMPTS,
    PLUGIN_EVENT_SCHEMA_VERSION,
    DeliveryAttempt,
    ConsumerAssignment,
    DeliveryClaim,
    DeliveryClaimError,
    DeliveryOutcome,
    DeliveryOutcomeUncertainError,
    DlqRecord,
    IdempotencyKey,
    PluginEvent,
    RawDlqRecord,
    RawRecordIdempotencyKey,
    Subscription,
    backend_idempotency_key,
    canonical_digest,
    consumer_config,
    delivery_outcome_for_attempt,
)
from .dispatch import PluginEventDispatcher, WakeHook
from .errors import PluginEventError, PluginEventErrorCode
from .ports import (
    AuditPort,
    ConsumedRecord,
    ConsumerOwnershipPort,
    ConsumerPort,
    DlqPort,
    EventHandlerPort,
    DeliveryClaimPort,
    IdempotencyStorePort,
    ProducerPort,
    PublishedEventStorePort,
    RawDlqPort,
    SubscriptionStorePort,
    TopicAdminPort,
    WakeHookPort,
)
from .topics import (
    DEFAULT_NUM_PARTITIONS,
    DEFAULT_REPLICATION_FACTOR,
    PLUGIN_DLQ_SUFFIX,
    PLUGIN_EVENTS_TOPIC,
    dlq_topic,
    dlq_topic_for,
    ensure_dlq_topic,
    ensure_topic,
    partition_key,
    partition_key_bytes,
    topic_for,
)

__all__ = [
    # topics
    "PLUGIN_EVENTS_TOPIC",
    "PLUGIN_DLQ_SUFFIX",
    "DEFAULT_NUM_PARTITIONS",
    "DEFAULT_REPLICATION_FACTOR",
    "topic_for",
    "dlq_topic",
    "dlq_topic_for",
    "partition_key",
    "partition_key_bytes",
    "ensure_topic",
    "ensure_dlq_topic",
    # contracts
    "PLUGIN_EVENT_SCHEMA_VERSION",
    "CONSUMER_AUTO_OFFSET_RESET",
    "CONSUMER_ENABLE_AUTO_COMMIT",
    "MAX_DELIVERY_ATTEMPTS",
    "AUDIT_PLUGIN_EVENT_RECEIVED",
    "AUDIT_PLUGIN_EVENT_DLQ",
    "PluginEvent",
    "DeliveryOutcome",
    "DeliveryAttempt",
    "ConsumerAssignment",
    "DeliveryClaim",
    "DeliveryClaimError",
    "DeliveryOutcomeUncertainError",
    "backend_idempotency_key",
    "Subscription",
    "IdempotencyKey",
    "RawRecordIdempotencyKey",
    "DlqRecord",
    "RawDlqRecord",
    "canonical_digest",
    "consumer_config",
    "delivery_outcome_for_attempt",
    # ports
    "ProducerPort",
    "ConsumerPort",
    "DlqPort",
    "TopicAdminPort",
    "SubscriptionStorePort",
    "IdempotencyStorePort",
    "DeliveryClaimPort",
    "PublishedEventStorePort",
    "RawDlqPort",
    "AuditPort",
    "EventHandlerPort",
    "WakeHookPort",
    "ConsumerOwnershipPort",
    "ConsumedRecord",
    # dispatch
    "PluginEventDispatcher",
    "WakeHook",
    # errors
    "PluginEventError",
    "PluginEventErrorCode",
]
