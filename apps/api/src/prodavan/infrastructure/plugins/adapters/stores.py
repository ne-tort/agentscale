"""In-memory реализации портов состояния событий плагинов (F7, задача 10).

.. warning:: **ТОЛЬКО для dev и unit-тестов.**

   Для кластера (k3s/dev-контур, несколько реплик Gateway/Consumer) эти классы
   **непригодны**: состояние живёт в памяти одного процесса, поэтому при
   рестарте/ребалансе теряется идемпотентность продюсера/консьюмера, реестр
   подписок и audit-журнал. В кластере каждый из портов обязан быть подменён на
   **БД (PostgreSQL, append-only audit) или Redis (TTL-ключи идемпотентности)** —
   это интеграционная задача **A10** родителя
   (``acceptance-map.md`` → «Родительская интеграция (Fa)», строка A10:
   «Модели БД для CapabilityGrant/PluginInstance + Alembic-ревизия»).

   Ни один in-memory класс НЕ подставляется ``build_event_dispatcher`` молча:
   сборка fail-closed и требует явной передачи каждого порта. Значения по
   умолчанию собраны в :func:`in_memory_event_ports` и используются только
   тестами/явным dev-выбором композитора.

См. также ``docs/ARCHITECTURE/plugin-architecture-complete.md`` §4.5 (append-only
аудит), §7.3 (``(subscription_id, source_event_id)``), §7.4 (wake до доставки).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from prodavan.application.plugins.events.contracts import (
    IdempotencyKey,
    PluginEvent,
    Subscription,
)

# Явная пометка для ревью/родителя: в кластере — БД/Redis, задача A10.
CLUSTER_BACKEND_NOTE = (
    "In-memory только для dev/тестов; в кластере заменить на БД/Redis (задача A10, "
    "acceptance-map.md «Родительская интеграция (Fa)»)."
)

__all__ = [
    "CLUSTER_BACKEND_NOTE",
    "EventInstanceResolver",
    "InMemoryAudit",
    "InMemoryEventPorts",
    "InMemoryIdempotencyStore",
    "InMemoryPublishedEventStore",
    "InMemorySubscriptionStore",
    "InMemoryWakeHook",
    "InstanceActivatorLike",
    "InstanceMappingError",
    "event_instance_resolver",
    "in_memory_event_ports",
]


# ---------------------------------------------------------------------------
# Вспомогательные контракты (структурные, без импорта чужого каталога)
# ---------------------------------------------------------------------------


@runtime_checkable
class InstanceActivatorLike(Protocol):
    """Структурный минимум ``PluginActivator.wake`` (задача 09).

    ``WakeResult`` намеренно не импортируется: адаптеры не должны зависеть от
    доменных типов задачи 09 (порт ``WakeHookPort`` требует только «не упал»).
    """

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> Any: ...


class InstanceMappingError(RuntimeError):
    """Не удалось отобразить событие в ``instance_id`` (fail-closed).

    Пробуждение без точного ``instance_id`` недопустимо: иначе activator поднимет
    не тот Deployment и handler получит connection-refused (§7.4).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: Резолвер события → ``instance_id``. Возврат ``None`` = отказать.
EventInstanceResolver = Callable[[PluginEvent, Subscription], str | None]


def event_instance_resolver(
    mapping: Mapping[tuple[str, str], str],
) -> EventInstanceResolver:
    """Построить резолвер ``(plugin_id, company_id) → instance_id``.

    Ключ берётся из ``event.plugin_id`` с фолбэком на ``subscription.plugin_id``:
    широковещательное событие (``plugin_id=None``) всё равно обязано быть
    адресовано конкретной подписке. Неизвестная пара → ``None`` → отказ.
    """

    def _resolve(event: PluginEvent, subscription: Subscription) -> str | None:
        plugin_id = event.plugin_id or subscription.plugin_id
        instance_id = mapping.get((plugin_id, event.company_id))
        if isinstance(instance_id, str) and instance_id.strip():
            return instance_id.strip()
        return None

    return _resolve


# ---------------------------------------------------------------------------
# SubscriptionStorePort
# ---------------------------------------------------------------------------


