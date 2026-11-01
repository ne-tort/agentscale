"""Persistence-neutral read ports for capability mapping decisions."""

from __future__ import annotations

from typing import Protocol, Sequence

from .contracts import CapabilityMethodMapping, MethodIdentity


class CapabilityMethodMappingReadPort(Protocol):
    """Read-only source of versioned mappings; implementations must not mutate state."""

    async def find_mapping(
        self, *, identity: MethodIdentity, capability_key: str, operation: str
    ) -> CapabilityMethodMapping | None: ...

    async def list_mappings(self, *, capability_key: str) -> Sequence[CapabilityMethodMapping]: ...


class ProtocolCompatibilityReadPort(Protocol):
    """Read-only MCP protocol compatibility source."""

    async def supports_protocol(self, *, protocol_version: str, required_version: str) -> bool: ...


__all__ = ["CapabilityMethodMappingReadPort", "ProtocolCompatibilityReadPort"]
