"""Durable plugin route projections.

Revision ID: 2026110905
Revises: 2026110904

Additive only: create the secret-free plugin_routes table. Existing grant,
instance, source, approval and artifact schemas are not changed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2026110905"
down_revision: str | Sequence[str] | None = "2026110904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_routes"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("plugin_id", sa.String(length=160), nullable=False),
        sa.Column("grant_id", sa.String(length=80), primary_key=True, nullable=False),
        sa.Column("instance_id", sa.String(length=80), nullable=False),
        sa.Column("webhook_path", sa.String(length=1024), nullable=False),
        sa.Column("pluginpage_path", sa.String(length=1024), nullable=False),
        sa.Column("capability_path", sa.String(length=1024), nullable=False),
        sa.Column(
            "egress_rule_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("deployment", sa.String(length=63), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "company_id",
            "plugin_id",
            "grant_id",
            name="uq_plugin_route_company_plugin_grant",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["plugin_capability_grants.id"],
            name="fk_plugin_route_grant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "egress_rule_count >= 0",
            name="ck_plugin_route_egress_rule_count",
        ),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
