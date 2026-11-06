"""Source-level contracts for equipment intent and authority boundaries."""

import pytest
from pydantic import ValidationError

from prodavan.domain.equipment.commands import (
    Approval,
    ApprovalRequirement,
    ClassifyRequestCommand,
    CommandActor,
    CreateCandidateGroupCommand,
    EquipmentCommandName,
    EquipmentCommandPolicy,
    MaterializeOffersCommand,
    RecalculateProcurementCommand,
    SearchOffersCommand,
    SelectOfferCommand,
)
from tests.fixtures.equipment_commands import (
    AGENT_GROUP_PAYLOAD,
    AUTHORITATIVE_OFFER_FACTS,
    GROUP_ID,
    OFFER_ID,
    REQUEST_ID,
    REQUEST_LINE_ID,
)


def test_agent_commands_capture_intent_without_offer_facts():
    classify = ClassifyRequestCommand(
        request_id=REQUEST_ID,
        request_line_id=REQUEST_LINE_ID,
        title="Rack server",
    )
    group = CreateCandidateGroupCommand(**AGENT_GROUP_PAYLOAD)
    search = SearchOffersCommand(candidate_group_id=GROUP_ID, query="SRV-1000")

    assert classify.actor is CommandActor.AGENT
    assert group.match_kind.value == "exact"
    assert search.command is EquipmentCommandName.SEARCH_OFFERS
    with pytest.raises(ValidationError):
        CreateCandidateGroupCommand(**AGENT_GROUP_PAYLOAD, price="100.00")


def test_policy_rejects_agent_authored_authoritative_offer_facts():
    policy = EquipmentCommandPolicy()
    with pytest.raises(ValueError, match="authoritative offer facts"):
        policy.assert_agent_cannot_authoritative_offer_facts(AUTHORITATIVE_OFFER_FACTS)
    with pytest.raises(ValueError, match="authoritative offer facts"):
        policy.assert_agent_cannot_authoritative_offer_facts({"result": {"offer": {"currency": "RUB"}}})


def test_deterministic_commands_and_approval_requirements_are_explicit():
    policy = EquipmentCommandPolicy()
    materialize = MaterializeOffersCommand(
        request_id=REQUEST_ID,
        candidate_group_ids=(GROUP_ID,),
        catalog_snapshot_id="snapshot-1",
    )
    select = SelectOfferCommand(
        candidate_group_id=GROUP_ID,
        offer_id=OFFER_ID,
        approval=Approval(approval_id="approval-1", approved_by="employee-1"),
    )
    recalculate = RecalculateProcurementCommand(
        request_id=REQUEST_ID,
        reason="offer selected",
        approval=Approval(approval_id="approval-2", approved_by="employee-1"),
    )

    assert materialize.actor is CommandActor.DETERMINISTIC_SERVICE
    assert policy.approval_requirement(select.command) is ApprovalRequirement.HUMAN
    assert policy.approval_requirement(recalculate.command) is ApprovalRequirement.HUMAN
    assert policy.validate(select) is select
    assert policy.validate(recalculate) is recalculate


def test_policy_rejects_mismatched_actor_and_missing_approval():
    policy = EquipmentCommandPolicy()
    mismatched = MaterializeOffersCommand(
        request_id=REQUEST_ID,
        catalog_snapshot_id="snapshot-1",
    ).model_copy(update={"actor": CommandActor.AGENT})
    with pytest.raises(ValueError, match="must be authored"):
        policy.validate(mismatched)
    with pytest.raises(ValidationError, match="approval"):
        SelectOfferCommand(candidate_group_id=GROUP_ID, offer_id=OFFER_ID)
