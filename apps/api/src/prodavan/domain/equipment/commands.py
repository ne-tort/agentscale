"""Typed command and authority policy foundation for equipment matching.

Agent commands describe intent only.  Platform service commands are the sole
authority for offer facts and procurement calculations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class CommandActor(StrEnum):
    AGENT = "agent"
    DETERMINISTIC_SERVICE = "deterministic_service"


class EquipmentCommandName(StrEnum):
    CLASSIFY_REQUEST = "classify_request"
    CREATE_CANDIDATE_GROUP = "create_candidate_group"
    SEARCH_OFFERS = "search_offers"
    MATERIALIZE_OFFERS = "materialize_offers"
    SELECT_OFFER = "select_offer"
    RECALCULATE_PROCUREMENT = "recalculate_procurement"


class MatchKind(StrEnum):
    EXACT = "exact"
    ANALOG = "analog"
    DOUBT = "doubt"


class ApprovalRequirement(StrEnum):
    NONE = "none"
    HUMAN = "human"


class EquipmentCommand(BaseModel):
    """Common immutable command envelope with a closed input vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: EquipmentCommandName
    actor: CommandActor


class ClassifyRequestCommand(EquipmentCommand):
    command: Literal[EquipmentCommandName.CLASSIFY_REQUEST] = EquipmentCommandName.CLASSIFY_REQUEST
    actor: Literal[CommandActor.AGENT] = CommandActor.AGENT
    request_id: str = Field(min_length=1)
    request_line_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    specification: dict[str, str] = Field(default_factory=dict)


class CreateCandidateGroupCommand(EquipmentCommand):
    command: Literal[EquipmentCommandName.CREATE_CANDIDATE_GROUP] = (
        EquipmentCommandName.CREATE_CANDIDATE_GROUP
    )
    actor: Literal[CommandActor.AGENT] = CommandActor.AGENT
    request_line_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    aliases_pn: tuple[str, ...] = ()
    aliases_hash: tuple[str, ...] = ()
    match_kind: MatchKind
    note: str | None = Field(default=None, max_length=1000)


class SearchOffersCommand(EquipmentCommand):
    command: Literal[EquipmentCommandName.SEARCH_OFFERS] = EquipmentCommandName.SEARCH_OFFERS
    actor: Literal[CommandActor.AGENT] = CommandActor.AGENT
    candidate_group_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=500)


class MaterializeOffersCommand(EquipmentCommand):
    command: Literal[EquipmentCommandName.MATERIALIZE_OFFERS] = EquipmentCommandName.MATERIALIZE_OFFERS
    actor: Literal[CommandActor.DETERMINISTIC_SERVICE] = CommandActor.DETERMINISTIC_SERVICE
    request_id: str = Field(min_length=1)
    candidate_group_ids: tuple[str, ...] = ()
    catalog_snapshot_id: str = Field(min_length=1)


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)


class SelectOfferCommand(EquipmentCommand):
    command: Literal[EquipmentCommandName.SELECT_OFFER] = EquipmentCommandName.SELECT_OFFER
    actor: Literal[CommandActor.DETERMINISTIC_SERVICE] = CommandActor.DETERMINISTIC_SERVICE
    candidate_group_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    approval: Approval


class RecalculateProcurementCommand(EquipmentCommand):
    command: Literal[EquipmentCommandName.RECALCULATE_PROCUREMENT] = (
        EquipmentCommandName.RECALCULATE_PROCUREMENT
    )
    actor: Literal[CommandActor.DETERMINISTIC_SERVICE] = CommandActor.DETERMINISTIC_SERVICE
    request_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    approval: Approval


EquipmentCommandInput: TypeAlias = Annotated[
    ClassifyRequestCommand
    | CreateCandidateGroupCommand
    | SearchOffersCommand
    | MaterializeOffersCommand
    | SelectOfferCommand
    | RecalculateProcurementCommand,
    Field(discriminator="command"),
]


class EquipmentCommandPolicy:
    """Enforce authority boundaries and approval requirements for commands."""

    _APPROVALS: dict[EquipmentCommandName, ApprovalRequirement] = {
        EquipmentCommandName.CLASSIFY_REQUEST: ApprovalRequirement.NONE,
        EquipmentCommandName.CREATE_CANDIDATE_GROUP: ApprovalRequirement.NONE,
        EquipmentCommandName.SEARCH_OFFERS: ApprovalRequirement.NONE,
        EquipmentCommandName.MATERIALIZE_OFFERS: ApprovalRequirement.NONE,
        EquipmentCommandName.SELECT_OFFER: ApprovalRequirement.HUMAN,
        EquipmentCommandName.RECALCULATE_PROCUREMENT: ApprovalRequirement.HUMAN,
    }

    def approval_requirement(self, command: EquipmentCommandName) -> ApprovalRequirement:
        return self._APPROVALS[command]

    def validate(self, command: EquipmentCommand) -> EquipmentCommand:
        expected_actor = (
            CommandActor.AGENT
            if command.command in {
                EquipmentCommandName.CLASSIFY_REQUEST,
                EquipmentCommandName.CREATE_CANDIDATE_GROUP,
                EquipmentCommandName.SEARCH_OFFERS,
            }
            else CommandActor.DETERMINISTIC_SERVICE
        )
        if command.actor is not expected_actor:
            raise ValueError(f"{command.command} must be authored by {expected_actor.value}")
        if self.approval_requirement(command.command) is ApprovalRequirement.HUMAN and not getattr(
            command, "approval", None
        ):
            raise ValueError(f"{command.command} requires human approval")
        return command

    def assert_agent_cannot_authoritative_offer_facts(self, payload: dict[str, object]) -> None:
        forbidden = {"price", "unit_price", "availability", "available_quantity", "stock", "currency"}

        def find_forbidden(value: object) -> set[str]:
            if isinstance(value, dict):
                found = forbidden.intersection(value)
                for nested in value.values():
                    found.update(find_forbidden(nested))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for nested in value:
                    found.update(find_forbidden(nested))
                return found
            return set()

        authored = find_forbidden(payload)
        if authored:
            fields = ", ".join(sorted(authored))
            raise ValueError(f"agent cannot author authoritative offer facts: {fields}")


__all__ = [
    "Approval",
    "ApprovalRequirement",
    "ClassifyRequestCommand",
    "CommandActor",
    "CreateCandidateGroupCommand",
    "EquipmentCommand",
    "EquipmentCommandInput",
    "EquipmentCommandName",
    "EquipmentCommandPolicy",
    "MaterializeOffersCommand",
    "MatchKind",
    "RecalculateProcurementCommand",
    "SearchOffersCommand",
    "SelectOfferCommand",
]
