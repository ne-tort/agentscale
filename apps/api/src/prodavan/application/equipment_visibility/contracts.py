"""Contracts for the equipment source visibility and provenance policy."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VisibilityContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class VisibilityContext(VisibilityContract):
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    supplier_keys: frozenset[str] = frozenset()
    now: datetime
    max_snapshot_age_seconds: int = Field(default=86400, gt=0)

    @model_validator(mode="after")
    def timezone_required(self) -> "VisibilityContext":
        if self.now.tzinfo is None:
            raise ValueError("visibility context now must be timezone-aware")
        return self


class SourceProvenance(VisibilityContract):
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    supplier_key: str = Field(min_length=1)
    source_chain: tuple[str, ...] = ()
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def chain_required(self) -> "SourceProvenance":
        if not self.source_chain:
            raise ValueError("source provenance requires a non-empty source chain")
        return self


class CatalogSnapshotVisibility(VisibilityContract):
    id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    captured_at: datetime
    source_chain: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> "CatalogSnapshotVisibility":
        if self.captured_at.tzinfo is None:
            raise ValueError("catalog snapshot captured_at must be timezone-aware")
        if not self.source_chain:
            raise ValueError("catalog snapshot requires source chain")
        return self


class VisibleOffer(VisibilityContract):
    id: str = Field(min_length=1)
    candidate_group_id: str = Field(min_length=1)
    part_number: str = Field(min_length=1)
    supplier_key: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    provenance: SourceProvenance
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def supplier_matches_provenance(self) -> "VisibleOffer":
        if self.supplier_key != self.provenance.supplier_key:
            raise ValueError("offer supplier must match source provenance")
        return self


class SelectedOfferVisibility(VisibilityContract):
    candidate_group_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)


class MaterializationVisibility(VisibilityContract):
    request_id: str = Field(min_length=1)
    catalog_snapshot_id: str = Field(min_length=1)
    offers: tuple[VisibleOffer, ...] = ()

    @model_validator(mode="after")
    def unique_supplier_per_part_number(self) -> "MaterializationVisibility":
        seen: set[tuple[str, str]] = set()
        for offer in self.offers:
            if offer.snapshot_id != self.catalog_snapshot_id:
                raise ValueError("offer snapshot does not match requested catalog snapshot")
            key = (offer.part_number.casefold(), offer.supplier_key.casefold())
            if key in seen:
                raise ValueError("same part number and supplier must remain a single separated offer")
            seen.add(key)
        return self


__all__ = [
    "CatalogSnapshotVisibility",
    "MaterializationVisibility",
    "SelectedOfferVisibility",
    "SourceProvenance",
    "VisibilityContext",
    "VisibleOffer",
]
