"""Неизменяемое состояние lifecycle плагина (задача 09, §7.4 / §11 кр. 13,14).

As-built ``project_service/idle_pause.py:25-40`` считает idle **только** по
agent-сессиям и не видит активные job'ы: backend плагина уснёт посреди
xlsx-экспорта. Здесь состояние явно несёт ``active_job_ids`` и lease'ы этих
job'ов, поэтому ``should_suspend`` может честно ответить «не усыплять».

Все объекты — frozen: любое изменение состояния создаёт новый объект через
``dataclasses.replace``. Это позволяет родителю (задача 11/14) atomically
сохранить состояние в БД/ConfigMap без гонки «прочитали → частично изменили».
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping

from prodavan.application.plugins.activation.errors import ActivationError, ActivationErrorCode


class DesiredState(StrEnum):
    """Желаемое состояние Deployment плагина.

    Соответствует ``replicas: 1`` / ``replicas: 0``; Argo закрывает
    ``/spec/replicas`` через ``ignoreDifferences`` (§7.4 v4), поэтому
    ``desired`` живёт в состоянии Gateway, а не в GitOps-манифесте.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class JobLease:
    """Активный background-job плагина с ограниченным сроком аренды.

    Lease — защита от «вечно активного» job'а: если воркер упал и не вызвал
    ``complete_job``, просроченная аренда снимается, иначе плагин никогда не
    уснёт (риск §10 «Backend уснул посреди активного job» превращается в
    обратный — «никогда не усыпляется»).
    """

    job_id: str
    registered_at: datetime
    expires_at: datetime

    def expired_at(self, *, now: datetime) -> bool:
        return now >= self.expires_at

    def renewed(self, *, now: datetime, ttl_seconds: float) -> JobLease:
        return replace(self, registered_at=now, expires_at=now + timedelta(seconds=ttl_seconds))


@dataclass(frozen=True, slots=True)
class ActivationState:
    """Состояние одного ``plugin_instance_id``.

    ``active_job_ids`` и ``job_leases`` — две проекции одного реестра; инвариант
    проверяется в ``__post_init__`` (fail-closed при рассинхроне). Поле
    ``active_job_ids`` оставлено явным ``frozenset[str]`` по контракту задачи:
    родитель читает его из БД/ConfigMap, не разбирая внутренние lease'ы.

    ``last_active_at is None`` означает «активности ещё не было»: усыпление в
    этом случае запрещено, потому что нет достоверных данных для TTL.
    """

    plugin_instance_id: str
    desired: DesiredState = DesiredState.SUSPENDED
    last_active_at: datetime | None = None
    active_job_ids: frozenset[str] = frozenset()
    wake_in_flight: bool = False
    job_leases: Mapping[str, JobLease] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.plugin_instance_id.strip():
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid plugin activation state",
                status=500,
                detail="plugin_instance_id не может быть пустым",
            )
        _require_aware(self.last_active_at, field_name="last_active_at")
        for job_id, lease in self.job_leases.items():
            _require_aware(lease.registered_at, field_name=f"job_leases[{job_id}].registered_at")
            _require_aware(lease.expires_at, field_name=f"job_leases[{job_id}].expires_at")
            if job_id != lease.job_id:
                raise ActivationError(
                    code=ActivationErrorCode.STATE_INVALID.value,
                    title="Invalid plugin activation state",
                    status=500,
                    detail=f"Ключ job_leases не совпадает с lease.job_id: {job_id!r}",
                )
        if self.active_job_ids != frozenset(self.job_leases):
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid plugin activation state",
                status=500,
                detail="active_job_ids рассинхронизирован с реестром job_leases",
            )

    # -- порождающие методы (frozen → новый объект) -------------------------

    def with_activity(self, *, at: datetime) -> ActivationState:
        return replace(self, last_active_at=at)

    def with_desired(self, desired: DesiredState) -> ActivationState:
        return replace(self, desired=desired)

    def with_wake_in_flight(self, value: bool) -> ActivationState:
        return replace(self, wake_in_flight=value)

    def with_job(self, lease: JobLease) -> ActivationState:
        leases = dict(self.job_leases)
        leases[lease.job_id] = lease
        return replace(self, job_leases=leases, active_job_ids=frozenset(leases))

    def without_job(self, job_id: str) -> ActivationState:
        leases = dict(self.job_leases)
        leases.pop(job_id, None)
        return replace(self, job_leases=leases, active_job_ids=frozenset(leases))

    def pruned_leases(self, *, now: datetime) -> ActivationState:
        """Снять просроченные lease'ы; вернуть новое состояние (или self)."""
        expired = [job_id for job_id, lease in self.job_leases.items() if lease.expired_at(now=now)]
        if not expired:
            return self
        leases = {job_id: lease for job_id, lease in self.job_leases.items() if job_id not in expired}
        return replace(self, job_leases=leases, active_job_ids=frozenset(leases))

    def has_active_jobs(self, *, now: datetime) -> bool:
        """Есть ли job'ы с неистёкшим lease (критерий 14)."""
        return any(not lease.expired_at(now=now) for lease in self.job_leases.values())


