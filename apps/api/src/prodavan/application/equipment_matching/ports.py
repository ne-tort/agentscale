"""Ports consumed by the equipment matching application service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .contracts import CandidateGroupDraft, MaterializedOffers, ProcurementRecalculation, RequestClassification


class EquipmentRequestPort(Protocol):
    async def classify_request(self, request_id: str, request_line_id: str) -> RequestClassification: ...

    async def save_candidate_group(self, group: CandidateGroupDraft) -> CandidateGroupDraft: ...


class EquipmentCatalogPort(Protocol):
    async def materialize_offers(
        self,
        *,
        request_id: str,
        candidate_group_ids: Sequence[str],
        catalog_snapshot_id: str,
    ) -> MaterializedOffers: ...


class EquipmentOfferPort(Protocol):
    async def select_offer(self, *, candidate_group_id: str, offer_id: str, approval_id: str) -> None: ...


class EquipmentProcurementPort(Protocol):
    async def recalculate(
        self, *, request_id: str, reason: str, approval_id: str
    ) -> ProcurementRecalculation: ...


__all__ = [
    "EquipmentCatalogPort",
    "EquipmentOfferPort",
    "EquipmentProcurementPort",
    "EquipmentRequestPort",
]
