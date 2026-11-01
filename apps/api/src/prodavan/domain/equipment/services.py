"""Use-case service interfaces for the equipment migration."""

from __future__ import annotations

from typing import Protocol

from prodavan.domain.equipment.schemas import CandidateGroup, IngestionRun, ProcurementTender


class EquipmentIngestionService(Protocol):
    async def ingest(self, *, tenant_id: str, source_key: str) -> IngestionRun: ...


class EquipmentProcurementService(Protocol):
    async def open_tender(self, tender_id: str) -> ProcurementTender: ...

    async def select_candidate(self, group_id: str, offer_id: str) -> CandidateGroup: ...
