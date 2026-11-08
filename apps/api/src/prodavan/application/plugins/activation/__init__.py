"""Activator плагинов (задача 09, F8 плана миграции).

Deployment ``replicas: 0`` + wake/hold-until-Ready + idle TTL + реестр
активных job'ов с lease. ``SandboxClaim`` для плагинов не используется
(§7.4 / §13.3 v4).

Публичный контракт:
* ``PluginActivator`` — оркестратор lifecycle;
* ``ActivationState`` / ``IdlePolicy`` / ``DesiredState`` / ``JobLease`` /
  ``WakeResult`` — неизменяемые типы состояния;
* порты (``ScalePort``, ``ReadinessPort``, ``ClockPort``, ``MintPort``,
  ``AuditPort``, ``ProxyPort``, ``StateStorePort``) — границы внешних эффектов;
* ``InMemoryStateStore`` — dev/тестовая реализация хранилища (в проде
  родитель подключает реализацию с межпроцессным CAS);
* ``SystemClock`` — реальные часы;
* ошибки — см. ``errors.py``.

Интеграцию в Plugin Gateway, БД/ConfigMap и k8s-адаптер scale выполняют
задачи 11/14 и родитель; этот пакет от них не зависит.
"""

from __future__ import annotations

from prodavan.application.plugins.activation.activator import PluginActivator, SystemClock
from prodavan.application.plugins.activation.errors import (
    ActivationConflict,
    ActivationError,
    ActivationErrorCode,
    LeaseExpired,
    NotReady,
    ScaleUnavailable,
    WakeTimeout,
)
from prodavan.application.plugins.activation.ports import (
    ActivationAuditEvent,
    AuditPort,
    ClockPort,
    MintPort,
    MintedClaims,
    ProxyPort,
    ReadinessPort,
    ScalePort,
    StateStorePort,
)
from prodavan.application.plugins.activation.state import (
    ActivationState,
    DesiredState,
    IdlePolicy,
    JobLease,
    WakeResult,
    utc_now,
)
from prodavan.application.plugins.activation.state_store import InMemoryStateStore

__all__ = [
    "ActivationAuditEvent",
    "ActivationConflict",
    "ActivationError",
    "ActivationErrorCode",
    "ActivationState",
    "AuditPort",
    "ClockPort",
    "DesiredState",
    "IdlePolicy",
    "InMemoryStateStore",
    "JobLease",
    "LeaseExpired",
    "MintPort",
    "MintedClaims",
    "NotReady",
    "PluginActivator",
    "ProxyPort",
    "ReadinessPort",
    "ScalePort",
    "ScaleUnavailable",
    "StateStorePort",
    "SystemClock",
    "WakeResult",
    "WakeTimeout",
    "utc_now",
]
