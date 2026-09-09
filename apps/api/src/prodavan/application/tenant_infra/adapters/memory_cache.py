"""In-memory Tenant Infra Cache (tests / Redis disabled)."""

from __future__ import annotations


class InMemoryTenantCache:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, *, ttl_sec: int | None) -> bool:
        self._data[key] = value
        return True

    async def delete(self, key: str) -> bool:
        self._data.pop(key, None)
        return True

    async def incr(self, key: str, *, amount: int = 1) -> int | None:
        cur = int(self._data.get(key) or "0")
        cur += int(amount)
        self._data[key] = str(cur)
        return cur
