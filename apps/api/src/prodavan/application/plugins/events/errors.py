"""Ошибки контура событий плагинов (F7, задача 10, дизайн v4 §7.3/§13.3).

Fail-closed: отсутствие обязательного порта, неподготовленный топик, сбой
аудита, DLQ или хранилища подписок — это ЯВНАЯ ошибка, а не молчаливый успех.
Ни один сбой не должен приводить к коммиту offset'а: событие обязано быть
доставлено повторно (ручной commit + ``auto_offset_reset=earliest``), иначе
повторится as-built инцидент «будильника» (``kick`` + ``enable_auto_commit``),
когда событие теряется при простое консьюмера.

Коды — стабильные строковые идентификаторы для логов, аудита и acceptance-
критерия №20 (§11). В ``reason`` никогда не кладём raw payload/секреты: только
короткое техническое описание без тел сообщений.
"""

from __future__ import annotations

from enum import StrEnum


class PluginEventErrorCode(StrEnum):
    """Стабильные коды отказов dispatch-контура событий плагинов."""

    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    INVALID_EVENT = "INVALID_EVENT"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    TOPIC_NOT_ENSURED = "TOPIC_NOT_ENSURED"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    UNKNOWN_SUBSCRIPTION = "UNKNOWN_SUBSCRIPTION"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    CONSUME_FAILED = "CONSUME_FAILED"
    WAKE_FAILED = "WAKE_FAILED"
    STORE_FAILED = "STORE_FAILED"
    AUDIT_FAILED = "AUDIT_FAILED"
    DLQ_FAILED = "DLQ_FAILED"
    COMMIT_FAILED = "COMMIT_FAILED"


class PluginEventError(Exception):
    """Отказ dispatch-контура. Несёт код и непустую причину.

    Fail-closed по умолчанию: любой отказ считается НЕ коммитящим offset.
    Исключение перехватывается диспечером, который возвращает позицию
    консьюмера (``seek``) к необработанной записи и пробрасывает ошибку дальше.
    """

    def __init__(self, code: PluginEventErrorCode, reason: str) -> None:
        if not isinstance(code, PluginEventErrorCode):
            raise TypeError("code must be a PluginEventErrorCode")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("PluginEventError requires a non-empty reason")
        super().__init__(f"{code.value}: {reason}")
        self.code = code
        self.reason = reason

    @property
    def code_value(self) -> str:
        """Строковое значение кода для логов и аудита."""

        return self.code.value


__all__ = ["PluginEventError", "PluginEventErrorCode"]
