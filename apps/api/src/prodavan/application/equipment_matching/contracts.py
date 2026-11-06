"""Typed application contracts for equipment request matching and procurement."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prodavan.domain.equipment.commands import MatchKind
from prodavan.domain.equipment.schemas import CatalogOffer


class MatchingContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class RequestClassification(MatchingContract):
    request_id: str = Field(min_length=1)
    request_line_id: str = Field(min_length=1)
    normalized_title: str = Field(min_length=1, max_length=500)
    specification: dict[str, str] = Field(default_factory=dict)
    candidate_group_ids: tuple[str, ...] = ()


class CandidateGroupDraft(MatchingContract):
    id: str = Field(min_length=1)
    request_line_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    aliases_pn: tuple[str, ...] = ()
    aliases_hash: tuple[str, ...] = ()
    match_kind: MatchKind
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_group_key(self) -> "CandidateGroupDraft":
        if not self.aliases_pn and not self.aliases_hash:
            raise ValueError("candidate groups require at least one part-number or hash alias")
        return self


class MaterializedOffers(MatchingContract):
    request_id: str = Field(min_length=1)
    catalog_snapshot_id: str = Field(min_length=1)
    candidate_group_ids: tuple[str, ...]
    offers: tuple[CatalogOffer, ...] = ()

    @model_validator(mode="after")
    def offers_must_use_materialization_snapshot(self) -> "MaterializedOffers":
        mismatched = {
            offer.snapshot_id for offer in self.offers if offer.snapshot_id != self.catalog_snapshot_id
        }
        if mismatched:
            raise ValueError("materialized offers must reference the requested catalog snapshot")
        return self


class SelectedOffer(MatchingContract):
    candidate_group_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)


class ProcurementRecalculation(MatchingContract):
    request_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    total: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    line_totals: dict[str, Decimal] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "CandidateGroupDraft",
    "MaterializedOffers",
    "ProcurementRecalculation",
    "RequestClassification",
    "SelectedOffer",
]
