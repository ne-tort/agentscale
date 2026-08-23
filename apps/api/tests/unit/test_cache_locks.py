"""Unit tests — Redis cache lock / rate-limit helpers (C-CACHE)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prodavan.core.infra import cache as cache_mod


@pytest.mark.asyncio
async def test_acquire_release_lock_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[str, str] = {}

    class _Client:
        async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
            if nx and key in store:
                return False
            store[key] = value
            return True

        async def eval(self, script: str, numkeys: int, key: str, token: str):
            if store.get(key) == token:
                del store[key]
                return 1
            return 0

    mgr = SimpleNamespace(enabled=True, client=_Client())
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: mgr,
    )

    token = await cache_mod.acquire_lock("prodavan:lock:x", ttl_sec=10)
    assert token is not None
    assert await cache_mod.acquire_lock("prodavan:lock:x", ttl_sec=10) is None
    assert await cache_mod.release_lock("prodavan:lock:x", token) is True
    assert await cache_mod.release_lock("prodavan:lock:x", token) is False


@pytest.mark.asyncio
async def test_rate_limit_allow_window(monkeypatch: pytest.MonkeyPatch) -> None:
    counts: dict[str, int] = {}

    class _Client:
        async def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        async def expire(self, key: str, sec: int) -> bool:
            return True

    mgr = SimpleNamespace(enabled=True, client=_Client())
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: mgr,
    )

    key = "prodavan:rl:t"
    assert await cache_mod.rate_limit_allow(key, limit=2, window_sec=60) is True
    assert await cache_mod.rate_limit_allow(key, limit=2, window_sec=60) is True
    assert await cache_mod.rate_limit_allow(key, limit=2, window_sec=60) is False


@pytest.mark.asyncio
async def test_lock_noop_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: None,
    )
    assert await cache_mod.acquire_lock("k") is None
    assert await cache_mod.rate_limit_allow("k", limit=1, window_sec=1) is True
