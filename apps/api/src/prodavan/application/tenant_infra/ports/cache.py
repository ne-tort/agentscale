"""Tenant Infra Cache port."""

from __future__ import annotations

from typing import Protocol


class TenantCachePort(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_sec: int | None) -> bool: ...

    async def delete(self, key: str) -> bool: ...

    async def incr(self, key: str, *, amount: int = 1) -> int | None: ...
