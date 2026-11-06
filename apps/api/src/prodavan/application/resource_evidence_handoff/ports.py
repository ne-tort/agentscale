"""Ports for resource evidence handoff; implementations stay outside this zone."""

from __future__ import annotations

from typing import Protocol

from .contracts import ResourceApplyResult, ResourceEvidenceHandoff


class ResourceEvidencePort(Protocol):
    """Read-only server evidence for the exact runtime revision and partition."""

    async def read_runtime_evidence(self, *, project_id: str): ...

    async def read_grant_evidence(self, *, resource_id: str): ...


class ResourceApplyPort(Protocol):
    """CAS apply boundary; it must atomically reject stale ownership/generation."""

    async def compare_and_swap_apply(
        self, *, handoff: ResourceEvidenceHandoff, expected_generation: int
    ) -> ResourceApplyResult: ...


__all__ = ["ResourceApplyPort", "ResourceEvidencePort"]
