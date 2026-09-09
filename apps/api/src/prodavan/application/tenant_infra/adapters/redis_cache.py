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

    async def incr(self, key: str, *, amount: int = 1, ttl_sec: int | None = None) -> int | None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return None
        try:
            client = mgr.client
            existed = await client.exists(key)
            if amount == 1:
                value = int(await client.incr(key))
            else:
                value = int(await client.incrby(key, int(amount)))
            if not existed and ttl_sec and ttl_sec > 0:
                await client.expire(key, int(ttl_sec))
            return value
        except Exception:
            logger.exception("tenant cache incr failed key=%s", key)
            return None

    async def index_add(self, index_key: str, member: str) -> int:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return 0
        try:
            return int(await mgr.client.sadd(index_key, member))
        except Exception:
            logger.exception("tenant cache index_add failed")
            return 0

    async def index_remove(self, index_key: str, member: str) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        try:
            await mgr.client.srem(index_key, member)
        except Exception:
            logger.exception("tenant cache index_remove failed")

    async def index_card(self, index_key: str) -> int:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return 0
        try:
            return int(await mgr.client.scard(index_key))
        except Exception:
            logger.exception("tenant cache index_card failed")
            return 0

    async def index_members(self, index_key: str) -> list[str]:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return []
        try:
            raw = await mgr.client.smembers(index_key)
            out: list[str] = []
            for item in raw or ():
                if isinstance(item, bytes):
                    out.append(item.decode("utf-8"))
                else:
                    out.append(str(item))
            return out
        except Exception:
            logger.exception("tenant cache index_members failed")
            return []

    async def purge_keys(self, keys: list[str]) -> int:
        if not keys:
            return 0
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return 0
        try:
            return int(await mgr.client.delete(*keys))
        except Exception:
            logger.exception("tenant cache purge_keys failed")
            return 0
