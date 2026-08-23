"""LifespanResource — base contract for FastAPI startup/shutdown (P0)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LifespanResource(ABC):
    """Registered unit managed by :class:`LifespanManager`."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable id for logs / health report."""

    @abstractmethod
    async def startup(self) -> None:
        """Acquire connections / start background work."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release resources (reverse order of startup)."""

    async def health(self) -> bool | None:
        """Optional probe: True/False, or None if not applicable / disabled."""
        return None