class InMemorySubscriptionStore:
    """Реестр подписок в памяти. Кластер → БД (задача A10).

    Fail-closed: неизвестная или отозванная подписка даёт ``None``/``False``,
    диспечер отказывает в обработке, а не подставляет группу «по умолчанию».
    """

    def __init__(
        self,
        subscriptions: Mapping[str, Subscription] | None = None,
        *,
        active: Mapping[str, bool] | None = None,
    ) -> None:
        self._subscriptions: dict[str, Subscription] = dict(subscriptions or {})
        self._active: dict[str, bool] = dict(active or {})
        self.get_calls: list[str] = []
        self.active_calls: list[str] = []

    async def get(self, subscription_id: str) -> Subscription | None:
        self.get_calls.append(subscription_id)
        return self._subscriptions.get(subscription_id)

    async def is_active(self, subscription_id: str) -> bool:
        self.active_calls.append(subscription_id)
        # Отсутствие записи об активности = НЕ активна (fail-closed).
        return self._active.get(subscription_id, False)

    # -- dev/тестовые мутации (не часть порта) --------------------------------

    def register(self, subscription: Subscription, *, active: bool = True) -> None:
        if not isinstance(subscription, Subscription):
            raise TypeError("register expects Subscription")
        self._subscriptions[subscription.id] = subscription
        self._active[subscription.id] = bool(active)

    def revoke(self, subscription_id: str) -> None:
        self._active[subscription_id] = False


# ---------------------------------------------------------------------------
# IdempotencyStorePort — ключ (subscription_id, source_event_id), НЕ body/URL
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore:
    """Дедупликация консьюмера в памяти. Кластер → БД/Redis (задача A10).

    ``remember`` идемпотентен (повторная отметка того же ключа не ошибка):
    диспечер вызывает его и после ACK, и после записи в DLQ.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self.remembered: list[tuple[str, str]] = []

    async def seen(self, key: IdempotencyKey) -> bool:
        if not isinstance(key, IdempotencyKey):
            return False  # fail-closed: мусорный ключ не считаем «уже видели»
        return key.as_tuple() in self._seen

    async def remember(self, key: IdempotencyKey) -> None:
        if not isinstance(key, IdempotencyKey):
            raise TypeError("remember expects IdempotencyKey")
        tuple_key = key.as_tuple()
        if tuple_key not in self._seen:
            self._seen.add(tuple_key)
            self.remembered.append(tuple_key)


# ---------------------------------------------------------------------------
# PublishedEventStorePort — ключ event_id ПРОДЮСЕРА
# ---------------------------------------------------------------------------


class InMemoryPublishedEventStore:
    """Дедупликация продюсера по ``event_id``. Кластер → БД/Redis (задача A10).

    Ключ намеренно отделён от консьюмерского: продюсер не должен «съесть»
    событие, которое адресат ещё не видел (§7.3). TTL в кластере задаётся
    Redis ``SET NX EX``; здесь хранится бессрочно в рамках процесса.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.remembered: list[str] = []

    async def seen(self, event_id: str) -> bool:
        return isinstance(event_id, str) and event_id in self._seen

    async def remember(self, event_id: str) -> None:
        if not isinstance(event_id, str) or not event_id.strip():
            raise TypeError("remember expects a non-empty event_id")
        if event_id not in self._seen:
            self._seen.add(event_id)
            self.remembered.append(event_id)


# ---------------------------------------------------------------------------
# WakeHookPort — вызов activator'а ДО handler'а (§7.3/§7.4)
# ---------------------------------------------------------------------------


class InMemoryWakeHook:
    """Пробуждение backend'а плагина перед доставкой события.

    «In-memory» означает, что колбэк держит прямую ссылку на ``PluginActivator``
    (задача 09) в процессе, а не публикует wake-команду в долговечную шину/БД.
    Кластер → отдельный durable wake-job + БД/Redis состояния (задача A10).

    Fail-closed:
      * событие не отобразилось в ``instance_id`` → ``InstanceMappingError``;
      * activator.wake поднял исключение → оно пробрасывается, диспечер делает
        RETRY и **НЕ коммитит** offset;
      * вызов фиксируется в ``calls`` ДО обращения к activator'у, чтобы тест
        «WakeHook вызывается до handler» проверял именно порядок.
    """

    def __init__(
        self,
        *,
        activator: InstanceActivatorLike,
        instance_id_for: EventInstanceResolver,
        timeout: float | None = None,
    ) -> None:
        if activator is None or not callable(getattr(activator, "wake", None)):
            raise ValueError("InMemoryWakeHook requires an activator with wake()")
        if instance_id_for is None:
            raise ValueError("InMemoryWakeHook requires an instance_id resolver")
        self._activator = activator
        self._instance_id_for = instance_id_for
        self._timeout = timeout
        self.calls: list[tuple[str, PluginEvent, Subscription]] = []

    async def wake(self, event: PluginEvent, *, subscription: Subscription) -> None:
        instance_id = self._instance_id_for(event, subscription)
        if not instance_id:
            raise InstanceMappingError(
                "не удалось отобразить событие в plugin instance_id; "
                "доставка без пробуждения запрещена (§7.4)"
            )
        self.calls.append((instance_id, event, subscription))
        await self._activator.wake(instance_id, timeout=self._timeout)