def _require_aware(value: datetime | None, *, field_name: str) -> None:
    """Naive datetime — молчаливый баг, превращающийся в вечный idle.

    Проверяем не только ``tzinfo``, но и ``utcoffset()``: объекты с «пустой»
    таймзоной формально не naive, но арифметика с ними всё равно ломается.
    """
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ActivationError(
            code=ActivationErrorCode.STATE_INVALID.value,
            title="Invalid plugin activation state",
            status=500,
            detail=f"{field_name} должен быть aware UTC datetime",
            extra={"field": field_name},
        )


@dataclass(frozen=True, slots=True)
class IdlePolicy:
    """Политика засыпания / пробуждения плагина.

    Три обязательных параметра — по контракту задачи. ``ready_poll_interval``
    и ``job_lease_ttl_seconds`` добавлены с дефолтами: без интервала polling
    невозможно написать детерминированный тест, а без TTL lease'а невозможно
    выполнить требование «просроченный lease снимается».

    Валидация в ``__post_init__`` — fail-closed: нулевой/отрицательный TTL
    привёл бы к усыплению посреди запроса, ``max_concurrent_wakes=0`` — к
    вечному отказу в wake, нулевой polling-интервал — к busy loop.
    """

    idle_ttl_seconds: float
    ready_timeout_seconds: float
    max_concurrent_wakes: int
    ready_poll_interval_seconds: float = 0.5
    job_lease_ttl_seconds: float = 900.0

    def __post_init__(self) -> None:
        for name in (
            "idle_ttl_seconds",
            "ready_timeout_seconds",
            "ready_poll_interval_seconds",
            "job_lease_ttl_seconds",
        ):
            value = float(getattr(self, name))
            if value <= 0 or value != value:  # NaN != NaN
                raise ActivationError(
                    code=ActivationErrorCode.STATE_INVALID.value,
                    title="Invalid plugin idle policy",
                    status=500,
                    detail=f"{name} должен быть положительным конечным числом",
                    extra={"field": name},
                )
        if self.max_concurrent_wakes < 1:
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid plugin idle policy",
                status=500,
                detail="max_concurrent_wakes должен быть >= 1",
            )
        if self.ready_poll_interval_seconds > self.ready_timeout_seconds:
            # Не ошибка конфигурации, но тогда ни одной проверки Ready не будет
            # сделано до таймаута; явный отказ честнее молчаливого WakeTimeout.
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid plugin idle policy",
                status=500,
                detail="ready_poll_interval_seconds не может превышать ready_timeout_seconds",
            )

    @staticmethod
    def defaults() -> IdlePolicy:
        """Разумные значения для dev: 15 мин idle, 60 с на Ready, 3 wake."""
        return IdlePolicy(
            idle_ttl_seconds=900.0,
            ready_timeout_seconds=60.0,
            max_concurrent_wakes=3,
        )


@dataclass(frozen=True, slots=True)
class WakeResult:
    """Результат успешного пробуждения (критерий 13).

    Несёт **новый короткий** ``PluginClaims`` (не истёкший из Secret) и метрики
    ожидания. ``token`` не попадает ни в аудит, ни в логи: в ``WakeResult`` он
    есть только потому, что Gateway должен подставить его в ``Authorization``
    при проксировании.
    """

    plugin_instance_id: str
    token: str
    jti: str
    claims: Mapping[str, Any]
    issued_at: datetime
    expires_at: datetime
    ready_at: datetime
    waited_seconds: float
    previous_replicas: int
    idempotent_hit: bool = False


def utc_now() -> datetime:
    """Единая точка получения «сейчас» для значений, создаваемых вне ClockPort."""
    return datetime.now(UTC)


__all__ = [
    "ActivationState",
    "DesiredState",
    "IdlePolicy",
    "JobLease",
    "WakeResult",
    "utc_now",
]
