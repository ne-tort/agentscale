"""Application orchestration for the exclusive equipment matching zone.

The service coordinates typed commands across equipment_v2, ingestion, and policy
ports. It never constructs authoritative offer facts and never treats agent input
as permission to select an offer or recalculate procurement.
"""

from __future__ import annotations

from prodavan.domain.equipment.commands import (
    ClassifyRequestCommand,
    CreateCandidateGroupCommand,
    EquipmentCommandPolicy,
    MaterializeOffersCommand,
    RecalculateProcurementCommand,
    SelectOfferCommand,
)
from .contracts import CandidateGroupDraft, MaterializedOffers, ProcurementRecalculation, RequestClassification
from .ports import EquipmentCatalogPort, EquipmentOfferPort, EquipmentProcurementPort, EquipmentRequestPort


class EquipmentMatchingService:
    """Coordinate classification, candidate groups, offers and procurement."""

    def __init__(
        self,
        *,
        requests: EquipmentRequestPort,
        catalog: EquipmentCatalogPort,
        offers: EquipmentOfferPort,
        procurement: EquipmentProcurementPort,
        policy: EquipmentCommandPolicy | None = None,
    ) -> None:
        self._requests = requests
        self._catalog = catalog
        self._offers = offers
        self._procurement = procurement
        self._policy = policy or EquipmentCommandPolicy()

    async def classify(self, command: ClassifyRequestCommand) -> RequestClassification:
        self._policy.validate(command)
        self._policy.assert_agent_cannot_authoritative_offer_facts(command.model_dump())
        return await self._requests.classify_request(command.request_id, command.request_line_id)

    async def create_candidate_group(self, command: CreateCandidateGroupCommand) -> CandidateGroupDraft:
        self._policy.validate(command)
        self._policy.assert_agent_cannot_authoritative_offer_facts(command.model_dump())
        group = CandidateGroupDraft(
            id=self._candidate_id(command),
            request_line_id=command.request_line_id,
            title=command.title,
            aliases_pn=command.aliases_pn,
            aliases_hash=command.aliases_hash,
            match_kind=command.match_kind,
            note=command.note,
        )
        return await self._requests.save_candidate_group(group)

    async def materialize(self, command: MaterializeOffersCommand) -> MaterializedOffers:
        self._policy.validate(command)
        return await self._catalog.materialize_offers(
            request_id=command.request_id,
            candidate_group_ids=command.candidate_group_ids,
            catalog_snapshot_id=command.catalog_snapshot_id,
        )

    async def select(self, command: SelectOfferCommand) -> None:
        self._policy.validate(command)
        await self._offers.select_offer(
            candidate_group_id=command.candidate_group_id,
            offer_id=command.offer_id,
            approval_id=command.approval.approval_id,
        )

    async def recalculate(self, command: RecalculateProcurementCommand) -> ProcurementRecalculation:
        self._policy.validate(command)
        return await self._procurement.recalculate(
            request_id=command.request_id,
            reason=command.reason,
            approval_id=command.approval.approval_id,
        )

    @staticmethod
    def _candidate_id(command: CreateCandidateGroupCommand) -> str:
        """Stable id is supplied by the application boundary, not offer facts."""
        import hashlib

        payload = "|".join(
            (
                command.request_line_id,
                command.title,
                command.match_kind.value,
                ",".join(command.aliases_pn),
                ",".join(command.aliases_hash),
            )
        )
        return f"group-{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


__all__ = ["EquipmentMatchingService"]
