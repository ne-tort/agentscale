"""Read-only ports for the plugin-instance registry adapter."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .contracts import PluginInstanceRegistryIdentity

RegistryRow = Mapping[str, Any] | Any


class PluginInstanceRegistrySourcePort(Protocol):
    """Read one exact persisted snapshot; no fallback or writes are allowed."""

    async def read_exact(self, identity: PluginInstanceRegistryIdentity) -> RegistryRow | None: ...


class PluginInstanceRegistryQuarantinePort(Protocol):
    """Record malformed/conflicting evidence without making it positive."""

    async def quarantine(self, row: RegistryRow, *, reason: str) -> None: ...


__all__ = ["PluginInstanceRegistryQuarantinePort", "PluginInstanceRegistrySourcePort", "RegistryRow"]
