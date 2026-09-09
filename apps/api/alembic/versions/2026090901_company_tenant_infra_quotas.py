"""Tenant infra company quotas — 2026090901."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026090901"
down_revision = "2026090802"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_tenant_infra_quotas",
        sa.Column("company_id", sa.String(length=40), primary_key=True),
        sa.Column("cache_ops_per_minute", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("cache_max_keys", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("cache_max_value_bytes", sa.Integer(), nullable=False, server_default="65536"),
        sa.Column("cache_default_ttl_sec", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("cache_max_ttl_sec", sa.Integer(), nullable=False, server_default="604800"),
        sa.Column("docs_ops_per_minute", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("docs_max_collections", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("docs_max_docs_per_collection", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("docs_max_doc_bytes", sa.Integer(), nullable=False, server_default="262144"),
        sa.Column("userdb_ops_per_minute", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("userdb_max_tables", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("userdb_max_rows_per_table", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("userdb_max_row_bytes", sa.Integer(), nullable=False, server_default="65536"),
        sa.Column("kafka_ops_per_minute", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("kafka_max_payload_bytes", sa.Integer(), nullable=False, server_default="65536"),
        sa.Column("kafka_max_backlog", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("kafka_retention_sec", sa.Integer(), nullable=False, server_default="172800"),
        sa.Column("objects_ops_per_minute", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("objects_max_per_project", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("objects_max_bytes", sa.Integer(), nullable=False, server_default="52428800"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("company_tenant_infra_quotas")
