"""RedisManager — pool/client + LifespanResource (C-CACHE subset)."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.infra.startup_ping import ping_with_retry, resolve_ping_retry
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

    def __init__(
        self,
        url: str | None = None,
        *,
        required: bool = False,
        ping_attempts: int | None = None,
        ping_delay_sec: float | None = None,
    ) -> None:
        self._url = (url or "").strip() or None
        self._required = required
        self._ping_attempts, self._ping_delay_sec = resolve_ping_retry(
            ping_attempts, ping_delay_sec
        )
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
            ok = await ping_with_retry(
                "redis",
                self._client.ping,
                attempts=self._ping_attempts,
                delay_sec=self._ping_delay_sec,
            )
            if not ok:
                raise RuntimeError("redis ping failed")
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