# ---------------------------------------------------------------------------
# AuditPort — append-only для тестов (КЛАСТЕР: БД, задача A10)
# ---------------------------------------------------------------------------


class InMemoryAudit:
    """Append-only аудит в памяти для unit-тестов. Кластер → БД (задача A10).

    Удовлетворяет **обоим** контрактам аудита стыка:

    * ``events.AuditPort.append(event_type, *, details)`` — ``entries``;
    * синк для ``gateway_adapters.AuditAdapter.write(record)`` — ``records``
      (уже каноническая схема §4.5 после redaction).

    ``fail=True`` имитирует недоступный sink: оба метода бросают исключение,
    что ``GatewayAuth._audit`` / ``PluginEventDispatcher`` трактуют как
    deny/system failure (§4.5), а не как «записали, ладно».
    """

    def __init__(self, *, fail: bool = False, clock: Callable[[], float] = time.time) -> None:
        self.fail = fail
        self._clock = clock
        self.entries: list[tuple[str, dict[str, Any]]] = []
        self.records: list[dict[str, Any]] = []
        self.appended_at: list[float] = []

    def _guard(self) -> None:
        if self.fail:
            raise RuntimeError("audit sink is unavailable (fail-closed)")

    async def append(self, event_type: str, *, details: Mapping[str, Any]) -> None:
        self._guard()
        if not isinstance(event_type, str) or not event_type.strip():
            raise TypeError("event_type must be a non-empty string")
        # Копируем: вызывающий не должен менять уже записанное событие.
        self.entries.append((event_type, dict(details)))
        self.appended_at.append(self._clock())

    async def write(self, record: Mapping[str, Any]) -> None:
        self._guard()
        if not isinstance(record, Mapping):
            raise TypeError("audit record must be a mapping")
        self.records.append(dict(record))
        self.appended_at.append(self._clock())

    @property
    def total(self) -> int:
        return len(self.entries) + len(self.records)


# ---------------------------------------------------------------------------
# Явный «dev-набор» портов. НИКОГДА не подставляется сборщиком молча.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InMemoryEventPorts:
    """Набор in-memory портов для тестов/явного dev-выбора."""

    subscriptions: InMemorySubscriptionStore
    published_events: InMemoryPublishedEventStore
    idempotency: InMemoryIdempotencyStore
    audit: InMemoryAudit
    wake_hook: InMemoryWakeHook


def in_memory_event_ports(
    *,
    activator: InstanceActivatorLike,
    instance_mapping: Mapping[tuple[str, str], str],
    subscriptions: Mapping[str, Subscription] | None = None,
    active: Mapping[str, bool] | None = None,
    activator_timeout: float | None = None,
    audit_fail: bool = False,
) -> InMemoryEventPorts:
    """Собрать полный in-memory набор (для тестов).

    ВАЖНО: ``build_event_dispatcher`` **не вызывает** эту функцию сам —
    отсутствие любого порта остаётся явной ошибкой композиции (fail-closed).
    """

    return InMemoryEventPorts(
        subscriptions=InMemorySubscriptionStore(subscriptions, active=active),
        published_events=InMemoryPublishedEventStore(),
        idempotency=InMemoryIdempotencyStore(),
        audit=InMemoryAudit(fail=audit_fail),
        wake_hook=InMemoryWakeHook(
            activator=activator,
            instance_id_for=event_instance_resolver(instance_mapping),
            timeout=activator_timeout,
        ),
    )
