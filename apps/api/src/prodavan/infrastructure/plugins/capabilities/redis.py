"""Durable Redis state for MCP registrations and manager indirection."""
from __future__ import annotations

import hashlib
from typing import Any


def _key(company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str) -> str:
    parts = (company_id, plugin_id, plugin_instance_id, server_id)
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return "plugin:mcp:registration:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RedisMcpRegistrationStore:
    def __init__(self, *, client: Any) -> None:
        if client is None or not all(
            callable(getattr(client, method, None)) for method in ("get", "set")
        ):
            raise ValueError("RedisMcpRegistrationStore requires a real Redis client")
        self._client = client

    async def get(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str
    ) -> str | None:
        value = await self._client.get(_key(company_id, plugin_id, plugin_instance_id, server_id))
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if value is not None and not isinstance(value, str):
            raise RuntimeError("invalid MCP registry value")
        return value

    async def put_if_absent(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        server_id: str,
        definitions: str,
    ) -> bool:
        result = await self._client.set(
            _key(company_id, plugin_id, plugin_instance_id, server_id), definitions, nx=True
        )
        # redis-py returns True for an inserted key and None for an NX miss.
        # False is accepted for compatible clients; neither result overwrites.
        if result is True:
            return True
        if result is None or result is False:
            return False
        raise RuntimeError("mcp_registry_write_not_confirmed")


class ManagerRedisClient:
    """Resolve the lifespan-managed Redis client only when an operation starts."""

    def __init__(self, manager: Any) -> None:
        if manager is None:
            raise ValueError("Redis manager is required")
        self._manager = manager

    def _client(self) -> Any:
        return self._manager.client

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return await self._client().get(*args, **kwargs)

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        return await self._client().set(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        return await self._client().delete(*args, **kwargs)

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        return await self._client().eval(*args, **kwargs)

    async def publish(self, *args: Any, **kwargs: Any) -> Any:
        return await self._client().publish(*args, **kwargs)


__all__ = ["ManagerRedisClient", "RedisMcpRegistrationStore"]
