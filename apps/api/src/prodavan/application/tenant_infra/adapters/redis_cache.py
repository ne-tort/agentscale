"""Redis adapter for Tenant Infra Cache."""

from __future__ import annotations

import logging

from prodavan.core.infra.cache import cache_delete, cache_get, cache_set

logger = logging.getLogger(__name__)


class RedisTenantCache:
    async def get(self, key: str) -> str | None:
        return await cache_get(key)

    async def set(self, key: str, value: str, *, ttl_sec: int | None) -> bool:
        return await cache_set(key, value, ttl_sec=ttl_sec)

    async def delete(self, key: str) -> bool:
        return await cache_delete(key)

    async def incr(self, key: str, *, amount: int = 1) -> int | None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return None
        try:
            if amount == 1:
                return int(await mgr.client.incr(key))
            return int(await mgr.client.incrby(key, int(amount)))
        except Exception:
            logger.exception("tenant cache incr failed key=%s", key)
            return None
