"""``RedisPluginQuota`` — настоящий ``QuotaPort`` (задача 03) поверх Redis.

Ключ: ``PluginQuotaKey.cache_key()`` = ``rl:plugin:<company>:<instance>:<digest>``
(ровно та же тройка, что в ``InMemoryQuota``; НИКАКОГО ``project_id`` и НИКАКОГО
company-only варианта — acceptance №21).

Семантика (жёсткие счётчики, без «alert вместо hard limit», §4.4):

* ``QuotaOp.OPS`` — СКОЛЬЗЯЩЕЕ ОКНО 60 с. Реализовано атомарным Lua-скриптом
  на Redis sorted set: ``ZREMRANGEBYSCORE`` + ``ZCARD`` + проверка лимита +
  ``ZADD`` + ``PEXPIRE`` в одном вызове (нет гонки «прочитали → записали»).
* остальные ``QuotaOp`` — ЖЁСТКИЕ накопительные счётчики (``INCRBY`` +
  ``PEXPIRE`` при первом инкременте), лимит из ``PluginQuota.limit_for(op)``.

FAIL-CLOSED (главное требование ТЗ)
-----------------------------------
Любая недоступность Redis (нет клиента, ``ConnectionError``, ``TimeoutError``,
ошибка Lua, неожиданный ответ) → ``QuotaDecision`` с
``allowed=False`` и кодом ``QUOTA_EXCEEDED`` / ``QUOTA_UNAVAILABLE``, но НИКОГДА
``ALLOWED``. ТЗ явно требует ``QUOTA_EXCEEDED``/deny при недоступности — поэтому
по умолчанию ``unavailable_reason=QUOTA_EXCEEDED`` (HTTP 429, см.
``QuotaDecision.http_status``); ``QUOTA_UNAVAILABLE`` можно включить явно.

Клиент Redis может быть:
* ``redis.asyncio.Redis`` (синхронный вызов ``enforce`` требует готового
  event loop → используем :meth:`enforce_async`);
* объект с синхронным ``eval``/``zadd``/... (например ``redis.Redis`` или
  тестовый фейк) — тогда работает sync :meth:`enforce`;
* ``RedisManager`` (``core.infra.redis_manager``) — берём ``.client``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

from prodavan.application.plugins.capabilities.quotas import (
    PluginQuota,
    PluginQuotaKey,
    QuotaDecision,
    QuotaOp,
    QuotaReason,
)

WINDOW_SECONDS = 60
"""Скользящее окно для ``ops_per_minute`` (§4.4, кр. 10)."""

# Lua: атомарное скользящее окно. KEYS[1] — cache_key(), ARGV — now_ms,
# window_ms, amount, limit, member (уникальный id события).
# Возвращает {used, allowed(0/1), retry_after_ms}.
_OPS_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local amount = tonumber(ARGV[3])
local limit = tonumber(ARGV[4])
local member = ARGV[5]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local used = tonumber(redis.call('ZCARD', key))
if used + amount > limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then
    retry = math.max(1, window - (now - tonumber(oldest[2])))
  end
  redis.call('PEXPIRE', key, window)
  return {used, 0, retry}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {used + amount, 1, 0}
"""

# Lua: жёсткий счётчик. Возвращает {used, allowed}.
_HARD_COUNTER_LUA = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local used = tonumber(redis.call('GET', key) or '0')
if used + amount > limit then
  return {used, 0}
end
used = redis.call('INCRBY', key, amount)
if used == amount then
  redis.call('PEXPIRE', key, ttl)
