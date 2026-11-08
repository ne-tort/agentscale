"""Единые исключения слоя capability-адаптеров плагинов (F2).

Имена зафиксированы по стыку с задачей 04 (HTTP surface `/capabilities/v1/*`):
задача 04 маппит ``PluginTokenError`` → 401, ``RevokedGrant`` → 403,
``QuotaExceededError`` → 429, ``ValidationError``/``TypedContractError`` → 422,
``AdapterMissingError``/``UnknownCapabilityError`` → 503.

ВАЖНО: в штатном пути ``PluginCapabilityService.invoke`` НЕ бросает эти
исключения — он возвращает типизированное решение с ``code`` ядра
(``GRANT_REVOKED`` / ``QUOTA_EXCEEDED`` / ``ADAPTER_MISSING`` / ...). Исключения
возникают только до входа в ядро (битый контракт, неизвестное capability) и
служат fallback-маппингом. Fail-closed: любая ошибка = отказ, не allow.
"""

from __future__ import annotations

from prodavan.application.plugins.capabilities.contracts import TypedContractError
from prodavan.application.plugins.capabilities.ports import (
    AdapterMissingError,
    UnknownCapabilityError,
)


class PluginCapabilityError(Exception):
    """База для ошибок границы capability-слоя плагинов."""


class PluginTokenError(PluginCapabilityError):
    """Невалидный/просроченный ``PluginClaims`` → HTTP 401."""


class RevokedGrant(PluginCapabilityError):
    """Grant отозван; проверка по ``grant_id`` на каждом запросе → HTTP 403."""


class QuotaExceededError(PluginCapabilityError):
    """Превышена квота ``(company, instance, digest)`` → HTTP 429 + QUOTA_EXCEEDED."""


#: Совместимый алиас: задача 04 может импортировать любое из двух имён.
#: Ошибки валидации typed контрактов — это те же ``ValueError``.
ValidationError = TypedContractError


__all__ = [
    "AdapterMissingError",
    "PluginCapabilityError",
    "PluginTokenError",
    "QuotaExceededError",
    "RevokedGrant",
    "TypedContractError",
    "UnknownCapabilityError",
    "ValidationError",
]
