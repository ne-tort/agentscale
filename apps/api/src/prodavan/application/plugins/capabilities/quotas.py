"""Квоты capability-запросов плагинов (F2, acceptance №10 / №21).

Ключевое отличие от ``tenant_infra/quota.py``:

* Там ``enforce_ops_rate`` ключует rate limit по ``project_id``
  (``cache_key("rl", "tenant_infra", plane, project_id)``, строки 93-107).
  Для company-wide, stateless плагина ``project_id`` отсутствует или не является
  границей; такой ключ слил бы ВСЕ инстансы компании в один счётчик.
* Здесь ключ ОБЯЗАТЕЛЬНО ``(company_id, plugin_instance_id, artifact_digest)``
  (§4.3, §4.4, кр. 21). Один «шумный» плагин не может исчерпать лимит компании,
  а смена артефакта (новая версия) начинает новый отсчёт — старый digest не
  «наследует» чужой лимит.

Fail-closed: ``QuotaPort`` отсутствует/ошибается → отказ; ``QUOTA_EXCEEDED``
имеет ``http_status=429`` (кр. 10). Никаких «alert вместо hard limit» (§4.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Any, Protocol, runtime_checkable

from prodavan.application.plugins.capabilities.contracts import TypedContractError

# ---------------------------------------------------------------------------
# Идентификаторы и решения
# ---------------------------------------------------------------------------


class QuotaOp(StrEnum):
    """Измеряемые операции квоты (единый реестр §4.4)."""

    OPS = "ops"
    ROWS = "rows"
    BYTES = "bytes"
    KEYS = "keys"
    OBJECT_BYTES = "object_bytes"
    EGRESS_BYTES = "egress_bytes"


QUOTA_FIELDS = (
    "ops_per_minute",
    "max_rows",
    "max_bytes",
    "max_keys",
    "max_object_bytes",
    "max_egress_bytes",
)
"""Обязательные шесть лимитов durable grant; порядок не является defaults."""


class QuotaReason(StrEnum):
    """Канонические коды решения; ``QUOTA_EXCEEDED`` совместим с HTTP 429."""

    ALLOWED = "allowed"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    QUOTA_KEY_INVALID = "quota_key_invalid"
    QUOTA_UNAVAILABLE = "quota_unavailable"


@dataclass(frozen=True, slots=True)
class PluginQuotaKey:
    """Обязательный per-``(company, instance, digest)`` ключ квоты.

    Все три поля НЕпустые. Ни ``company_id`` в одиночку, ни ``project_id``
    ключом не являются: ``project_id`` здесь вообще отсутствует, потому что
    плагин company-scoped (§4.1). Отсутствие ``artifact_digest`` или попытка
    подставить ``None`` — ``TypedContractError``, а не «общая квота компании».
    """

    company_id: str
    plugin_instance_id: str
    artifact_digest: str

    def __post_init__(self) -> None:
        for name in ("company_id", "plugin_instance_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TypedContractError(f"{name} must be a non-empty string")
        if any(char in self.company_id + self.plugin_instance_id for char in ("\x00", "\n", "\r", ":")):
            raise TypedContractError("quota key components must not contain separators")
        digest = self.artifact_digest
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or any(char not in "0123456789abcdef" for char in digest[7:])
        ):
            raise TypedContractError("artifact_digest must be a sha256 digest")

    def to_tuple(self) -> tuple[str, str, str]:
        """Каноническая форма ключа — вся тройка, без усечения до компании."""
        return (self.company_id, self.plugin_instance_id, self.artifact_digest)

    def cache_key(self) -> str:
        """Ключ для внешнего хранилища счётчиков (Redis и т.п.).

        Явно включает все три компонента; порядок фиксирован. НИКАКОГО
        ``project_id`` и НИКАКОГО ``company_id``-only варианта.
        """
        return "rl:plugin:" + ":".join(self.to_tuple())


@dataclass(frozen=True, slots=True)
class PluginQuota:
    """Лимиты одного плагина (расширение ``CompanyTenantInfraQuota``, §4.3).

    Имена полей совпадают с реестром §4.4: ``ops_per_minute``, ``max_rows``,
    ``max_bytes``, ``max_keys``, ``max_object_bytes``, ``max_egress_bytes``.
    Все лимиты положительные; «0 = без лимита» здесь ЗАПРЕЩЁН (fail-closed).
    """

    ops_per_minute: int = 120
    max_rows: int = 10_000
    max_bytes: int = 10 * 1024 * 1024
    max_keys: int = 1_000
    max_object_bytes: int = 50 * 1024 * 1024
    max_egress_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "ops_per_minute", "max_rows", "max_bytes", "max_keys",
            "max_object_bytes", "max_egress_bytes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise TypedContractError(f"{name} must be a positive integer")

    def limit_for(self, op: QuotaOp) -> int:
        """Жёсткий лимит для операции; неизвестная операция → отказ, не ``None``."""
        limits: dict[QuotaOp, int] = {
            QuotaOp.OPS: self.ops_per_minute,
            QuotaOp.ROWS: self.max_rows,
            QuotaOp.BYTES: self.max_bytes,
            QuotaOp.KEYS: self.max_keys,
            QuotaOp.OBJECT_BYTES: self.max_object_bytes,
            QuotaOp.EGRESS_BYTES: self.max_egress_bytes,
        }
        try:
            return limits[op]
        except KeyError as exc:
            raise TypedContractError(f"unknown quota operation: {op!r}") from exc

    @classmethod
    def from_grant(cls, raw: Any) -> PluginQuota:
        """Сконвертировать именно ``invocation.grant.quotas`` без defaults/coercion.

        Поддерживаются durable ``QuotaSet`` и JSON-подобный mapping. Отсутствие
        любого из шести полей — ошибка, а не повод подставить ``PluginQuota()``.
        """
        if raw is None or isinstance(raw, (str, bytes, bytearray)):
            raise TypedContractError("grant.quotas must contain all six limits")
        missing = object()
        values: dict[str, int] = {}
        for name in QUOTA_FIELDS:
            value = raw.get(name, missing) if isinstance(raw, Mapping) else getattr(raw, name, missing)
            if value is missing:
                raise TypedContractError(f"grant.quotas.{name} is required")
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise TypedContractError(f"grant.quotas.{name} must be a positive integer")
            values[name] = value
        return cls(**values)


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    """Полные единицы одного invoke, измеренные typed server-side meter'ом.

    Все поля обязательны: отсутствие измерения нельзя замаскировать под ноль.
    Ноль допустим только как явный результат meter'а для неприменимой единицы.
    """

    ops: int
    rows: int
    bytes: int
    keys: int
    object_bytes: int
    egress_bytes: int

    def __post_init__(self) -> None:
        for name in ("ops", "rows", "bytes", "keys", "object_bytes", "egress_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise TypedContractError(f"quota usage {name} must be a non-negative integer")

    def amount_for(self, op: QuotaOp) -> int:
        amounts = {
            QuotaOp.OPS: self.ops,
            QuotaOp.ROWS: self.rows,
            QuotaOp.BYTES: self.bytes,
            QuotaOp.KEYS: self.keys,
            QuotaOp.OBJECT_BYTES: self.object_bytes,
            QuotaOp.EGRESS_BYTES: self.egress_bytes,
        }
        try:
            return amounts[op]
        except KeyError as exc:
            raise TypedContractError(f"unknown quota operation: {op!r}") from exc


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """Результат ``enforce``. ``QUOTA_EXCEEDED`` → ``http_status=429`` (кр. 10)."""

    allowed: bool
    code: QuotaReason
    key: PluginQuotaKey
    op: QuotaOp
    amount: int
    limit: int
    used: int
    remaining: int
    retry_after_seconds: int | None = None
    reason: str | None = None

    @property
    def http_status(self) -> int:
        """429 для превышения, 503 для недоступности квоты, 200 для allow."""
        if self.allowed:
            return 200
        if self.code is QuotaReason.QUOTA_EXCEEDED:
            return 429
        return 503

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool) or self.amount < 0:
            raise TypedContractError("amount must be a non-negative integer")
        if self.allowed and self.code is not QuotaReason.ALLOWED:
            raise TypedContractError("allowed decision must use ALLOWED code")
        if not self.allowed and self.code is QuotaReason.ALLOWED:
            raise TypedContractError("denied decision must not use ALLOWED code")


# ---------------------------------------------------------------------------
# Порт и in-memory реализация для тестов
# ---------------------------------------------------------------------------


@runtime_checkable
class QuotaPort(Protocol):
    """Enforcement ДО адаптера; ``limits`` обязателен из exact durable grant.

    Хранилище не владеет policy/defaults. Каждый invoke вызывает OPS ровно один
    раз и каждый ненулевой result dimension не более одного раза.
    """

    async def enforce(
        self, key: PluginQuotaKey, op: QuotaOp, amount: int, limits: PluginQuota
    ) -> QuotaDecision: ...

    async def release(
        self, key: PluginQuotaKey, op: QuotaOp, amount: int
    ) -> None: ...


class InMemoryQuota:
    """Детерминированная реализация ``QuotaPort`` для unit-тестов.

    Счётчики хранятся в словаре с ключом ``PluginQuotaKey.to_tuple()`` —
    то есть РАЗДЕЛЬНО для каждой тройки. Два инстанса одной компании, два
    digest'а одного инстанса и две компании имеют независимые счётчики
    (acceptance №21). Потокобезопасность через ``Lock``; в проде ожидается
    Redis-реализация с тем же ``cache_key()``.
    """

    def __init__(
        self, quota: PluginQuota | None = None, *, now: datetime | None = None
    ) -> None:
        # Compatibility for the existing composition root, which still passes
        # PluginQuota(). It is deliberately NOT stored/read: enforce() requires
        # exact invocation.grant.quotas on every call.
        _ = quota
        self._now = now
        self._lock = Lock()
        self._ops: dict[tuple[str, str, str], list[tuple[float, int]]] = {}
        self._counters: dict[tuple[tuple[str, str, str], QuotaOp], int] = {}

    @staticmethod
    def _epoch(value: datetime) -> float:
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypedContractError("now must be timezone-aware")
        return value.timestamp()

    def _current_time(self) -> float:
        return self._epoch(self._now) if self._now is not None else datetime.now(UTC).timestamp()

    async def enforce(
        self, key: PluginQuotaKey, op: QuotaOp, amount: int, limits: PluginQuota
    ) -> QuotaDecision:
        """Проверить И зарезервировать ``amount`` по exact-grant ``limits``.

        Отказ НЕ изменяет счётчик (шумный плагин не «доедает» лимит соседей,
        но и не получает allow). Отрицательный ``amount`` отклоняется.
        """
        if not isinstance(key, PluginQuotaKey):
            raise TypedContractError(f"invalid quota key: {key!r}")
        if not isinstance(op, QuotaOp):
            raise TypedContractError(f"unknown quota op: {op!r}")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            return self._deny(key, op, 0, QuotaReason.QUOTA_KEY_INVALID, 0, 0, "amount must be non-negative")

        if not isinstance(limits, PluginQuota):
            return self._deny(
                key, op, amount, QuotaReason.QUOTA_KEY_INVALID, 0, 0,
                "exact grant limits are required",
            )
        limit = limits.limit_for(op)
        ident = key.to_tuple()
        now = self._current_time()

        with self._lock:
            if op is QuotaOp.OPS:
                # Скользящее окно 60 с, строго по per-instance ключу.
                window_start = now - 60.0
                events = [(ts, qty) for ts, qty in self._ops.get(ident, ()) if ts > window_start]
                used = sum(qty for _, qty in events)
                if used + amount > limit:
                    self._ops[ident] = events
                    retry = max(1, int(60.0 - (now - events[0][0]))) if events else 60
                    return self._deny(
                        key, op, amount, QuotaReason.QUOTA_EXCEEDED, limit, used,
                        "ops_per_minute exceeded", retry,
                    )
                events.append((now, amount))
                self._ops[ident] = events
                remaining = max(0, limit - (used + amount))
                return self._allow(key, op, amount, limit, used + amount, remaining)

            counter_key = (ident, op)
            used = self._counters.get(counter_key, 0)
            if used + amount > limit:
                return self._deny(
                    key, op, amount, QuotaReason.QUOTA_EXCEEDED, limit, used,
                    f"{op.value} limit exceeded",
                )
            self._counters[counter_key] = used + amount
            remaining = max(0, limit - (used + amount))
            return self._allow(key, op, amount, limit, used + amount, remaining)

    async def release(
        self, key: PluginQuotaKey, op: QuotaOp, amount: int
    ) -> None:
        """Вернуть ранее разрешённые единицы (откат составного резерва).

        Нужен, когда запрос прошёл по OPS, но был отклонён по другой
        размерности: без возврата отклонённый запрос съедал бы бюджет.
        """
        if not isinstance(key, PluginQuotaKey) or not isinstance(op, QuotaOp):
            raise TypedContractError("invalid quota release key/op")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            return
        ident = key.to_tuple()
        with self._lock:
            if op is QuotaOp.OPS:
                events = list(self._ops.get(ident, ()))
                remaining_budget = amount
                kept: list[tuple[float, int]] = []
                for ts, qty in reversed(events):
                    if remaining_budget > 0 and qty <= remaining_budget:
                        remaining_budget -= qty
                        continue
                    kept.append((ts, qty))
                self._ops[ident] = list(reversed(kept))
                return
            counter_key = (ident, op)
            used = self._counters.get(counter_key, 0)
            self._counters[counter_key] = max(0, used - amount)

    @staticmethod
    def _allow(
        key: PluginQuotaKey, op: QuotaOp, amount: int, limit: int, used: int, remaining: int
    ) -> QuotaDecision:
        return QuotaDecision(
            allowed=True,
            code=QuotaReason.ALLOWED,
            key=key,
            op=op,
            amount=amount,
            limit=limit,
            used=used,
            remaining=remaining,
        )

    @staticmethod
    def _deny(
        key: PluginQuotaKey,
        op: QuotaOp,
        amount: int,
        code: QuotaReason,
        limit: int,
        used: int,
        reason: str,
        retry: int | None = None,
    ) -> QuotaDecision:
        return QuotaDecision(
            allowed=False,
            code=code,
            key=key,
            op=op,
            amount=amount,
            limit=limit,
            used=used,
            remaining=0,
            retry_after_seconds=retry,
            reason=reason,
        )

    def usage(self, key: PluginQuotaKey, op: QuotaOp) -> int:
        """Диагностика для тестов изоляции; не является источником решения."""
        ident = key.to_tuple()
        with self._lock:
            if op is QuotaOp.OPS:
                return sum(qty for _, qty in self._ops.get(ident, ()))
            return self._counters.get((ident, op), 0)


def quota_key_from_claims(
    *, company_id: str, plugin_instance_id: str, artifact_digest: str
) -> PluginQuotaKey:
    """Единственный способ построить ключ квоты из identity плагина.

    Явно требует все три поля: забытый ``artifact_digest`` или попытка
    построить «квоту компании» - ошибка, а не молчаливый fallback.
    """
    return PluginQuotaKey(
        company_id=company_id,
        plugin_instance_id=plugin_instance_id,
        artifact_digest=artifact_digest,
    )


__all__ = [
    "QUOTA_FIELDS",
    "InMemoryQuota",
    "PluginQuota",
    "PluginQuotaKey",
    "QuotaDecision",
    "QuotaOp",
    "QuotaPort",
    "QuotaReason",
    "QuotaUsage",
    "quota_key_from_claims",
]
