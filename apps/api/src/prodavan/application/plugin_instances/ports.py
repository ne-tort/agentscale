"""Persistence and quarantine ports for M05 plugin instance fencing."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    LeaseCasDecision,
    LeaseCasRequest,
    OwnerCasDecision,
    PluginInstanceIdentity,
    PluginInstanceRecord,
    WriterCasRequest,
)


class PluginInstanceRepository(Protocol):
    """Repository must implement exact-key reads and full-snapshot atomic CAS operations.

    CAS implementations must quarantine and deny any result whose identity,
    subject/session, lease, or fencing generations do not exactly match the request.
    """

    async def get_exact(self, identity: PluginInstanceIdentity) -> PluginInstanceRecord | None: ...

    async def acquire_lease(self, request: LeaseCasRequest) -> LeaseCasDecision: ...

    async def advance_writer(self, request: WriterCasRequest) -> OwnerCasDecision: ...


class PluginInstanceQuarantinePort(Protocol):
    async def record_quarantine(self, record: PluginInstanceRecord, *, reason: str) -> None: ...


__all__ = ["PluginInstanceQuarantinePort", "PluginInstanceRepository"]
