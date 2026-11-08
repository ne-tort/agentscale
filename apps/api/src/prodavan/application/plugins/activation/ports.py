"""Порты activator'а плагинов (задача 09, §7.4 / §4.2 / §4.5 v4).

Все внешние эффекты (k8s scale, probe Ready, часы, выпуск ``PluginClaims``,
аудит, HTTP-прокси) вынесены за границу доменной логики: activator не знает,
стоит ли за портом k8s-клиент, БД или тестовый фейк.

Fail-closed контракт: **ни один порт не является опциональным по умолчанию**.
Отсутствие реализации = явная ошибка ``ActivationError``, а не «считаем Ready»,
«считаем что replicas уже 1» или «тихо вернём запрос без аудита».

Реальный HTTP-proxy реализуется задачей 11 (Plugin Gateway); здесь зафиксирован
только его контракт, чтобы задача 11 могла подставить адаптер без правки
activator'а.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable

from prodavan.application.plugins.activation.state import ActivationState

# ---------------------------------------------------------------------------
# Типы-значения, пересекающие границу портов
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MintedClaims:
    """Результат выпуска короткого ``PluginClaims`` при wake.

    ``token`` — компактная подписанная строка; ``claims`` — уже разобранные поля
    (iss/aud/sub/jti/exp/grant_id/artifact_digest, §4.2 v4). В аудит и логи
    уходит только ``jti``/``grant_id``, **никогда** сам ``token``.

    Намеренно не импортируем типы из задачи 02 (identity): каталог identity
    разрабатывается параллельно и может ещё отсутствовать. Activator
    проверяет свежесть по ``issued_at``/``expires_at`` и по ``jti`` — этого
    достаточно, чтобы не проксировать истёкший токен из Secret (критерий 13).
    """

    token: str
    jti: str
    issued_at: datetime
    expires_at: datetime
    claims: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ttl_seconds(self) -> float:
        return (self.expires_at - self.issued_at).total_seconds()

    def expired_at(self, *, now: datetime) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class ActivationAuditEvent:
    """Append-only событие аудита lifecycle (схема §4.5 v4).

    Поля канонические: никаких raw SQL, тел payload, токенов, DSN, PII.
    ``resource`` — идентификатор (``job_id``, ``deployment/<name>``), не данные.
    Связка «решение» и «факт исполнения» фиксируется разными событиями.
    """

    event_type: str
    plugin_instance_id: str
    occurred_at: datetime
    outcome: str  # "allowed" | "denied"
    code: str | None = None
    reason: str | None = None
    resource: str | None = None


# ---------------------------------------------------------------------------
# Порты
# ---------------------------------------------------------------------------


@runtime_checkable
class ScalePort(Protocol):
    """PATCH ``deployments/scale`` для Deployment плагина с ``replicas: 0``.

    Реализация (задача 14/родитель) обязана быть идемпотентной и не трогать
    ничего, кроме ``/spec/replicas``: Argo закрывает это поле через
    ``ignoreDifferences`` (§7.4). ``current_replicas`` возвращает фактическое
    число реплик, а не «желаемое из манифеста» — иначе повторный wake
    застанет рассинхрон и сделает лишний PATCH.
    """

    async def current_replicas(self, plugin_instance_id: str) -> int: ...

    async def scale_to(self, plugin_instance_id: str, replicas: int) -> None: ...


@runtime_checkable
class ReadinessPort(Protocol):
    """Факт Ready пода плагина (readinessProbe ``/health``, §3.1 / §7.4).

    Важное отличие от as-built ``ensure_running`` (``runtime.py:336-348``):
    тот **не ждёт Ready**, из-за чего первый запрос получает
    connection-refused. Здесь activator опрашивает порт до таймаута.
    ``False`` — не ошибка, а «ещё не готово»; исключение — ``NotReady``.
    """

    async def is_ready(self, plugin_instance_id: str) -> bool: ...


@runtime_checkable
class ClockPort(Protocol):
    """Инжектируемые часы: детерминированные тесты без реального ``sleep``.

    ``now()`` обязан вернуть aware UTC ``datetime``: сравнение с naive
    временем — молчаливый баг, который превращается в вечный idle или
    мгновенный TTL. Activator проверяет это явно (fail-closed).
    ``sleep`` — корутина, чтобы polling не блокировал event loop Gateway.
    """

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


@runtime_checkable
class MintPort(Protocol):
    """Выпуск **нового короткого** ``PluginClaims`` при каждом успешном wake.

    Токен из Secret, созданного при install (§5.1), к моменту wake почти
    наверняка истёк: TTL 300 с, а плагин мог спать часами. Поэтому wake
    обязан перевыпустить токен, а не переиспользовать записанный в Secret.
    Реализация — задача 02 (identity: ``iss=agentscale-plugins``,
    ``aud=capability-gateway``, ``kid``/JWKS, проверка ``grant_id``).
    """

    async def mint(self, *, plugin_instance_id: str, reason: str) -> MintedClaims: ...


@runtime_checkable
class AuditPort(Protocol):
    """Append-only аудит событий ``plugin_wake_up`` / ``plugin_suspend`` и др.

    Синхронный контракт повторяет ``capability_gateway_service.AuditPort``,
    чтобы переиспользовать существующий sink (§4.5). Исключение из ``record``
    трактуется activator'ом как **deny/system failure** для позитивной
    мутации: 0→1 без аудита не считается успешным пробуждением.
    """

    def record(self, event: ActivationAuditEvent) -> None: ...


@runtime_checkable
class StateStorePort(Protocol):
    """Atomарное чтение/запись ``ActivationState`` (БД или ConfigMap, §7.4).

    Родитель (задача 11) подключает реализацию; ``compare_and_swap`` —
    обязательный CAS, чтобы два конкурентных wake не оба прошли проверку
    ``wake_in_flight`` и не сделали два PATCH ``scale_to(1)``. Возврат
    ``False`` означает «состояние изменилось другой стороной, повторите
    чтение», а не «успех». ``save`` без CAS используется только внутри
    уже взятой блокировки воркера.
    """

    async def load(self, plugin_instance_id: str) -> ActivationState: ...

    async def save(self, state: ActivationState) -> None: ...

    async def compare_and_swap(
        self,
        *,
        expected: ActivationState,
        new: ActivationState,
    ) -> bool: ...


@runtime_checkable
class ProxyPort(Protocol):
    """Проксирование удержанного запроса в уже Ready backend плагина.

    Интерфейс для задачи 11: Gateway строит исходящие заголовки allowlist'ом
    (§6.4), перезаписывает ``X-Plugin-*``, отбрасывает входящие
    ``Authorization``/``Cookie`` и подставляет выпущенные ``claims``.
    Activator лишь удерживает запрос до Ready и передаёт его сюда; сетевой
    таймаут, rate limit и retry — ответственность Gateway.
    """

    async def forward(
        self,
        *,
        plugin_instance_id: str,
        request: Any,
        claims: MintedClaims,
    ) -> Any: ...


__all__ = [
    "ActivationAuditEvent",
    "AuditPort",
    "ClockPort",
    "MintPort",
    "MintedClaims",
    "ProxyPort",
    "ReadinessPort",
    "ScalePort",
    "StateStorePort",
]
