"""Read-only ports used by the equipment visibility policy adapter."""

from __future__ import annotations

from typing import Protocol

from .contracts import CatalogSnapshotVisibility, SourceProvenance, VisibleOffer


class CatalogSnapshotVisibilityPort(Protocol):
    async def get_snapshot(self, snapshot_id: str) -> CatalogSnapshotVisibility | None: ...


class OfferVisibilityPort(Protocol):
    async def get_offer(self, offer_id: str) -> VisibleOffer | None: ...

    async def is_offer_in_candidate_group(self, *, candidate_group_id: str, offer_id: str) -> bool: ...


class SourceProvenancePort(Protocol):
    async def get_provenance(self, offer_id: str) -> SourceProvenance | None: ...


__all__ = [
    "CatalogSnapshotVisibilityPort",
    "OfferVisibilityPort",
    "SourceProvenancePort",
]
