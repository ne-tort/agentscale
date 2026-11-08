"""Контракты шины событий плагинов (F7, задача 10, дизайн v4 §7.3/§4.5).

Ключевые правила, зафиксированные здесь в типах:

* ``PluginEvent`` — frozen/slots, НЕ содержит raw payload. В шину уходит только
  ``payload_digest`` (``sha256:<64 hex>``), как требует §4.5 / criterion 11:
  «audit-событие не содержит raw SQL, параметров, токенов, payload». Сам payload
  живёт в tenant-БД/объектном хранилище плагина и достаётся адресатом по
  ``source`` + ``event_id`` вне этого контракта.
* ``source_event_id`` вместо ``event_id`` в ключе идемпотентности консьюмера:
  платформенный ``event_id`` присваивается при публикации, а адресат
  дедуплицирует по идентификатору ИСТОЧНИКА. Ключ — ``(subscription_id,
  source_event_id)``, а НЕ URL и НЕ hash тела (§7.3). Hash тела меняется при
  редактировании данных и не защищает от повторной доставки одного и того же
  события.
* ``Subscription.consumer_group`` — отдельная группа на подписку: так несколько
  плагинов читают один топик ``agentscale.plugin.events`` независимо и каждый
  коммитит СВОЙ offset (ручной commit, §7.3).
* ``DeliveryOutcome`` — три исхода обработки: ACK / RETRY / DLQ. Коммит
  выполняется ТОЛЬКО для ACK и DLQ (после записи в DLQ), никогда для RETRY.

Все объекты fail-closed: пустые обязательные поля, некорректный digest,
неподдерживаемая версия схемы и неправильные enum → ``ValueError`` /
``PluginEventError`` при конструировании, а не «как-нибудь обработаем».
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from .errors import PluginEventError, PluginEventErrorCode

# Единственная поддерживаемая версия wire-схемы события.
PLUGIN_EVENT_SCHEMA_VERSION = 1

# Канонический digest: ``sha256:<64 lowercase hex>``.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# Простые идентификаторы без управляющих символов/переводов строк (защита
# ключей Kafka, заголовков и audit-полей от инъекций).
_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
# Жёстко фиксируем конфигурацию консьюмера плагинов (acceptance №20, §11).
# Изменить эти значения можно только правкой кода + обновлением теста:
# ``test_dispatch.py::test_consumer_config_is_earliest_and_manual_commit``.
CONSUMER_AUTO_OFFSET_RESET = "earliest"
CONSUMER_ENABLE_AUTO_COMMIT = False
MAX_DELIVERY_ATTEMPTS = 3

__all__ = [
    "CONSUMER_AUTO_OFFSET_RESET",
    "CONSUMER_ENABLE_AUTO_COMMIT",
    "MAX_DELIVERY_ATTEMPTS",
    "PLUGIN_EVENT_SCHEMA_VERSION",
    "AUDIT_PLUGIN_EVENT_DLQ",
    "AUDIT_PLUGIN_EVENT_RECEIVED",
    "DeliveryAttempt",
    "DeliveryOutcome",
    "DlqRecord",
    "IdempotencyKey",
    "PluginEvent",
    "Subscription",
    "canonical_digest",
    "consumer_config",
    "delivery_outcome_for_attempt",
]


# Имена audit-событий (§4.5, §7.2). Только канонические digest-ы и id,
# никогда тела/токены/URL.
AUDIT_PLUGIN_EVENT_RECEIVED = "plugin_event_received"
AUDIT_PLUGIN_EVENT_DLQ = "plugin_event_dlq"


def _require_text(value: Any, name: str) -> str:
    """Fail-closed: непустая строка без управляющих символов."""

    if not isinstance(value, str) or not value.strip():
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            f"{name} обязано быть непустой строкой",
        )
    if _TEXT_RE.fullmatch(value) is None:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            f"{name} содержит управляющие символы",
        )
    return value


def _require_optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, name)


def _require_digest(value: Any, name: str) -> str:
    """Строгий канонический digest; сами данные не принимаем нигде."""

    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            f"{name} обязано быть sha256:<64 lowercase hex>",
        )
    return value


def canonical_digest(value: Mapping[str, Any]) -> str:
    """Посчитать канонический digest JSON-совместимого отображения.

    Используется ПРОИЗВОДИТЕЛЕМ события для получения ``payload_digest`` до
    отправки: в сам ``PluginEvent`` сырые данные НЕ попадают. Канонизация —
    сортировка ключей + компактный UTF-8 JSON, чтобы digest был стабильным
    независимо от порядка ключей.

    fail-closed: не-JSON-совместимое значение → ошибка, а не ``repr``/str.
    """

    if not isinstance(value, Mapping):
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "payload_digest считается только из JSON-совместимого отображения",
        )
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "payload не JSON-совместим; digest не может быть посчитан",
        ) from exc
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_aware_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            f"{name} обязано быть timezone-aware datetime (UTC)",
        )
    return value


@dataclass(frozen=True, slots=True)
class PluginEvent:
    """Событие шины плагинов (immutable, без raw payload).

    Поля:

    * ``event_id`` — платформенный id записи, ключ идемпотентности ПРОДЮСЕРА;
    * ``event_type`` / ``source`` — тип и источник (например ``"offers"``);
    * ``company_id`` — КЛЮЧ ПАРТИЦИИ (§7.3);
    * ``plugin_id`` — адресат или ``None`` для широковещательной доставки;
    * ``subscription_id`` — подписка, для которой публикуется событие;
    * ``occurred_at`` — timezone-aware UTC;
    * ``payload_digest`` — ``sha256:...`` вместо тела (§4.5, criterion 11);
    * ``run_id`` / ``correlation_id`` — трассировка (обязательны: DLQ-запись
      и аудит должны сохранять ``run_id`` даже после 3 ретраев);
    * ``schema_version`` — только :data:`PLUGIN_EVENT_SCHEMA_VERSION`.
    """

    event_id: str
    event_type: str
    source: str
    company_id: str
    subscription_id: str
    occurred_at: datetime
    payload_digest: str
    run_id: str
    correlation_id: str
    plugin_id: str | None = None
    schema_version: int = PLUGIN_EVENT_SCHEMA_VERSION
    # Идентификатор исходной системы. Для консьюмера именно он (а не event_id,
    # не URL, не hash тела) является частью ключа идемпотентности. По умолчанию
    # совпадает с event_id, чтобы publishers из платформы не теряли источник.
    source_event_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "event_type",
            "source",
            "company_id",
            "subscription_id",
            "payload_digest",
            "run_id",
            "correlation_id",
        ):
            _require_text(getattr(self, name), name)
        _require_digest(self.payload_digest, "payload_digest")
        _require_optional_text(self.plugin_id, "plugin_id")
        _require_optional_text(self.source_event_id, "source_event_id")
        _require_aware_datetime(self.occurred_at, "occurred_at")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != PLUGIN_EVENT_SCHEMA_VERSION
        ):
            raise PluginEventError(
                PluginEventErrorCode.UNSUPPORTED_SCHEMA,
                f"schema_version={self.schema_version!r} не поддерживается",
            )
        if self.source_event_id is None:
            # Явный дефолт: один и тот же id у продюсера и у консьюмера.
            object.__setattr__(self, "source_event_id", self.event_id)

    @property
    def source_id(self) -> str:
        """Идентификатор источника для ключа ``(subscription_id, source_event_id)``."""

        # __post_init__ гарантирует не-None; аннотация сужается для адаптеров.
        return self.source_event_id or self.event_id

    def to_wire(self) -> dict[str, Any]:
        """Каноническое JSON-совместимое представление для ``ProducerPort``.

        Важно: метод НЕ добавляет ``payload`` и не принимает его ни от кого —
        в шину уходит только digest. См. criterion 11 (§11).
        """
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "source_event_id": self.source_id,
            "event_type": self.event_type,
            "source": self.source,
            "company_id": self.company_id,
            "plugin_id": self.plugin_id,
            "subscription_id": self.subscription_id,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "payload_digest": self.payload_digest,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> PluginEvent:
        """Восстановить событие из записи Kafka (fail-closed).

        Неизвестные поля (в т.ч. случайно приехавший ``payload``) ИГНОРИРУЮТСЯ
        и в контракт не попадают: сырое тело не проходит в аудит/DLQ/handler.
        """

        if not isinstance(value, Mapping):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT, "запись события должна быть объектом"
            )
        try:
            raw_occurred = value["occurred_at"]
            occurred = (
                datetime.fromisoformat(raw_occurred)
                if isinstance(raw_occurred, str)
                else raw_occurred
            )
            return cls(
                event_id=value["event_id"],
                event_type=value["event_type"],
                source=value["source"],
                company_id=value["company_id"],
                subscription_id=value["subscription_id"],
                occurred_at=occurred,
                payload_digest=value["payload_digest"],
                run_id=value["run_id"],
                correlation_id=value["correlation_id"],
                plugin_id=value.get("plugin_id"),
                schema_version=value.get("schema_version", PLUGIN_EVENT_SCHEMA_VERSION),
                source_event_id=value.get("source_event_id"),
            )
        except KeyError as exc:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                f"в записи события отсутствует обязательное поле {exc}",
            ) from exc
        except (TypeError, ValueError, PluginEventError) as exc:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                f"запись события невалидна: {exc}",
            ) from exc


class DeliveryOutcome(StrEnum):
    """Исход одной попытки доставки (§7.3, acceptance №20).

    * ``ACK``   — обработчик успешно обработал событие → коммитим offset;
    * ``RETRY`` — временная ошибка → offset НЕ коммитим, повторяем (до 3 раз);
    * ``DLQ``   — ретраи исчерпаны → пишем в DLQ, затем коммитим offset.
    """

    ACK = "ACK"
    RETRY = "RETRY"
    DLQ = "DLQ"


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """Результат вызова обработчика одной попытки.

    ``attempt`` считается с 1. ``error_kind`` — ТОЛЬКО имя класса исключения
    (короткая техническая строка), никогда текст исключения, payload, URL,
    токены: критерий 11 (§11) запрещает сырые данные в аудите/DLQ.
    """

    outcome: DeliveryOutcome
    attempt: int
    error_kind: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DeliveryOutcome):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "outcome должен быть DeliveryOutcome",
            )
        if (
            not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or not 1 <= self.attempt <= MAX_DELIVERY_ATTEMPTS
        ):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                f"attempt должен быть целым 1..{MAX_DELIVERY_ATTEMPTS}",
            )
        if self.error_kind is not None:
            _require_text(self.error_kind, "error_kind")
        if self.outcome is DeliveryOutcome.ACK and self.error_kind is not None:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "ACK не может содержать error_kind",
            )


def delivery_outcome_for_attempt(attempt: DeliveryAttempt) -> DeliveryOutcome:
    """Нормализовать исход попытки с учётом лимита ``MAX_DELIVERY_ATTEMPTS``.

    Независимо от того, что вернул обработчик на последней попытке, после 3
    неудачных попыток исход — ``DLQ``. Это единая точка принятия решения:
    диспечер не коммитит offset при ``RETRY`` и коммитит только после записи
    в DLQ при ``DLQ``.
    """

    if not isinstance(attempt, DeliveryAttempt):
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "ожидается DeliveryAttempt",
        )
    if attempt.outcome is DeliveryOutcome.ACK:
        return DeliveryOutcome.ACK
    if attempt.attempt >= MAX_DELIVERY_ATTEMPTS:
        return DeliveryOutcome.DLQ
    return DeliveryOutcome.RETRY


@dataclass(frozen=True, slots=True)
class Subscription:
    """Подписка плагина на типы событий (§7.3: ``/gateway/events/subscribe``).

    ``consumer_group`` — ОТДЕЛЬНАЯ Kafka consumer group на подписку. Именно
    она позволяет читать один топик ``agentscale.plugin.events`` несколькими
    плагинами без «будильника»: у каждой группы свой offset, который коммитится
    вручную только после успеха.
    """

    id: str
    plugin_id: str
    event_types: tuple[str, ...]
    consumer_group: str

    def __post_init__(self) -> None:
        for name in ("id", "plugin_id", "consumer_group"):
            _require_text(getattr(self, name), name)
        if not isinstance(self.event_types, tuple) or not self.event_types:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "event_types обязано быть непустым кортежем",
            )
        for item in self.event_types:
            _require_text(item, "event_types[]")
        object.__setattr__(
            self, "event_types", tuple(dict.fromkeys(self.event_types))
        )

    def accepts(self, event_type: str) -> bool:
        """Проверить, подписан ли плагин на тип (fail-closed: пусто = False)."""

        return isinstance(event_type, str) and event_type in self.event_types


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """Ключ дедупликации КОНСЬЮМЕРА: ``(subscription_id, source_event_id)``.

    НЕ URL, НЕ hash тела, НЕ ``event_id`` платформы (§7.3). Hash тела меняется
    при изменении данных и не защищает от повторной доставки; URL меняется при
    ротации endpoint'а; ``event_id`` может быть переприсвоен при ре-паблише.
    """

    subscription_id: str
    source_event_id: str

    def __post_init__(self) -> None:
        _require_text(self.subscription_id, "subscription_id")
        _require_text(self.source_event_id, "source_event_id")

    def as_tuple(self) -> tuple[str, str]:
        return (self.subscription_id, self.source_event_id)

    def __str__(self) -> str:
        # Разделитель '|' запрещён в идентификаторах _require_text? Нет, но
        # хранилище работает с кортежем; строка нужна только для логов.
        return f"{self.subscription_id}:{self.source_event_id}"


@dataclass(frozen=True, slots=True)
class DlqRecord:
    """Запись DLQ. Сохраняет ``event_id`` и ``run_id`` исходного события.

    Тело события по-прежнему НЕ хранится: только ``payload_digest``. Плюс
    ``attempts`` и ``error_kind`` (имя класса, без текста исключения), чтобы
    оператор мог понять причину, не получая PII/секреты (criterion 11).
    """

    event: PluginEvent
    subscription_id: str
    topic: str
    dlq_topic: str
    attempts: int
    error_kind: str | None
    failed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.event, PluginEvent):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "event должен быть PluginEvent",
            )
        _require_text(self.subscription_id, "subscription_id")
        _require_text(self.topic, "topic")
        _require_text(self.dlq_topic, "dlq_topic")
        if (
            not isinstance(self.attempts, int)
            or isinstance(self.attempts, bool)
            or self.attempts != MAX_DELIVERY_ATTEMPTS
        ):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                f"DLQ допускается только после {MAX_DELIVERY_ATTEMPTS} попыток",
            )
        if self.error_kind is not None:
            _require_text(self.error_kind, "error_kind")
        _require_aware_datetime(self.failed_at, "failed_at")

    def to_wire(self) -> dict[str, Any]:
        """Представление для ``DlqPort``; без raw payload."""

        return {
            **self.event.to_wire(),
            "subscription_id": self.subscription_id,
            "topic": self.topic,
            "dlq_topic": self.dlq_topic,
            "attempts": self.attempts,
            "error_kind": self.error_kind,
            "failed_at": self.failed_at.astimezone(UTC).isoformat(),
        }


def consumer_config() -> dict[str, Any]:
    """Зафиксированная конфигурация консьюмера плагинов (acceptance №20).

    Возвращает НОВЫЙ dict: вызывающий не может изменить канонические значения.
    ``enable_auto_commit=False`` + ``auto_offset_reset="earliest"`` — прямая
    противоположность as-built ``kafka_manager.py:695-696``. Любой адаптер
    ``ConsumerPort`` обязан применить ровно этот конфиг; тест проверяет обе
    константы и отсутствие ``kick``-режима.
    """

    return {
        "auto_offset_reset": CONSUMER_AUTO_OFFSET_RESET,
        "enable_auto_commit": CONSUMER_ENABLE_AUTO_COMMIT,
        "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
        "consumer_mode": "dispatch",
        "topic": "agentscale.plugin.events",
    }
