"""Unit tests — RedisManager disabled / enabled paths."""

from __future__ import annotations

import pytest

from prodavan.core.infra.redis_manager import RedisManager, get_redis_manager, set_redis_manager


@pytest.mark.asyncio
async def test_redis_disabled_when_url_empty() -> None:
    set_redis_manager(None)
    mgr = RedisManager(url=None)
    await mgr.startup()
    assert mgr.enabled is False
    assert await mgr.health() is None
    assert get_redis_manager() is mgr
    await mgr.shutdown()
    assert get_redis_manager() is None


@pytest.mark.asyncio
async def test_redis_required_raises_on_bad_url() -> None:
    mgr = RedisManager(url="redis://127.0.0.1:1/0", required=True)
    with pytest.raises(Exception):
        await mgr.startup()
    assert get_redis_manager() is None or get_redis_manager() is mgr
    await mgr.shutdown()
