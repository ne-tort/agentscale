"""Порты (Protocol) шины событий плагинов (F7, задача 10, дизайн v4 §7.3).

Гексагональная граница: ``PluginEventDispatcher`` знает ТОЛЬКО эти протоколы и
не знает ни ``aiokafka``, ни ``KafkaManager``, ни ``webhook_deliveries``.
Адаптеры (задача родителя / F11) подключаются снаружи; отсутствие адаптера —
``MISSING_DEPENDENCY``, а не «буферизуем в памяти и забудем» (fail-closed).

Почему ``ConsumerPort`` объявлен с ЯВНЫМИ ``commit`` / ``seek`` / ``poll``:
as-built ``KafkaManager`` использует ``enable_auto_commit=True`` и не даёт
диспечеру управлять offset'ом — именно это теряет события при простое
консьюмера (criterion 20, §11; §13.3). Здесь коммит — осознанное действие
диспечера после ACK или после записи в DLQ, а ``seek`` возвращает позицию при
любой ошибке, чтобы событие было прочитано снова.

Ни один метод не принимает raw payload: только :class:`PluginEvent` (с
``payload_digest``), его wire-dict, offset и короткие технические метаданные.
Celery-outbox ``webhook_deliveries`` НЕ участвует: он остаётся исключительно
для исходящих вебхуков (``application/external/webhooks.py``).
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import (
    DlqRecord,
    IdempotencyKey,
    PluginEvent,
    Subscription,
)

# ---------------------------------------------------------------------------
# Вспомогательные value-объекты портов
# ---------------------------------------------------------------------------


@runtime_checkable
class ConsumedRecord(Protocol):
    """Минимальный контракт записи, возвращаемой ``ConsumerPort.poll``.

    Адаптер aiokafka подставляет ``ConsumerRecord`` как есть (у него есть
    ``value``/``key``/``offset``/``partition``/``topic``), а в unit-тестах
    используется простая замена. Диспечер читает только эти атрибуты.
    """

    @property
    def topic(self) -> str: ...

    @property
    def partition(self) -> int: ...

    @property
    def offset(self) -> int: ...

    @property
    def key(self) -> bytes | None: ...

    @property
    def value(self) -> Mapping[str, Any]: ...


# ---------------------------------------------------------------------------
# Producer / Consumer
# ---------------------------------------------------------------------------


@runtime_checkable
class ProducerPort(Protocol):
    """Публикация событий плагинов в Redpanda.

    Контракт намеренно узкий: адаптер сам применяет ``enable_idempotence``,
    ``acks="all"`` и кодирует ``key``. Диспечер передаёт ключ партиции УЖЕ
    равным ``company_id`` (через ``partition_key``), поэтому адаптер не должен его
    хэшировать или подменять.
    """

    async def send(
        self,
        *,
        topic: str,
        key: str,
        value: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Отправить запись. Любая ошибка → исключение (fail-closed)."""

        ...

    async def flush(self) -> None:
        """Дождаться подтверждения всех отправок (до возврата из ``publish``)."""

        ...


@runtime_checkable
class ConsumerPort(Protocol):
    """Консьюмер с РУЧНЫМ управлением offset'ом.

    Обязательная конфигурация адаптера (``consumer_config()``):
    ``auto_offset_reset="earliest"``, ``enable_auto_commit=False``.
    Никакого ``KAFKA_CONSUMER_MODE=kick``: режим — dispatch, то есть обработка
    КАЖДОЙ записи, а не дебаунс-будильник.

    Контракт ``poll`` → ``commit`` / ``seek``:

    * ``poll`` возвращает одну запись или ``None`` (простой консьюмера);
    * ``commit`` вызывается ТОЛЬКО после успешной обработки (ACK) или после
      подтверждённой записи в DLQ;
    * ``seek`` возвращает позицию к этому же offset'у, чтобы при ошибке запись
      была доставлена повторно, а не «проскочила» мимо.
    """

    async def poll(self, *, timeout_ms: int = 1000) -> ConsumedRecord | None:
        """Прочитать одну запись без авто-коммита. ``None`` = нет записей."""

        ...

    async def commit(self, offset: int) -> None:
        """Зафиксировать offset ТОЛЬКО для обработанной записи.

        Явный параметр ``offset`` (а не «закоммитить текущую позицию»)
        исключает случайный коммит следующей, ещё не обработанной записи.
        """

        ...

    async def seek(self, offset: int) -> None:
        """Вернуться к указанному offset'у (откат при RETRY/ошибке портов)."""

        ...

    async def close(self) -> None:
        """Остановить консьюмер; не коммитит необработанные offset'ы."""

        ...


# ---------------------------------------------------------------------------
# DLQ / Topic admin
# ---------------------------------------------------------------------------


