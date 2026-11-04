"""Equipment procurement migration tables.

This module is intentionally independent from the legacy ``mod_equipment``
JSON rows. It is a foundation for a later migration and is not registered in
legacy module code.

Tenant/project anchors (task ``MIG-EQ-ANCHORS-01``, revision ``2026110318``)
=========================================================================
``docs/08-migration/equipment-association-tables.md`` section 2.1 asks for a
*preparatory* scope revision before any project-scoped cutover: a canonical
``project_id`` on the operational rows and the relational replacement of the two
JSONB reference arrays.  This module is that preparation step, and it is
deliberately the **nullable** stage of it:

* ``equipment_v2_requests``, ``equipment_v2_ingestion_runs`` and
  ``equipment_v2_tenders`` gain ``project_id``/``cabinet_id``.  Both are
  ``NULL``-able so that every row written before the backfill keeps working
  (section 2.1: a nullable anchor plus a backfill first, a later revision that
  forbids ``NULL`` second), and the compound ``(tenant_id, cabinet_id,
  project_id)`` tuple is *not* yet enforced by a composite FK: a partially
  backfilled table must not reject the rows it cannot prove yet.
* ``equipment_v2_candidate_group_offers`` and
  ``equipment_v2_tender_candidate_groups`` are created with their own
  ``tenant_id`` (``NOT NULL`` — the tables start empty, so every association row
  is provably tenant-owned from its first insert) and a nullable
  ``cabinet_id``/``project_id`` pair mirroring the parent's scope.  The scoped
  *composite* foreign keys of sections 3.1/3.2 are **not** installed yet: they
  require ``tenant_id, project_id NOT NULL`` on ``candidate_groups`` and
  ``catalog_offers``, which this revision does not introduce (the catalog chain
  stays tenant-scoped shared catalog with ``project_id IS NULL``).  What is
  installed here is the part a plain FK can prove today - parent existence and
  delete semantics - plus the deferred ordinal uniqueness that makes a
  transactional reorder safe.
* The JSONB arrays (``candidate_groups.offer_ids``,
  ``tenders.candidate_group_ids``) stay in place and stay authoritative.
  Sections 5/6 of the design: they are the rollback source until the cutover
  window ends, so this revision is "expand" only - no backfill, no dual-write,
  no read cutover.

New columns are declared **last** in their class on purpose: ``ALTER TABLE ...
ADD COLUMN`` appends them to the physical table, so model column order matches
the migrated order and ``alembic revision --autogenerate`` stays empty.
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
        # MIG-EQ-ANCHORS-01: the project feed ("runs of this project, newest
        # first") and the cabinet regression check by anchor.
        Index(
            "ix_equipment_v2_ingestion_project_created",
            "project_id",
            text("created_at DESC"),
        ),
        Index("ix_equipment_v2_ingestion_cabinet_id", "cabinet_id"),
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
    # MIG-EQ-ANCHORS-01: the import context.  ``NULL`` keeps the shared-catalog
    # policy of the design doc (one catalog may serve several projects); a
    # project-scoped import records the project it was requested for, and the
    # snapshots/offers below still inherit nothing from it (shared catalog).
    project_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    cabinet_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("cabinet_instances.id", ondelete="RESTRICT"), nullable=True
    )


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
        # MIG-EQ-ANCHORS-01: partition boundary for procurement per project.
        Index(
            "ix_equipment_v2_requests_project_status_created",
            "project_id",
            "status",
            text("created_at DESC"),
        ),
        Index("ix_equipment_v2_requests_cabinet_id", "cabinet_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("erq"))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # MIG-EQ-ANCHORS-01: the request belongs to a project (and, through it, to a
    # cabinet).  ``NULL`` only for rows written before the backfill; once the
    # backfill is proven, a later revision forbids ``NULL`` (design doc 2.1).
    project_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    cabinet_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("cabinet_instances.id", ondelete="RESTRICT"), nullable=True
    )


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
        # MIG-EQ-ANCHORS-01: the project's live tenders.
        Index("ix_equipment_v2_tenders_project_status", "project_id", "status"),
        Index("ix_equipment_v2_tenders_cabinet_id", "cabinet_id"),
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
    # MIG-EQ-ANCHORS-01: the tender's scope, which must agree with its request's
    # scope.  Both sides are nullable in this expand stage, so the equality is a
    # service/trigger invariant until the backfill allows ``NOT NULL``.
    project_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    cabinet_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("cabinet_instances.id", ondelete="RESTRICT"), nullable=True
    )


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
