"""RedisManager — pool/client + LifespanResource (C-CACHE subset)."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

_manager: RedisManager | None = None


def get_redis_manager() -> RedisManager | None:
    return _manager


def set_redis_manager(manager: RedisManager | None) -> None:
    global _manager
    _manager = manager


class RedisManager(LifespanResource):
    """Async Redis client. Disabled when ``url`` is empty (transitional until Redis in all envs)."""

    def __init__(self, url: str | None = None, *, required: bool = False) -> None:
        self._url = (url or "").strip() or None
        self._required = required
        self._client: Any = None

    @property
    def name(self) -> str:
        return "redis"

    @property
    def enabled(self) -> bool:
        return self._url is not None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Redis client is not started (call startup or check REDIS_URL)")
        return self._client

    async def startup(self) -> None:
        set_redis_manager(self)
        if not self._url:
            logger.info("redis: disabled (REDIS_URL empty)")
            return
        import redis.asyncio as redis_async

        self._client = redis_async.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
        )
        try:
            await self._client.ping()
            logger.info("redis: connected")
        except Exception:
            logger.exception("redis: ping failed on startup")
            if self._required:
                await self._close_client()
                if get_redis_manager() is self:
                    set_redis_manager(None)
                raise
            # Keep client for later retries; readiness will report unhealthy.

    async def shutdown(self) -> None:
        await self._close_client()
        if get_redis_manager() is self:
            set_redis_manager(None)

    async def _close_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("redis: close failed")
            self._client = None

    async def health(self) -> bool | None:
        if not self._url:
            return None
        if self._client is None:
            return False
        try:
            await self._client.ping()
            return True
        except Exception:
            return False

    async def ping(self) -> bool:
        result = await self.health()
        return bool(result)
