"""Create the ``equipment_v2`` procurement tables.

Source of truth: ``prodavan.infrastructure.persistence.models.equipment_v2``
(eight declarative tables registered in ``models/__init__.py``). This revision is
a metadata-parity translation of that module - table names, column names, column
order, types, nullability, foreign keys with their ``ondelete`` action, composite
uniques, checks, JSONB usage and indexes are copied one-to-one, so a reviewed
``alembic revision --autogenerate`` produces an empty diff.

Deliberate limits (see ``docs/08-migration/equipment-persistence-adapter-contract.md``,
``equipment-association-tables.md`` and ``equipment-evidence-handoff.md``):

* No scope columns are invented. ``project_id``/``cabinet_id`` do not exist in the
  ORM yet; the contract (section 4.1) requires a separate preparatory revision plus
  a backfill before project-scoped cutover. Adding them here would create schema the
  models cannot describe.
* Status columns stay ``varchar(24)`` without a value CHECK. The status vocabulary
  (``queued``/``active``/``planned``/``open``/``draft``/``reserved`` and the later
  adapter outcome classifications) is owned by the typed command layer, and the
  persistence adapter must not become a second policy owner. Quantities, money,
  line identity, currency shaping and the tender window ordering are enforced in the
  database exactly as the models declare them.
* ``candidate_groups.offer_ids`` and ``tenders.candidate_group_ids`` remain JSONB
  compatibility storage. The normalized ``equipment_v2_candidate_group_offers`` /
  ``equipment_v2_tender_candidate_groups`` association tables are a later revision.
* No RLS: policies and trusted tenant context belong to revision ``2026110307``.
* Nothing in this file was executed against a database; the migration verification
  freeze is in force.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110305"
down_revision = "2026110304"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "equipment_v2_ingestion_runs",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("item_count >= 0", name="ck_equipment_v2_ingestion_item_count_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_v2_ingestion_runs_tenant_id", "equipment_v2_ingestion_runs", ["tenant_id"])
    op.create_index(
        "ix_equipment_v2_ingestion_tenant_status_created",
        "equipment_v2_ingestion_runs",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )

    op.create_table(
        "equipment_v2_catalog_snapshots",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("ingestion_run_id", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_payload, nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["equipment_v2_ingestion_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingestion_run_id", "content_hash", name="uq_equipment_v2_snapshot_hash"),
    )
    op.create_index(
        "ix_equipment_v2_catalog_snapshots_ingestion_run_id",
        "equipment_v2_catalog_snapshots",
        ["ingestion_run_id"],
    )
    op.create_index(
        "ix_equipment_v2_snapshots_source_captured",
        "equipment_v2_catalog_snapshots",
        ["source_id", sa.text("captured_at DESC")],
    )

    op.create_table(
        "equipment_v2_catalog_offers",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("snapshot_id", sa.String(40), nullable=False),
        sa.Column("supplier_key", sa.String(160), nullable=False),
        sa.Column("external_key", sa.String(240), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("available_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attributes", json_payload, nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["equipment_v2_catalog_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "external_key", name="uq_equipment_v2_offer_external"),
        sa.CheckConstraint("unit_price >= 0", name="ck_equipment_v2_offer_price_nonnegative"),
        sa.CheckConstraint("available_quantity >= 0", name="ck_equipment_v2_offer_quantity_nonnegative"),
        sa.CheckConstraint("currency = upper(currency)", name="ck_equipment_v2_offer_currency_upper"),
    )
    op.create_index("ix_equipment_v2_catalog_offers_snapshot_id", "equipment_v2_catalog_offers", ["snapshot_id"])

    op.create_table(
        "equipment_v2_requests",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_v2_requests_tenant_id", "equipment_v2_requests", ["tenant_id"])
    op.create_index(
        "ix_equipment_v2_requests_tenant_status_created",
        "equipment_v2_requests",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )

    op.create_table(
        "equipment_v2_request_lines",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("request_id", sa.String(40), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("specification", json_payload, nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["equipment_v2_requests.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "line_number", name="uq_equipment_v2_request_line_number"),
        sa.CheckConstraint("line_number > 0", name="ck_equipment_v2_request_line_positive"),
        sa.CheckConstraint("quantity > 0", name="ck_equipment_v2_request_quantity_positive"),
    )
    op.create_index("ix_equipment_v2_request_lines_request_id", "equipment_v2_request_lines", ["request_id"])

    op.create_table(
        "equipment_v2_candidate_groups",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("request_line_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("selected_offer_id", sa.String(40), nullable=True),
        sa.Column("offer_ids", json_payload, nullable=False),
        sa.ForeignKeyConstraint(
            ["request_line_id"],
            ["equipment_v2_request_lines.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["selected_offer_id"],
            ["equipment_v2_catalog_offers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_v2_candidate_groups_request_line_id",
        "equipment_v2_candidate_groups",
        ["request_line_id"],
    )

    op.create_table(
        "equipment_v2_budget_allocations",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("request_id", sa.String(40), nullable=False),
        sa.Column("cost_center", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["equipment_v2_requests.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_equipment_v2_budget_amount_positive"),
    )
    op.create_index("ix_equipment_v2_budget_allocations_request_id", "equipment_v2_budget_allocations", ["request_id"])
    op.create_index(
        "ix_equipment_v2_budget_request_status",
        "equipment_v2_budget_allocations",
        ["request_id", "status"],
    )

    op.create_table(
        "equipment_v2_tenders",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_group_ids", json_payload, nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["equipment_v2_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "opens_at IS NULL OR closes_at IS NULL OR closes_at > opens_at",
            name="ck_equipment_v2_tender_window_order",
        ),
    )
    op.create_index("ix_equipment_v2_tenders_tenant_id", "equipment_v2_tenders", ["tenant_id"])
    op.create_index("ix_equipment_v2_tenders_request_id", "equipment_v2_tenders", ["request_id"])
    op.create_index("ix_equipment_v2_tenders_request_status", "equipment_v2_tenders", ["request_id", "status"])
    op.create_index(
        "ix_equipment_v2_tenders_status_closes",
        "equipment_v2_tenders",
        ["status", sa.text("closes_at ASC")],
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_v2_tenders_status_closes", table_name="equipment_v2_tenders")
    op.drop_index("ix_equipment_v2_tenders_request_status", table_name="equipment_v2_tenders")
    op.drop_index("ix_equipment_v2_tenders_request_id", table_name="equipment_v2_tenders")
    op.drop_index("ix_equipment_v2_tenders_tenant_id", table_name="equipment_v2_tenders")
    op.drop_table("equipment_v2_tenders")

    op.drop_index("ix_equipment_v2_budget_request_status", table_name="equipment_v2_budget_allocations")
    op.drop_index("ix_equipment_v2_budget_allocations_request_id", table_name="equipment_v2_budget_allocations")
    op.drop_table("equipment_v2_budget_allocations")

    op.drop_index("ix_equipment_v2_candidate_groups_request_line_id", table_name="equipment_v2_candidate_groups")
    op.drop_table("equipment_v2_candidate_groups")

    op.drop_index("ix_equipment_v2_request_lines_request_id", table_name="equipment_v2_request_lines")
    op.drop_table("equipment_v2_request_lines")

    op.drop_index("ix_equipment_v2_requests_tenant_status_created", table_name="equipment_v2_requests")
    op.drop_index("ix_equipment_v2_requests_tenant_id", table_name="equipment_v2_requests")
    op.drop_table("equipment_v2_requests")

    op.drop_index("ix_equipment_v2_catalog_offers_snapshot_id", table_name="equipment_v2_catalog_offers")
    op.drop_table("equipment_v2_catalog_offers")

    op.drop_index("ix_equipment_v2_snapshots_source_captured", table_name="equipment_v2_catalog_snapshots")
    op.drop_index(
        "ix_equipment_v2_catalog_snapshots_ingestion_run_id",
        table_name="equipment_v2_catalog_snapshots",
    )
    op.drop_table("equipment_v2_catalog_snapshots")

    op.drop_index("ix_equipment_v2_ingestion_tenant_status_created", table_name="equipment_v2_ingestion_runs")
    op.drop_index("ix_equipment_v2_ingestion_runs_tenant_id", table_name="equipment_v2_ingestion_runs")
    op.drop_table("equipment_v2_ingestion_runs")
