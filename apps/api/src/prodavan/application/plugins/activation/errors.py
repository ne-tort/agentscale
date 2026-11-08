"""Ошибки activator'а плагинов (задача 09, §7.4 v4).

Fail-closed: любая неопределённость (k8s недоступен, Ready не наступил,
токен истёк, lease просрочен, аудит не записан) — это ЯВНЫЙ отказ, а не
«молчаливый успех». Все ошибки наследуют ``AppError`` и потому маппятся
в problem+json общим обработчиком приложения.

Коды стабильны: на них опираются Gateway (задача 11), Admin API (задача 12)
и acceptance-критерии 13/14. В ``detail`` и ``extra`` никогда не кладутся
токены, DSN, тела запросов и PII (§4.5 v4).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from prodavan.domain.errors import AppError


class ActivationErrorCode(StrEnum):
    """Реестр кодов activator'а. Расширять только вместе с планом миграции."""

    WAKE_TIMEOUT = "PLUGIN_WAKE_TIMEOUT"
    SCALE_UNAVAILABLE = "PLUGIN_SCALE_UNAVAILABLE"
    ACTIVATION_CONFLICT = "PLUGIN_ACTIVATION_CONFLICT"
    NOT_READY = "PLUGIN_NOT_READY"
    LEASE_EXPIRED = "PLUGIN_JOB_LEASE_EXPIRED"
    AUDIT_UNAVAILABLE = "PLUGIN_AUDIT_UNAVAILABLE"
    MINT_INVALID = "PLUGIN_MINT_INVALID"
    PROXY_MISSING = "PLUGIN_PROXY_MISSING"
    STATE_INVALID = "PLUGIN_STATE_INVALID"
    UNEXPECTED = "PLUGIN_ACTIVATION_FAILED"


class ActivationError(AppError):
    """Базовый класс ошибок activator'а.

    Напрямую инстанцируется только для инфраструктурных сбоев, у которых нет
    отдельного подкласса (например, недоступное хранилище состояния).
    """

    def __init__(
        self,
        *,
        code: str = ActivationErrorCode.UNEXPECTED.value,
        title: str = "Plugin activation failed",
        status: int = 503,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code=code, title=title, status=status, detail=detail, extra=extra)


class WakeTimeout(ActivationError):
    """Ready не наступил в отведённый бюджет: wake НЕ считается успехом."""

    CODE = ActivationErrorCode.WAKE_TIMEOUT.value

    def __init__(self, *, plugin_instance_id: str, timeout_seconds: float, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title="Plugin wake timeout",
            status=504,
            detail=detail or f"Плагин {plugin_instance_id} не стал Ready за {timeout_seconds} с",
            extra={"plugin_instance_id": plugin_instance_id, "timeout_seconds": timeout_seconds},
        )


class ScaleUnavailable(ActivationError):
    """PATCH deployments/scale не отработал или k8s/scale-порт недоступен."""

    CODE = ActivationErrorCode.SCALE_UNAVAILABLE.value

    def __init__(self, *, plugin_instance_id: str, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title="Plugin scale unavailable",
            status=503,
            detail=detail or f"Не удалось изменить replicas для плагина {plugin_instance_id}",
            extra={"plugin_instance_id": plugin_instance_id},
        )


class ActivationConflict(ActivationError):
    """Параллельный wake / усыпление при активном job'е — повторить позже, не дублировать scale."""

    CODE = ActivationErrorCode.ACTIVATION_CONFLICT.value

    def __init__(self, *, plugin_instance_id: str, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title="Plugin activation conflict",
            status=409,
            detail=detail or f"Плагин {plugin_instance_id} уже находится в переходном состоянии",
            extra={"plugin_instance_id": plugin_instance_id},
        )


class NotReady(ActivationError):
    """Проба Ready упала или backend потерял Ready до проксирования."""

    CODE = ActivationErrorCode.NOT_READY.value

    def __init__(self, *, plugin_instance_id: str, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title="Plugin backend not ready",
            status=503,
            detail=detail or f"Backend плагина {plugin_instance_id} не Ready",
            extra={"plugin_instance_id": plugin_instance_id},
        )


class LeaseExpired(ActivationError):
    """Lease job'а истёк: результат такого job'а нельзя принять молча.

    Job мог быть передан другому воркеру и выполнен повторно; поэтому
    ``complete_job`` с просроченным lease — явная ошибка, а не no-op.
    """

    CODE = ActivationErrorCode.LEASE_EXPIRED.value

    def __init__(self, *, plugin_instance_id: str, job_id: str, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title="Plugin job lease expired",
            status=410,
            detail=detail or f"Lease job'а {job_id} у плагина {plugin_instance_id} истёк",
            extra={"plugin_instance_id": plugin_instance_id, "job_id": job_id},
        )


__all__ = [
    "ActivationConflict",
    "ActivationError",
    "ActivationErrorCode",
    "LeaseExpired",
    "NotReady",
    "ScaleUnavailable",
    "WakeTimeout",
]
