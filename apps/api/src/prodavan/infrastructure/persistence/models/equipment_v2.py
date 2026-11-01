"""Equipment procurement migration tables.

This module is intentionally independent from the legacy ``mod_equipment``
JSON rows. It is a foundation for a later migration and is not registered in
legacy module code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class EquipmentIngestionRunRow(Base):
    __tablename__ = "equipment_v2_ingestion_runs"
    __table_args__ = (
        CheckConstraint("item_count >= 0", name="ck_equipment_v2_ingestion_item_count_nonnegative"),
        Index(
            "ix_equipment_v2_ingestion_tenant_status_created",
            "tenant_id",
            "status",
            text("created_at DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("eir"))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EquipmentCatalogSnapshotRow(Base):
    __tablename__ = "equipment_v2_catalog_snapshots"
    __table_args__ = (
        UniqueConstraint("ingestion_run_id", "content_hash", name="uq_equipment_v2_snapshot_hash"),
        Index(
            "ix_equipment_v2_snapshots_source_captured",
            "source_id",
            text("captured_at DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("ecs"))
    ingestion_run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("equipment_v2_ingestion_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class EquipmentCatalogOfferRow(Base):
    __tablename__ = "equipment_v2_catalog_offers"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "external_key", name="uq_equipment_v2_offer_external"),
        CheckConstraint("unit_price >= 0", name="ck_equipment_v2_offer_price_nonnegative"),
        CheckConstraint("available_quantity >= 0", name="ck_equipment_v2_offer_quantity_nonnegative"),
        CheckConstraint("currency = upper(currency)", name="ck_equipment_v2_offer_currency_upper"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("eco"))
    snapshot_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("equipment_v2_catalog_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_key: Mapped[str] = mapped_column(String(160), nullable=False)
    external_key: Mapped[str] = mapped_column(String(240), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    available_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class EquipmentRequestRow(Base):
    __tablename__ = "equipment_v2_requests"
    __table_args__ = (
        Index(
            "ix_equipment_v2_requests_tenant_status_created",
            "tenant_id",
            "status",
            text("created_at DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("erq"))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EquipmentRequestLineRow(Base):
    __tablename__ = "equipment_v2_request_lines"
    __table_args__ = (
        UniqueConstraint("request_id", "line_number", name="uq_equipment_v2_request_line_number"),
        CheckConstraint("line_number > 0", name="ck_equipment_v2_request_line_positive"),
        CheckConstraint("quantity > 0", name="ck_equipment_v2_request_quantity_positive"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("erl"))
    request_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("equipment_v2_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    specification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class EquipmentCandidateGroupRow(Base):
    __tablename__ = "equipment_v2_candidate_groups"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("ecg"))
    request_line_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("equipment_v2_request_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    selected_offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment_v2_catalog_offers.id", ondelete="SET NULL"), nullable=True
    )
    offer_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class EquipmentTenderRow(Base):
    __tablename__ = "equipment_v2_tenders"
    __table_args__ = (
        Index("ix_equipment_v2_tenders_request_status", "request_id", "status"),
        Index(
            "ix_equipment_v2_tenders_status_closes",
            "status",
            text("closes_at ASC"),
        ),
        CheckConstraint(
            "opens_at IS NULL OR closes_at IS NULL OR closes_at > opens_at",
            name="ck_equipment_v2_tender_window_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("etd"))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("equipment_v2_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_group_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class EquipmentBudgetAllocationRow(Base):
    __tablename__ = "equipment_v2_budget_allocations"
    __table_args__ = (
        Index("ix_equipment_v2_budget_request_status", "request_id", "status"),
        CheckConstraint("amount > 0", name="ck_equipment_v2_budget_amount_positive"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("eba"))
    request_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("equipment_v2_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cost_center: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
