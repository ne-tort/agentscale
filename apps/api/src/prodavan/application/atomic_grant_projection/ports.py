"""Ports for the exclusive repository-owned grant projection transaction."""
from __future__ import annotations

from typing import Protocol

from .contracts import AtomicGrantProjectionCommand, AtomicGrantProjectionResult


class AtomicGrantProjectionRepository(Protocol):
    """Owns one transaction containing decision, projection, evidence and audit."""

    async def execute(self, command: AtomicGrantProjectionCommand) -> AtomicGrantProjectionResult: ...


__all__ = ["AtomicGrantProjectionRepository"]
