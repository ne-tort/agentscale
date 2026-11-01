"""Trusted transaction ports for the evidence handoff boundary."""

from __future__ import annotations

from typing import Protocol

from .contracts import EquipmentAdmissionCommand, EquipmentAdmissionResult


class TrustedEvidenceAdmissionPort(Protocol):
    """Durable, append-only admission and downstream intent CAS boundary.

    Implementations must execute server-resolved visibility, source provenance,
    exact candidate-group/offer ownership membership, evidence append, and the
    requested intent in one tenant/project transaction. A conflict or stale
    generation is zero-row/no-side-effects.
    """

    async def admit(self, command: EquipmentAdmissionCommand) -> EquipmentAdmissionResult:
        """Atomically append/replay evidence and authorize one intent."""
        ...


class DurableEvidenceStore(Protocol):
    """Marker for the mandatory durable store required by trusted adapters."""

    async def append_or_replay(self, *, tenant_id: str, project_id: str, evidence_id: str, digest: str) -> None: ...


__all__ = ["DurableEvidenceStore", "TrustedEvidenceAdmissionPort"]
