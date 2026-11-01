"""Source contracts for the exclusive equipment matching application zone.

These tests are intentionally not executed during migration verification freeze.
"""

from decimal import Decimal

import pytest
from prodavan.application.equipment_matching import EquipmentMatchingService
from prodavan.application.equipment_matching.contracts import (
    CandidateGroupDraft,
    MaterializedOffers,
    ProcurementRecalculation,
    RequestClassification,
)
from prodavan.domain.equipment.commands import (
    ClassifyRequestCommand,
    CreateCandidateGroupCommand,
    MaterializeOffersCommand,
    RecalculateProcurementCommand,
    SelectOfferCommand,
)


class RequestPort:
    def __init__(self):
        self.saved = []

    async def classify_request(self, request_id, request_line_id):
        return RequestClassification(
            request_id=request_id,
            request_line_id=request_line_id,
            normalized_title="Rack server",
            specification={"cpu": "x86"},
        )

    async def save_candidate_group(self, group):
        self.saved.append(group)
        return group


class CatalogPort:
    async def materialize_offers(self, *, request_id, candidate_group_ids, catalog_snapshot_id):
        self.last_call = {
            "request_id": request_id,
            "candidate_group_ids": tuple(candidate_group_ids),
            "catalog_snapshot_id": catalog_snapshot_id,
        }
        return MaterializedOffers(
            request_id=request_id,
            catalog_snapshot_id=catalog_snapshot_id,
            candidate_group_ids=tuple(candidate_group_ids),
        )


class OfferPort:
    def __init__(self):
        self.selected = None

    async def select_offer(self, **kwargs):
        self.selected = kwargs


class ProcurementPort:
    def __init__(self):
        self.calls = []

    async def recalculate(self, **kwargs):
        self.calls.append(kwargs)
        return ProcurementRecalculation(
            request_id=kwargs["request_id"],
            reason=kwargs["reason"],
            total=Decimal("0"),
            currency="RUB",
        )


def service():
    requests = RequestPort()
    catalog = CatalogPort()
    offers = OfferPort()
    procurement = ProcurementPort()
    return (
        EquipmentMatchingService(
            requests=requests,
            catalog=catalog,
            offers=offers,
            procurement=procurement,
        ),
        requests,
        catalog,
        offers,
        procurement,
    )


@pytest.mark.asyncio
async def test_classify_and_create_group_keep_agent_payload_typed():
    matching, requests, _, _, _ = service()

    result = await matching.classify(
        ClassifyRequestCommand(request_id="request-1", request_line_id="line-1", title="Rack server")
    )
    group = await matching.create_candidate_group(
        CreateCandidateGroupCommand(
            request_line_id="line-1",
            title="Rack server exact",
            aliases_pn=("RS-1",),
            match_kind="exact",
        )
    )

    assert result.normalized_title == "Rack server"
    assert isinstance(group, CandidateGroupDraft)
    assert group.aliases_pn == ("RS-1",)
    assert requests.saved == [group]


@pytest.mark.asyncio
async def test_materialize_uses_snapshot_and_returns_platform_offers():
    matching, _, _, _, _ = service()
    result = await matching.materialize(
        MaterializeOffersCommand(
            request_id="request-1",
            candidate_group_ids=("group-1",),
            catalog_snapshot_id="snapshot-1",
        )
    )
    assert result.catalog_snapshot_id == "snapshot-1"
    assert result.offers == ()


def test_candidate_group_requires_search_key():
    with pytest.raises(ValueError, match="at least one part-number or hash alias"):
        CandidateGroupDraft(
            id="group-1",
            request_line_id="line-1",
            title="Rack server",
            match_kind="doubt",
        )


def test_materialized_offers_reject_cross_snapshot_provenance():
    from prodavan.domain.equipment.schemas import CatalogOffer

    with pytest.raises(ValueError, match="requested catalog snapshot"):
        MaterializedOffers(
            request_id="request-1",
            catalog_snapshot_id="snapshot-1",
            candidate_group_ids=("group-1",),
            offers=(
                CatalogOffer(
                    id="offer-1",
                    snapshot_id="snapshot-2",
                    supplier_key="supplier-1",
                    external_key="external-1",
                    title="Rack server",
                    unit_price=Decimal("10"),
                    currency="RUB",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_select_and_recalculate_require_human_approval():
    matching, _, _, offers, procurement = service()

    with pytest.raises(ValueError, match="requires human approval"):
        await matching.select(
            SelectOfferCommand(candidate_group_id="group-1", offer_id="offer-1")
        )
    with pytest.raises(ValueError, match="requires human approval"):
        await matching.recalculate(
            RecalculateProcurementCommand(request_id="request-1", reason="refresh")
        )

    from prodavan.domain.equipment.commands import Approval

    await matching.select(
        SelectOfferCommand(
            candidate_group_id="group-1",
            offer_id="offer-1",
            approval=Approval(approval_id="approval-1", approved_by="human-1"),
        )
    )
    await matching.recalculate(
        RecalculateProcurementCommand(
            request_id="request-1",
            reason="approved refresh",
            approval=Approval(approval_id="approval-1", approved_by="human-1"),
        )
    )
    assert offers.selected["approval_id"] == "approval-1"
    assert procurement.calls[0]["approval_id"] == "approval-1"


@pytest.mark.asyncio
async def test_agent_cannot_author_authoritative_offer_facts():
    matching, _, _, _, _ = service()
    with pytest.raises(ValueError, match="authoritative offer facts"):
        await matching.classify(
            ClassifyRequestCommand(
                request_id="request-1",
                request_line_id="line-1",
                title="Server",
                specification={"price": "1"},
            )
        )
