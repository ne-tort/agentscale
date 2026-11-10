"""Redis-backed durable delivery claims for plugin events.

The store accepts redis.asyncio Redis or a compatible test double. No socket is
opened by this module itself. Lua scripts make acquire/renew/complete/release
atomic and fence stale owners.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any

from prodavan.application.plugins.events.contracts import DeliveryClaim, IdempotencyKey


_ACQUIRE = """
local completed = redis.call('EXISTS', KEYS[2])
if redis.call('EXISTS', KEYS[1]) == 1 and completed == 0 then
    return {0, 0}
end
-- A completed delivery has no backend side effect left. Its lease may be
-- transferred for owner-fenced remember/audit/commit finalization only.
local generation = redis.call('INCR', KEYS[3])
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
return {generation, completed}
"""

_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_COMPLETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[2], ARGV[2])
    return redis.call('PEXPIRE', KEYS[1], ARGV[3])
end
return 0
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _number(value: Any) -> int:
    if isinstance(value, bytes):
        value = value.decode("ascii")
    if isinstance(value, bool):
        raise ValueError("Redis returned boolean where integer was expected")
    return int(value)


class RedisDeliveryClaimStore:
    """Durable lease store implementing DeliveryClaimPort.

    Keys are derived from a SHA-256 digest of subscription/source identity, so
    raw event identifiers are not used as Redis key material. Completion is
    persistent independently of the expiring lease, allowing a later retry to
    observe completed delivery without repeating the backend side effect.
    """

    def __init__(self, *, redis: Any, prefix: str = "agentscale:plugin:delivery:") -> None:
        if redis is None or not callable(getattr(redis, "eval", None)):
            raise ValueError("redis client with eval() is required")
        if not isinstance(prefix, str) or not prefix or any(c.isspace() for c in prefix):
            raise ValueError("prefix must be a non-empty Redis key prefix")
        self._redis = redis
        self._prefix = prefix
        # This adapter intentionally never closes a Redis client. In production
        # it receives the shared lifespan-managed client; no owned client is
        # created here, so there is no lifecycle conflict with raw DLQ/cache.
        self.owns_client = False

    def _keys(self, key: IdempotencyKey) -> tuple[str, str, str]:
        digest = hashlib.sha256(
            json.dumps(
                {"subscription_id": key.subscription_id, "source_event_id": key.source_event_id},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        # Redis Cluster hash tag keeps all Lua keys in one hash slot.
        base = f"{self._prefix}{{{digest}}}"
        return f"{base}:lease", f"{base}:completed", f"{base}:generation"

    async def acquire(self, key: IdempotencyKey, *, lease_seconds: float) -> DeliveryClaim | None:
        ttl_seconds = float(lease_seconds)
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        lease_key, completed_key, generation_key = self._keys(key)
        token = uuid.uuid4().hex
        result = await self._redis.eval(
            _ACQUIRE,
            3,
            lease_key,
            completed_key,
            generation_key,
            token,
            max(1, int(ttl_seconds * 1000)),
        )
        generation, completed = result
        if _number(generation) == 0:
            return None
        return DeliveryClaim(
            key=key,
            owner_token=token,
            generation=_number(generation),
            lease_seconds=ttl_seconds,
            completed=_number(completed) == 1,
        )

    async def renew(self, claim: DeliveryClaim) -> bool:
        lease_key, _, _ = self._keys(claim.key)
        result = await self._redis.eval(
            _RENEW,
            1,
            lease_key,
            claim.owner_token,
            max(1, int(claim.lease_seconds * 1000)),
        )
        return _number(result) == 1

    async def complete(self, claim: DeliveryClaim) -> bool:
        lease_key, completed_key, _ = self._keys(claim.key)
        result = await self._redis.eval(
            _COMPLETE,
            2,
            lease_key,
            completed_key,
            claim.owner_token,
            str(claim.generation),
            max(1, int(claim.lease_seconds * 1000)),
        )
        return _number(result) == 1

    async def release(self, claim: DeliveryClaim) -> bool:
        lease_key, _, _ = self._keys(claim.key)
        result = await self._redis.eval(
            _RELEASE,
            1,
            lease_key,
            claim.owner_token,
        )
        return _number(result) == 1


__all__ = ["RedisDeliveryClaimStore"]
