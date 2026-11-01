"""Infrastructure-neutral ports for the M05 trusted adapter boundary."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from prodavan.application.plugin_instances import (
    FencingToken,
    LeaseCasRequest,
    PluginInstanceIdentity,
    WriterCasRequest,
)

RuntimeRow = Mapping[str, Any] | Any


class RuntimeInstanceReadPort(Protocol):
    """Read the exact runtime row; this port must not perform fallback lookup."""

    async def read_exact(self, identity: PluginInstanceIdentity) -> RuntimeRow | None: ...


class RuntimeInstanceCasPort(Protocol):
    """Atomic lease and writer CAS operations over the runtime row."""

    async def acquire_lease(self, request: LeaseCasRequest) -> RuntimeRow | None: ...

    async def advance_writer(self, request: WriterCasRequest) -> RuntimeRow | None: ...


class PluginInstanceAdapterQuarantinePort(Protocol):
    """Append malformed/conflicting source evidence without making it positive."""

    async def quarantine(self, row: RuntimeRow, *, reason: str) -> None: ...


__all__ = [
    "PluginInstanceAdapterQuarantinePort",
    "RuntimeInstanceCasPort",
    "RuntimeInstanceReadPort",
    "RuntimeRow",
]
