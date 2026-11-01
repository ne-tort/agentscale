"""Read-only and trusted decision ports for owner-anchor resolution."""

from __future__ import annotations

from typing import Protocol

from .contracts import OwnerAnchorRecord, OwnerKey, ParentCompatibility, RlsContext


class OwnerAnchorReader(Protocol):
    """Read one exact typed key; implementations must expose duplicates."""

    async def find_by_key(self, key: OwnerKey) -> tuple[OwnerAnchorRecord, ...]: ...


class ParentCompatibilityReader(Protocol):
    async def prove(self, key: OwnerKey) -> ParentCompatibility: ...


class RlsContextReader(Protocol):
    async def get_server_context(self) -> RlsContext | None: ...


class TrustedOwnerAnchorPort(OwnerAnchorReader, ParentCompatibilityReader, RlsContextReader, Protocol):
    """Combined read/decision port; deliberately has no ORM or write methods."""
