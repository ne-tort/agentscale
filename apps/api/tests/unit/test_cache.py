"""Unit tests — Redis cache helpers (no-op without Redis)."""

from __future__ import annotations

import pytest

from prodavan.core.infra.cache import cache_delete, cache_get, cache_key, cache_set
from prodavan.core.infra.redis_manager import RedisManager, set_redis_manager


@pytest.mark.asyncio
async def test_cache_noop_when_redis_disabled() -> None:
    set_redis_manager(None)
    mgr = RedisManager(url=None)
    await mgr.startup()
    assert await cache_get("k") is None
    assert await cache_set("k", "v") is False
    assert await cache_delete("k") is False
    assert cache_key("a", "b") == "prodavan:a:b"
    await mgr.shutdown()
