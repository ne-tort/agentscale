"""Thin Redis cache helpers (C-CACHE) — no-op when Redis disabled."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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


def cache_key(*parts: Any) -> str:
    return "prodavan:" + ":".join(str(p) for p in parts if p is not None and str(p) != "")
