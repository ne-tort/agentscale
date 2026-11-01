"""Application ports for the equipment migration domain."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from prodavan.domain.equipment.schemas import (
    CatalogOffer,
    CandidateGroup,
    CatalogSnapshot,
    EquipmentRequest,
    EquipmentRequestLine,
    IngestionRun,
    ProcurementTender,
)


class EquipmentRepository(Protocol):
    async def create_ingestion_run(self, run: IngestionRun) -> IngestionRun: ...

    async def complete_ingestion_run(self, run_id: str, *, item_count: int) -> IngestionRun: ...

    async def fail_ingestion_run(self, run_id: str, *, error_message: str) -> IngestionRun: ...

    async def save_snapshot(self, snapshot: CatalogSnapshot) -> CatalogSnapshot: ...

    async def save_offers(self, offers: Sequence[CatalogOffer]) -> int: ...

    async def create_request(self, request: EquipmentRequest) -> EquipmentRequest: ...

    async def add_request_line(self, line: EquipmentRequestLine) -> EquipmentRequestLine: ...

    async def save_candidate_group(self, group: CandidateGroup) -> CandidateGroup: ...

    async def create_tender(self, tender: ProcurementTender) -> ProcurementTender: ...

    async def get_tender(self, tender_id: str) -> ProcurementTender | None: ...
