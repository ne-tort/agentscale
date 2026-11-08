"""PluginEventDispatcher — доставка событий плагинов без потерь (F7, задача 10).

Реализует «Разделение транспортов» из §7.3 дизайна v4 и acceptance №20 (§11):

* ``publish`` — продюсер с ключом партиции ``company_id``, идемпотентность по
  ``event_id``, обязательная подготовка топика и DLQ через ``TopicAdminPort``
  (в кластере ``auto_create_topics_enabled=false``, ``kafka.yaml:221``);
* ``consume`` — ``auto_offset_reset="earliest"`` + ``enable_auto_commit=False``
  (зафиксировано в :func:`contracts.consumer_config`), коммит offset'а ТОЛЬКО
  после успеха; ошибка → до 3 ретраев → DLQ → коммит. Никакого ``kick``-режима
  и ``auto_commit``: as-built ``KafkaManager`` теряет события при простое
  консьюмера (``kafka_manager.py:695-696``, §13.3);
* ``wake_hook`` — колбэк, который будит backend плагина ДО доставки (задача 09);
* идемпотентность консьюмера по ``(subscription_id, source_event_id)``, а НЕ
  по URL/hash тела;
* аудит ``plugin_event_received`` / ``plugin_event_dlq`` без raw payload.

Fail-closed: отсутствие любого порта/адаптера, ошибка wake, аудита, DLQ,
хранилища или коммита — НЕ приводит к коммиту offset'а. Позиция возвращается
через ``seek``, событие будет прочитано снова (``earliest`` + ручной commit).

Celery-outbox ``webhook_deliveries`` НЕ импортируется и НЕ используется как
шина: он остаётся только для исходящих вебхуков (``application/external``).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .contracts import (
    AUDIT_PLUGIN_EVENT_DLQ,
    AUDIT_PLUGIN_EVENT_RECEIVED,
    MAX_DELIVERY_ATTEMPTS,
    DeliveryAttempt,
    DeliveryOutcome,
    DlqRecord,
    IdempotencyKey,
    PluginEvent,
    Subscription,
    consumer_config,
    delivery_outcome_for_attempt,
)
from .errors import PluginEventError, PluginEventErrorCode
from .ports import (
    AuditPort,
    ConsumedRecord,
    ConsumerPort,
    DlqPort,
    EventHandlerPort,
    IdempotencyStorePort,
    ProducerPort,
    PublishedEventStorePort,
    SubscriptionStorePort,
    TopicAdminPort,
    WakeHookPort,
)
from .topics import (
    PLUGIN_EVENTS_TOPIC,
    dlq_topic,
    ensure_dlq_topic,
    ensure_topic,
    partition_key,
)

logger = logging.getLogger(__name__)

# wake_hook может быть как объектом WakeHookPort, так и обычным async-колбэком
# (задача 09 ещё не создана — не завязываемся на конкретный класс).
WakeHook = WakeHookPort | Callable[[PluginEvent, Subscription], Awaitable[None]]

__all__ = ["PluginEventDispatcher", "WakeHook"]


class PluginEventDispatcher:
    """Доставка событий плагинов через Redpanda dispatch (без потерь).

    Все зависимости — порты из :mod:`ports`. Адаптеры передаются снаружи (F11);
    ``None`` вместо обязательного порта — ``MISSING_DEPENDENCY`` при первом же
    вызове, а не «тихий» режим буферизации.

    ``consume`` обрабатывает ОДНУ запись за вызов (одну итерацию dispatch-loop):
    это делает контракт ``poll → commit/seek`` проверяемым в unit-тестах и
    позволяет внешнему воркеру самому решать, когда останавливаться. Событие не
    теряется при «простое»: пока нет ACK, ``commit`` не вызывается вовсе, а при
    следующем старте ``earliest`` вернёт консьюмера к некоммитнутой позиции.
    """

    def __init__(
        self,
        *,
        producer: ProducerPort | None = None,
        consumer: ConsumerPort | None = None,
        topic_admin: TopicAdminPort | None = None,
        dlq: DlqPort | None = None,
        subscriptions: SubscriptionStorePort | None = None,
        published_events: PublishedEventStorePort | None = None,
        idempotency: IdempotencyStorePort | None = None,
        audit: AuditPort | None = None,
        handler: EventHandlerPort | None = None,
        wake_hook: WakeHook | None = None,
        topic: str = PLUGIN_EVENTS_TOPIC,
    ) -> None:
        self._producer = producer
        self._consumer = consumer
        self._topic_admin = topic_admin
        self._dlq = dlq
        self._subscriptions = subscriptions
        self._published_events = published_events
        self._idempotency = idempotency
        self._audit = audit
        self._handler = handler
        self._wake_hook = wake_hook
        self._topic = topic
        # Топики подготавливаются один раз: ensure_topic обязателен (auto_create
        # выключен), но не должен дёргать Admin API на каждое событие.
        self._topics_ready = False

    @property
    def topic(self) -> str:
        return self._topic

    @property
    def consumer_settings(self) -> dict[str, Any]:
        """Канонический конфиг консьюмера (для адаптера и acceptance-теста)."""

        return {**consumer_config(), "topic": self._topic}

    # ------------------------------------------------------------------
    # Внутренние проверки (fail-closed)
    # ------------------------------------------------------------------

    @staticmethod
    def _require(dependency: Any, name: str) -> Any:
        if dependency is None:
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                f"{name} обязателен: молчаливый пропуск доставки запрещён",
            )
        return dependency

    async def _ensure_topics_ready(self) -> str:
        """Подготовить основной топик и DLQ ДО любой отправки/чтения.

        auto_create_topics_enabled=false (kafka.yaml:221) — топик плагинов и
        DLQ не создаются init-Job'ом, поэтому единственный легальный путь —
        ``TopicAdminPort``. Ошибка пробрасывается, публикация не начинается.
        """

        admin = self._require(self._topic_admin, "TopicAdminPort")
        if not self._topics_ready:
            await ensure_topic(admin, self._topic)
            await ensure_dlq_topic(admin, self._topic)
            self._topics_ready = True
        return self._topic

    @staticmethod
    def _safe_error_kind(exc: BaseException) -> str:
        """Только имя класса исключения: без текста, payload, URL, токенов."""

        return type(exc).__name__

    async def _audit_received(
        self, event: PluginEvent, subscription: Subscription, *, duplicate: bool = False
    ) -> None:
        """``plugin_event_received``: только id/digest, без raw payload (§4.5)."""

        audit = self._require(self._audit, "AuditPort")
        await audit.append(
            AUDIT_PLUGIN_EVENT_RECEIVED,
            details={
                "event_id": event.event_id,
                "source_event_id": event.source_id,
                "event_type": event.event_type,
                "company_id": event.company_id,
                "plugin_id": event.plugin_id or "",
                "subscription_id": subscription.id,
                "consumer_group": subscription.consumer_group,
                "payload_digest": event.payload_digest,
                "run_id": event.run_id,
                "correlation_id": event.correlation_id,
                "duplicate": duplicate,
                "topic": self._topic,
            },
        )

    async def _audit_dlq(
        self,
        event: PluginEvent,
        subscription: Subscription,
        *,
        attempts: int,
        error_kind: str | None,
    ) -> None:
        """``plugin_event_dlq``: фиксирует event_id и run_id (criterion 11/20)."""

        audit = self._require(self._audit, "AuditPort")
        await audit.append(
            AUDIT_PLUGIN_EVENT_DLQ,
            details={
                "event_id": event.event_id,
                "source_event_id": event.source_id,
                "event_type": event.event_type,
                "company_id": event.company_id,
                "plugin_id": event.plugin_id or "",
                "subscription_id": subscription.id,
                "consumer_group": subscription.consumer_group,
                "payload_digest": event.payload_digest,
                "run_id": event.run_id,
                "correlation_id": event.correlation_id,
                "attempts": attempts,
                "error_kind": error_kind or "",
                "dlq_topic": dlq_topic(self._topic),
                "topic": self._topic,
            },
        )

    # ------------------------------------------------------------------
    # Публикация
    # ------------------------------------------------------------------

    async def publish(self, event: PluginEvent) -> bool:
        """Опубликовать событие. Возвращает ``True`` при новой отправке.

        * идемпотентность ПРОДЮСЕРА по ``event_id``: повтор → ``False`` БЕЗ
          повторной отправки (дубликат не считается ошибкой);
        * ключ партиции = ``company_id`` (§7.3); в wire уходит ``to_wire()``
          только с ``payload_digest``, без raw payload;
        * топик и DLQ подготовлены заранее (``ensure_topic`` обязателен);
        * ``flush`` до ``remember``: запоминаем ``event_id`` только после
          подтверждённой отправки, иначе сбой продюсера «съел» бы событие.

        Любая ошибка порта → ``PluginEventError`` (fail-closed), без тихого
        «и так сойдёт».
        """

        if not isinstance(event, PluginEvent):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "publish ожидает PluginEvent",
            )
        producer = self._require(self._producer, "ProducerPort")
        store = self._require(self._published_events, "PublishedEventStorePort")

        await self._ensure_topics_ready()

        if await store.seen(event.event_id):
            logger.info(
                "plugin event: duplicate publish suppressed event_id=%s", event.event_id
            )
            return False

        key = partition_key(event.company_id)
        try:
            await producer.send(
                topic=self._topic,
                key=key,
                value=event.to_wire(),
                headers={"event_id": event.event_id, "schema_version": str(event.schema_version)},
            )
            await producer.flush()
            await store.remember(event.event_id)
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.PUBLISH_FAILED,
                f"публикация {event.event_id} не удалась: {self._safe_error_kind(exc)}",
            ) from exc
        return True

    # ------------------------------------------------------------------
    # Потребление (одна запись за вызов)
    # ------------------------------------------------------------------

    async def consume(self, subscription: Subscription) -> DeliveryOutcome | None:
        """Прочитать и обработать ОДНУ запись для подписки.

        Возврат:
          * ``ACK``   — обработчик принял событие, offset ЗАКОММИЧЕН;
          * ``RETRY`` — временная ошибка, offset НЕ коммитится (будет повтор);
          * ``DLQ``   — 3 неудачи, событие записано в DLQ, offset ЗАКОММИЧЕН;
          * ``None``  — записей нет (простой консьюмера; НИКАКОГО коммита).

        Гарантия «не теряем при простое»: ``poll`` вернул запись → до успеха
        ``commit`` не вызывается. Если воркер уснул/упал в любой точке до
        коммита, при следующем старте ``earliest`` + не-закоммиченный offset
        вернёт ту же запись. ``seek`` откатывает позицию при каждом RETRY и при
        любом сбое инфраструктурного порта.
        """

        if not isinstance(subscription, Subscription):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "consume ожидает Subscription",
            )
        consumer = self._require(self._consumer, "ConsumerPort")
        subscriptions = self._require(self._subscriptions, "SubscriptionStorePort")
        idem = self._require(self._idempotency, "IdempotencyStorePort")
        handler = self._require(self._handler, "EventHandlerPort")
        dlq = self._require(self._dlq, "DlqPort")
        wake_hook = self._require(self._wake_hook, "WakeHookPort/callable")

        await self._ensure_topics_ready()

        # Fail-closed: подписка должна существовать и быть активной. Иначе
        # revocation/uninstall продолжил бы получать события.
        known = await subscriptions.get(subscription.id)
        if known is None or not await subscriptions.is_active(subscription.id):
            raise PluginEventError(
                PluginEventErrorCode.UNKNOWN_SUBSCRIPTION,
                f"подписка {subscription.id!r} не найдена или отозвана",
            )

        record: ConsumedRecord | None = await consumer.poll()
        if record is None:
            # Простой консьюмера: НЕ коммитим ничего. Это и есть отличие от
            # as-built kick-режима, который коммитил offset до обработки.
            return None

        offset = record.offset
        try:
            event = PluginEvent.from_wire(record.value)
        except PluginEventError:
            await consumer.seek(offset)
            raise

        idem_key = IdempotencyKey(
            subscription_id=subscription.id,
            source_event_id=event.source_id,
        )
        # Дедупликация по (subscription_id, source_event_id), НЕ по URL/hash тела.
        if await idem.seen(idem_key):
            await self._audit_received(event, subscription, duplicate=True)
            await consumer.commit(offset)
            return DeliveryOutcome.ACK

        if not subscription.accepts(event.event_type):
            # Не наш тип: ACK без вызова обработчика (свой offset всё равно
            # коммитится, чтобы не блокировать группу).
            await self._audit_received(event, subscription)
            await idem.remember(idem_key)
            await consumer.commit(offset)
            return DeliveryOutcome.ACK

        last_error_kind: str | None = None
        for attempt_no in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            try:
                # wake_hook ДО доставки: backend плагина в replicas: 0 должен
                # стать Ready, иначе handler получит connection-refused (§7.4).
                await self._call_wake(wake_hook, event, subscription)
                await handler.handle(event, subscription=subscription)
                attempt = DeliveryAttempt(outcome=DeliveryOutcome.ACK, attempt=attempt_no)
            except Exception as exc:
                last_error_kind = self._safe_error_kind(exc)
                logger.info(
                    "plugin event: handler failed event_id=%s attempt=%s error=%s",
                    event.event_id,
                    attempt_no,
                    last_error_kind,
                )
                attempt = DeliveryAttempt(
                    outcome=DeliveryOutcome.RETRY,
                    attempt=attempt_no,
                    error_kind=last_error_kind,
                )

            outcome = delivery_outcome_for_attempt(attempt)
            if outcome is DeliveryOutcome.ACK:
                await self._audit_received(event, subscription)
                await idem.remember(idem_key)
                await consumer.commit(offset)
                return DeliveryOutcome.ACK

            if outcome is DeliveryOutcome.RETRY:
                # offset НЕ коммитим; возвращаем позицию к этой же записи.
                await consumer.seek(offset)
                continue

            # DLQ: ровно после MAX_DELIVERY_ATTEMPTS неудачных попыток.
            await self._send_to_dlq(
                dlq=dlq,
                event=event,
                subscription=subscription,
                error_kind=last_error_kind,
            )
            await self._audit_dlq(
                event,
                subscription,
                attempts=MAX_DELIVERY_ATTEMPTS,
                error_kind=last_error_kind,
            )
            await idem.remember(idem_key)
            # Коммит ТОЛЬКО после подтверждённой записи в DLQ и аудита.
            await consumer.commit(offset)
            return DeliveryOutcome.DLQ

        # Недостижимо: цикл всегда заканчивается ACK/RETRY/DLQ. Оставлено как
        # fail-closed предохранитель на случай правки MAX_DELIVERY_ATTEMPTS.
        await consumer.seek(offset)
        raise PluginEventError(
            PluginEventErrorCode.CONSUME_FAILED,
            f"доставка {event.event_id} завершилась без исхода",
        )

    async def _call_wake(
        self,
        wake_hook: WakeHook,
        event: PluginEvent,
        subscription: Subscription,
    ) -> None:
        """Вызвать wake-колбэк (объект задачи 09 или async callable).

        Важно: wake выполняется ДО ``handler.handle`` и ДО ``commit``. Ошибка
        wake приравнена к ошибке обработчика → RETRY (offset не коммитится).
        """

        try:
            if hasattr(wake_hook, "wake"):
                await wake_hook.wake(event, subscription=subscription)  # type: ignore[union-attr]
            else:
                await wake_hook(event, subscription)  # type: ignore[operator]
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                f"пробуждение backend не удалось: {self._safe_error_kind(exc)}",
            ) from exc

    async def _send_to_dlq(
        self,
        *,
        dlq: DlqPort,
        event: PluginEvent,
        subscription: Subscription,
        error_kind: str | None,
    ) -> None:
        """Записать DLQ-событие с сохранением ``event_id`` и ``run_id``.

        Никакого raw payload: :class:`DlqRecord` несёт ``payload_digest``.
        Ошибка записи → ``DLQ_FAILED`` и коммит НЕ выполняется.
        """

        record = DlqRecord(
            event=event,
            subscription_id=subscription.id,
            topic=self._topic,
            dlq_topic=dlq_topic(self._topic),
            attempts=MAX_DELIVERY_ATTEMPTS,
            error_kind=error_kind,
        )
        try:
            await dlq.publish(record)
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.DLQ_FAILED,
                f"запись в DLQ не удалась: {self._safe_error_kind(exc)}",
            ) from exc