@runtime_checkable
class DlqPort(Protocol):
    """Запись «мёртвых» событий в DLQ-топик.

    Вызов должен быть подтверждённым (flush), иначе коммит offset'а недопустим:
    событие исчезло бы из основного топика и не появилось в DLQ.
    """

    async def publish(self, record: DlqRecord) -> None:
        """Записать событие в DLQ с сохранением ``event_id`` и ``run_id``."""

        ...


@runtime_checkable
class TopicAdminPort(Protocol):
    """Администрирование топиков (обязателен при auto-create=false).

    См. ``infra/k3s/base/platform/kafka.yaml:221``:
    ``rpk cluster config set auto_create_topics_enabled false``. Без этого
    порта ``ensure_topic`` отказывает, и публикация не начинается.
    """

    async def topic_exists(self, topic: str) -> bool:
        """Существует ли топик (без создания)."""

        ...

    async def create_topic(
        self,
        topic: str,
        *,
        num_partitions: int,
        replication_factor: int,
    ) -> None:
        """Создать топик явно. RF=1 для single-node Redpanda."""

        ...


# ---------------------------------------------------------------------------
# Хранилища состояния
# ---------------------------------------------------------------------------


@runtime_checkable
class SubscriptionStorePort(Protocol):
    """Реестр подписок плагинов (``POST /gateway/events/subscribe``).

    Fail-closed: неизвестная/неактивная подписка → ``None``, диспечер отказывает
    в обработке, а не подставляет группу «по умолчанию».
    """

    async def get(self, subscription_id: str) -> Subscription | None:
        """Вернуть подписку или ``None``."""

        ...

    async def is_active(self, subscription_id: str) -> bool:
        """Активна ли подписка (revoked/uninstalled → False)."""

        ...


@runtime_checkable
class IdempotencyStorePort(Protocol):
    """Дедупликация КОНСЬЮМЕРА по ``(subscription_id, source_event_id)``.

    НЕ по URL, НЕ по hash тела, НЕ по ``event_id`` (§7.3). Операция ``remember``
    обязана быть атомарной: повторная доставка того же исходного события не
    должна вызвать обработчик второй раз даже при конкурентных консьюмерах.
    """

    async def seen(self, key: IdempotencyKey) -> bool:
        """Уже обработано ли событие этой подпиской."""

        ...

    async def remember(self, key: IdempotencyKey) -> None:
        """Отметить событие обработанным (после ACK или после записи в DLQ)."""

        ...


@runtime_checkable
class PublishedEventStorePort(Protocol):
    """Дедупликация ПРОДЮСЕРА по ``event_id`` (защита от ре-паблиша).

    Отдельный порт от ``IdempotencyStorePort``: у продюсера и консьюмера
    РАЗНЫЕ ключи идемпотентности. Продюсер не должен «съесть» событие, которое
    консьюмер ещё не видел.
    """

    async def seen(self, event_id: str) -> bool:
        """Публиковалось ли уже событие с таким ``event_id``."""

        ...

    async def remember(self, event_id: str) -> None:
        """Запомнить ``event_id`` ПОСЛЕ подтверждённой отправки."""

        ...


# ---------------------------------------------------------------------------
# Доставка / wake-up / аудит
# ---------------------------------------------------------------------------


@runtime_checkable
class EventHandlerPort(Protocol):
    """Обработчик события на стороне backend плагина (``POST /events/*``)."""

    async def handle(self, event: PluginEvent, *, subscription: Subscription) -> None:
        """Успех = отсутствие исключения; любое исключение → RETRY/DLQ."""

        ...


@runtime_checkable
class WakeHookPort(Protocol):
    """Пробуждение backend плагина ДО доставки (интеграция с задачей 09).

    §7.3/§7.4: wake-up триггеры включают «событие до доставки». Gateway/
    activator (``application/plugins/activation/**``, задача 09) владеет
    lifecycle: диспечер только вызывает колбэк и НЕ проксирует запрос сам.
    Fail-closed: если wake не удался, доставка НЕ начинается и offset НЕ
    коммитится — иначе backend в ``replicas: 0`` получит connection-refused.
    """

    async def wake(self, event: PluginEvent, *, subscription: Subscription) -> None:
        """Разбудить backend и дождаться Ready; ошибка → исключение."""

        ...


@runtime_checkable
class AuditPort(Protocol):
    """Append-only аудит (§4.5). Только канонические id и digest-ы.

    Запрещено передавать raw SQL, параметры, тела payload, токены, секреты,
    PII (criterion 11). ``details`` — плоский dict простых скаляров; диспечер
    сам формирует безопасный набор полей.
    """

    async def append(self, event_type: str, *, details: Mapping[str, Any]) -> None:
        """Добавить запись аудита. Ошибка → исключение (fail-closed)."""

        ...


__all__ = [
    "AuditPort",
    "ConsumedRecord",
    "ConsumerPort",
    "DlqPort",
    "EventHandlerPort",
    "IdempotencyStorePort",
    "ProducerPort",
    "PublishedEventStorePort",
    "SubscriptionStorePort",
    "TopicAdminPort",
    "WakeHookPort",
]