end
return {used, 1}
"""


class RedisPluginQuota:
    """``QuotaPort`` поверх Redis. Недоступность = deny, не allow.

    ``redis`` обязателен (fail-closed при сборке): ``None`` → ``ValueError``.
    """

    def __init__(
        self,
        redis: Any,
        *,
        quota: PluginQuota | None = None,
        unavailable_reason: QuotaReason = QuotaReason.QUOTA_EXCEEDED,
        clock: Any = None,
        key_prefix: str = "",
    ) -> None:
        if redis is None:
            raise ValueError("RedisPluginQuota требует redis-клиент (fail-closed: без квоты = deny)")
        if unavailable_reason in (QuotaReason.ALLOWED, QuotaReason.QUOTA_KEY_INVALID):
            raise ValueError("unavailable_reason обязан быть отказным кодом")
        self._redis = redis
        self._quota = quota or PluginQuota()
        self._unavailable_reason = unavailable_reason
        self._clock = clock or (lambda: datetime.now(UTC))
        self._key_prefix = key_prefix

    @property
    def quota(self) -> PluginQuota:
        return self._quota

    # ------------------------------------------------------------------
    # Внутреннее: клиент, ключи, решения
    # ------------------------------------------------------------------

    def _client(self) -> Any:
        """Достать реальный redis-клиент (поддерживается RedisManager)."""
        client = self._redis
        # RedisManager хранит клиент в .client и бросает, если не запущен.
        if hasattr(client, "client") and not hasattr(client, "eval"):
            try:
                client = client.client
            except Exception as exc:  # noqa: BLE001 — любая ошибка = недоступность
                raise ConnectionError(f"RedisManager.client недоступен: {exc}") from exc
        if client is None:
            raise ConnectionError("redis-клиент отсутствует")
        return client

    def _cache_key(self, key: PluginQuotaKey, op: QuotaOp) -> str:
        # cache_key() уже включает ВСЮ тройку; для не-OPS добавляем только
        # суффикс операции, чтобы счётчики разных QuotaOp не смешивались.
        base = self._key_prefix + key.cache_key()
        if op is QuotaOp.OPS:
            return base + ":ops"
        return f"{base}:{op.value}"

    def _now_ms(self) -> int:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("часы квоты обязаны возвращать aware datetime")
        return int(value.astimezone(UTC).timestamp() * 1000)

    def _deny(
        self,
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

    def _allow(
        self,
        key: PluginQuotaKey,
        op: QuotaOp,
        amount: int,
        limit: int,
        used: int,
    ) -> QuotaDecision:
        return QuotaDecision(
            allowed=True,
            code=QuotaReason.ALLOWED,
            key=key,
            op=op,
            amount=amount,
            limit=limit,
            used=used,
            remaining=max(0, limit - used),
        )

    def _validate(self, key: Any, op: Any, amount: Any) -> str | None:
        """Строгая валидация входа; возвращает текст ошибки или ``None``."""
        if not isinstance(key, PluginQuotaKey):
            return "invalid quota key"
        if not isinstance(op, QuotaOp):
            return "unknown quota op"
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            return "amount must be non-negative integer"
        return None

    # ------------------------------------------------------------------
    # QuotaPort (синхронный)
    # ------------------------------------------------------------------

    def enforce(self, key: PluginQuotaKey, op: QuotaOp, amount: int) -> QuotaDecision:
        """Проверить И зарезервировать ``amount``; любая ошибка Redis → deny.

        Если передан async-клиент, попытка выполнить его из sync-контекста
        также трактуется как недоступность (deny) — использовать
        :meth:`enforce_async`. Никакого «разрешить на всякий случай».
        """
        invalid = self._validate(key, op, amount)
        if invalid is not None:
            return self._deny(
                key if isinstance(key, PluginQuotaKey) else _dummy_key(),
                op if isinstance(op, QuotaOp) else QuotaOp.OPS,
                amount if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0 else 0,
                QuotaReason.QUOTA_KEY_INVALID,
                0,
                0,
                invalid,
            )
        try:
            limit = self._quota.limit_for(op)
        except Exception as exc:  # noqa: BLE001 — неизвестная операция = отказ
            return self._deny(key, op, amount, QuotaReason.QUOTA_KEY_INVALID, 0, 0, str(exc))

        try:
            client = self._client()
            now_ms = self._now_ms()
            redis_key = self._cache_key(key, op)
            if op is QuotaOp.OPS:
                member = f"{now_ms}-{id(object())}"
                raw = client.eval(
                    _OPS_WINDOW_LUA,
                    1,
                    redis_key,
                    now_ms,
                    WINDOW_SECONDS * 1000,
                    amount,
                    limit,
                    member,
                )
                used, allowed, retry_ms = _parse_triple(raw)
                if not allowed:
                    return self._deny(
                        key, op, amount, QuotaReason.QUOTA_EXCEEDED, limit, used,
                        "ops_per_minute exceeded", max(1, int(retry_ms / 1000)),
                    )
                return self._allow(key, op, amount, limit, used)

            raw = client.eval(
                _HARD_COUNTER_LUA,
                1,
                redis_key,
                amount,
                limit,
                WINDOW_SECONDS * 1000,
            )
            used, allowed = _parse_pair(raw)
            if not allowed:
                return self._deny(
                    key, op, amount, QuotaReason.QUOTA_EXCEEDED, limit, used,
                    f"{op.value} limit exceeded",
                )
            return self._allow(key, op, amount, limit, used)
        except Exception as exc:  # noqa: BLE001 — ЛЮБАЯ недоступность = deny
            return self._deny(
                key, op, amount, self._unavailable_reason, limit, 0,
                f"quota storage unavailable: {type(exc).__name__}",
            )

    async def enforce_async(self, key: PluginQuotaKey, op: QuotaOp, amount: int) -> QuotaDecision:
        """Async-вариант для ``redis.asyncio.Redis``; та же fail-closed семантика."""
        invalid = self._validate(key, op, amount)
        if invalid is not None:
            return self._deny(
                key if isinstance(key, PluginQuotaKey) else _dummy_key(),
                op if isinstance(op, QuotaOp) else QuotaOp.OPS,
                amount if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0 else 0,
                QuotaReason.QUOTA_KEY_INVALID,
                0,
                0,
                invalid,
            )
        try:
            limit = self._quota.limit_for(op)
        except Exception as exc:  # noqa: BLE001
            return self._deny(key, op, amount, QuotaReason.QUOTA_KEY_INVALID, 0, 0, str(exc))

        try:
            client = self._client()
            now_ms = self._now_ms()
            redis_key = self._cache_key(key, op)
            if op is QuotaOp.OPS:
                member = f"{now_ms}-{id(object())}"
                raw = await _maybe_await(client.eval(
                    _OPS_WINDOW_LUA, 1, redis_key, now_ms,
                    WINDOW_SECONDS * 1000, amount, limit, member,
                ))
                used, allowed, retry_ms = _parse_triple(raw)
                if not allowed:
                    return self._deny(
                        key, op, amount, QuotaReason.QUOTA_EXCEEDED, limit, used,
                        "ops_per_minute exceeded", max(1, int(retry_ms / 1000)),
                    )
                return self._allow(key, op, amount, limit, used)
            raw = await _maybe_await(client.eval(
                _HARD_COUNTER_LUA, 1, redis_key, amount, limit, WINDOW_SECONDS * 1000
            ))
            used, allowed = _parse_pair(raw)
            if not allowed:
                return self._deny(
                    key, op, amount, QuotaReason.QUOTA_EXCEEDED, limit, used,
                    f"{op.value} limit exceeded",
                )
            return self._allow(key, op, amount, limit, used)
        except Exception as exc:  # noqa: BLE001 — недоступность = deny
            return self._deny(
                key, op, amount, self._unavailable_reason, limit, 0,
                f"quota storage unavailable: {type(exc).__name__}",
            )

    def usage(self, key: PluginQuotaKey, op: QuotaOp) -> int:
        """Диагностика: текущее использование (ошибка Redis → 0, НЕ allow)."""
        try:
            client = self._client()
            redis_key = self._cache_key(key, op)
            if op is QuotaOp.OPS:
                return int(client.zcard(redis_key))
            raw = client.get(redis_key)
            return int(raw or 0)
        except Exception:  # noqa: BLE001
            return 0


async def _maybe_await(value: Any) -> Any:
    """Поддержать и sync-, и async-клиент Redis."""
    if inspect.isawaitable(value):
        return await value
    return value


def _parse_pair(raw: Any) -> tuple[int, int]:
    """Разобрать ответ Lua ``{used, allowed}``; мусор = ошибка (deny)."""
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return int(raw[0]), int(raw[1])
    raise ValueError(f"unexpected redis reply: {raw!r}")


def _parse_triple(raw: Any) -> tuple[int, int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return int(raw[0]), int(raw[1]), int(raw[2])
    raise ValueError(f"unexpected redis reply: {raw!r}")


def _dummy_key() -> PluginQuotaKey:
    """Безопасный ключ-заглушка для deny-решения при невалидном входе.

    Не используется как источник разрешения: решение уже ``allowed=False``.
    """
    return PluginQuotaKey(
        company_id="invalid",
        plugin_instance_id="invalid",
        artifact_digest="sha256:" + "0" * 64,
    )


__all__ = ["WINDOW_SECONDS", "RedisPluginQuota"]
