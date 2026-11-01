"""Immutable API-facing contracts for the equipment migration model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prodavan.domain.equipment.types import (
    BudgetStatus,
    CandidateGroupStatus,
    IngestionRunStatus,
    OfferStatus,
    ProcurementStatus,
    TenderStatus,
)


class EquipmentSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IngestionRun(EquipmentSchema):
    id: str
    tenant_id: str
    source_id: str = Field(min_length=1, max_length=120)
    status: IngestionRunStatus = IngestionRunStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    item_count: int = Field(default=0, ge=0)
    error_message: str | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("ingestion timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "IngestionRun":
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.status in {
            IngestionRunStatus.SUCCEEDED,
            IngestionRunStatus.PARTIAL,
            IngestionRunStatus.FAILED,
            IngestionRunStatus.CANCELLED,
        }:
            if self.finished_at is None:
                raise ValueError("terminal ingestion runs require finished_at")
        if self.status is IngestionRunStatus.FAILED and not self.error_message:
            raise ValueError("failed ingestion runs require error_message")
        return self


class CatalogSnapshot(EquipmentSchema):
    id: str
    ingestion_run_id: str
    source_id: str = Field(min_length=1, max_length=120)
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        return value
    content_hash: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogOffer(EquipmentSchema):
    id: str
    snapshot_id: str
    supplier_key: str = Field(min_length=1, max_length=160)
    external_key: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=500)
    unit_price: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    available_quantity: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()
    status: OfferStatus = OfferStatus.ACTIVE
    attributes: dict[str, Any] = Field(default_factory=dict)


class EquipmentRequestLine(EquipmentSchema):
    id: str
    request_id: str
    line_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=40)
    specification: dict[str, Any] = Field(default_factory=dict)


class EquipmentRequest(EquipmentSchema):
    id: str
    tenant_id: str
    title: str = Field(min_length=1, max_length=300)
    status: ProcurementStatus = ProcurementStatus.PLANNED
    requested_by: str | None = None
    lines: tuple[EquipmentRequestLine, ...] = ()


class CandidateGroup(EquipmentSchema):
    id: str
    request_line_id: str
    title: str = Field(min_length=1, max_length=300)
    status: CandidateGroupStatus = CandidateGroupStatus.OPEN
    offer_ids: tuple[str, ...] = ()
    selected_offer_id: str | None = None

    @model_validator(mode="after")
    def selected_offer_must_be_in_group(self) -> "CandidateGroup":
        if self.selected_offer_id is not None and self.selected_offer_id not in self.offer_ids:
            raise ValueError("selected_offer_id must reference an offer in offer_ids")
        if self.status is CandidateGroupStatus.SELECTED and self.selected_offer_id is None:
            raise ValueError("selected candidate groups require selected_offer_id")
        return self


class BudgetAllocation(EquipmentSchema):
    id: str
    request_id: str
    cost_center: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    status: BudgetStatus = BudgetStatus.RESERVED

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class ProcurementTender(EquipmentSchema):
    id: str
    tenant_id: str
    request_id: str
    title: str = Field(min_length=1, max_length=300)
    status: TenderStatus = TenderStatus.DRAFT
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    budget_allocations: tuple[BudgetAllocation, ...] = ()
    candidate_group_ids: tuple[str, ...] = ()

    @field_validator("opens_at", "closes_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("tender window timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def closing_time_after_opening(self) -> "ProcurementTender":
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        if self.status is TenderStatus.OPEN and self.opens_at is None:
            raise ValueError("open tenders require opens_at")
        return self
