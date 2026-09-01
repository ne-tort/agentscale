"""Thin Redis cache helpers (C-CACHE) — no-op when Redis disabled."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from prodavan.config.settings import settings

logger = logging.getLogger(__name__)

_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


async def cache_get(key: str) -> str | None:
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return None
    try:
        value = await mgr.client.get(key)
        return str(value) if value is not None else None
    except Exception:
        logger.exception("cache_get failed key=%s", key)
        return None


async def cache_set(key: str, value: str, *, ttl_sec: int | None = 300) -> bool:
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return False
    try:
        if ttl_sec and ttl_sec > 0:
            await mgr.client.set(key, value, ex=int(ttl_sec))
        else:
            await mgr.client.set(key, value)
        return True
    except Exception:
        logger.exception("cache_set failed key=%s", key)
        return False


async def cache_delete(key: str) -> bool:
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return False
    try:
        await mgr.client.delete(key)
        return True
    except Exception:
        logger.exception("cache_delete failed key=%s", key)
        return False


async def acquire_lock(key: str, *, ttl_sec: int = 30, token: str | None = None) -> str | None:
    """SET NX EX short lock. Returns token if acquired, else None. No-op without Redis."""
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return None
    tok = token or uuid.uuid4().hex
    try:
        ok = await mgr.client.set(key, tok, nx=True, ex=max(1, int(ttl_sec)))
        return tok if ok else None
    except Exception:
        logger.exception("acquire_lock failed key=%s", key)
        return None


async def release_lock(key: str, token: str) -> bool:
    """Release lock only if token matches (compare-and-delete)."""
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return False
    try:
        deleted = await mgr.client.eval(_RELEASE_LOCK_LUA, 1, key, token)
        return bool(deleted)
    except Exception:
        logger.exception("release_lock failed key=%s", key)
        return False


async def rate_limit_enforce(key: str, *, limit: int, window_sec: int, detail: str = "rate limit exceeded") -> None:
    """Fixed-window counter — raises when limit exceeded or Redis unavailable (fail-closed)."""
    from prodavan.core.infra.redis_manager import get_redis_manager
    from prodavan.domain.errors import AppError

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        if (settings.auth_mode or "").strip().lower() == "test":
            return
        raise AppError(
            code="REDIS_UNAVAILABLE",
            title="Service Unavailable",
            status=503,
            detail="redis unavailable",
        )
    if limit < 1 or window_sec < 1:
        return
    try:
        count = await mgr.client.incr(key)
        if int(count) == 1:
            await mgr.client.expire(key, int(window_sec))
        if int(count) > int(limit):
            raise AppError(
                code="RATE_LIMITED",
                title="Too Many Requests",
                status=429,
                detail=detail,
            )
    except AppError:
        raise
    except Exception:
        logger.exception("rate_limit_enforce failed key=%s", key)
        raise AppError(
            code="REDIS_UNAVAILABLE",
            title="Service Unavailable",
            status=503,
            detail="redis unavailable",
        ) from None


async def rate_limit_allow(key: str, *, limit: int, window_sec: int) -> bool:
    """Fixed-window counter: allow if count <= limit within window. No-op → allow."""
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return True
    if limit < 1 or window_sec < 1:
        return True
    try:
        count = await mgr.client.incr(key)
        if int(count) == 1:
            await mgr.client.expire(key, int(window_sec))
        return int(count) <= int(limit)
    except Exception:
        logger.exception("rate_limit_allow failed key=%s", key)
        return True


def cache_key(*parts: Any) -> str:
    return "prodavan:" + ":".join(str(p) for p in parts if p is not None and str(p) != "")
