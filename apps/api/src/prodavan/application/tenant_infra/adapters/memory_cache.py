"""In-memory Tenant Infra Cache (tests / Redis disabled)."""

from __future__ import annotations

import time

_SHARED: InMemoryTenantCache | None = None


class InMemoryTenantCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._indexes: dict[str, set[str]] = {}

    def _alive(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, exp = item
        if exp is not None and time.time() >= exp:
            self._data.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._alive(key)

    async def set(self, key: str, value: str, *, ttl_sec: int | None) -> bool:
        exp = (time.time() + ttl_sec) if ttl_sec and ttl_sec > 0 else None
        self._data[key] = (value, exp)
        return True

    async def delete(self, key: str) -> bool:
        self._data.pop(key, None)
        return True

    async def incr(self, key: str, *, amount: int = 1, ttl_sec: int | None = None) -> int | None:
        cur = int(self._alive(key) or "0")
        cur += int(amount)
        exp = None
        if key in self._data and self._data[key][1] is not None:
            exp = self._data[key][1]
        elif ttl_sec and ttl_sec > 0:
            exp = time.time() + ttl_sec
        self._data[key] = (str(cur), exp)
        return cur

    async def index_add(self, index_key: str, member: str) -> int:
        bucket = self._indexes.setdefault(index_key, set())
        bucket.add(member)
        return len(bucket)

    async def index_remove(self, index_key: str, member: str) -> None:
        bucket = self._indexes.get(index_key)
        if bucket is not None:
            bucket.discard(member)

    async def index_card(self, index_key: str) -> int:
        return len(self._indexes.get(index_key) or ())

    async def index_members(self, index_key: str) -> list[str]:
        return list(self._indexes.get(index_key) or ())

    async def purge_keys(self, keys: list[str]) -> int:
        n = 0
        for key in keys:
            if key in self._data:
                self._data.pop(key, None)
                n += 1
            for bucket in self._indexes.values():
                bucket.discard(key)
            if key in self._indexes:
                self._indexes.pop(key, None)
                n += 1
        return n


def get_shared_memory_tenant_cache() -> InMemoryTenantCache:
    """Process-wide memory cache — required so HTTP handlers share state without Redis."""
    global _SHARED
    if _SHARED is None:
        _SHARED = InMemoryTenantCache()
    return _SHARED


def reset_shared_memory_tenant_cache() -> None:
    """Test helper."""
    global _SHARED
    _SHARED = None
