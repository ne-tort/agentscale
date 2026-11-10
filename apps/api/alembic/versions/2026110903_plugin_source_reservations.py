"""Distributed pre-provider reservations for ModuleSource creation.

Revision ID: 2026110903
Revises: 2026110902
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2026110903"
down_revision: str | Sequence[str] | None = "2026110902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_source_reservations"
_FULL_NAME_RE = "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
_DEFAULT_BRANCH_RE = "^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$"
_WORKFLOW_RE = (
    "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    "[.]github/workflows/[A-Za-z0-9_.-]+[.]ya?ml@[0-9a-f]{40}$"
)
_SECRET_REF_RE = (
    "^(vault://|secret_ref://|secret://|secret_ref:)"
    "[A-Za-z0-9][A-Za-z0-9._/-]+$"
)
# Same 3..512-after-scheme contract as 2026110901 and the ORM model; PostgreSQL
# ARE rejects repeat counts >255, so length() replaces the former {2,511}.
_SECRET_REF_LENGTH_SQL = (
    "length(regexp_replace(installation_credential_ref, "
    "'^(vault://|secret_ref://|secret://|secret_ref:)', '')) BETWEEN 3 AND 512"
)


def upgrade() -> None:
    # No repo_id column: GitHub assigns it only after the reserved create call.
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("plugin_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False, server_default="github_app"),
        sa.Column("expected_full_name", sa.String(255), nullable=False),
        sa.Column("expected_private", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expected_default_branch", sa.String(255), nullable=False),
        sa.Column("installation_credential_ref", sa.String(512), nullable=False),
        sa.Column("trusted_workflow_ref", sa.String(1024), nullable=False),
        sa.Column("attempt_id", sa.String(80), nullable=False),
        sa.Column("create_in_progress", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(16), nullable=False, server_default="provisioning"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "company_id", "plugin_id", name="uq_plugin_source_reservation_company_plugin"
        ),
        sa.UniqueConstraint(
            "provider",
            "expected_full_name",
            name="uq_plugin_source_reservation_provider_full_name",
        ),
        sa.CheckConstraint(
            "provider = 'github_app'", name="ck_plugin_source_reservation_provider"
        ),
        sa.CheckConstraint(
            "expected_full_name ~ '" + _FULL_NAME_RE + "'",
            name="ck_plugin_source_reservation_full_name",
        ),
        sa.CheckConstraint(
            "expected_private = true", name="ck_plugin_source_reservation_private"
        ),
        sa.CheckConstraint(
            "expected_default_branch ~ '" + _DEFAULT_BRANCH_RE + "'",
            name="ck_plugin_source_reservation_default_branch",
        ),
        sa.CheckConstraint(
            "trusted_workflow_ref ~ '" + _WORKFLOW_RE + "'",
            name="ck_plugin_source_reservation_workflow_ref",
        ),
        sa.CheckConstraint(
            "installation_credential_ref ~ '" + _SECRET_REF_RE + "' AND " + _SECRET_REF_LENGTH_SQL,
            name="ck_plugin_source_reservation_credential_ref",
        ),
        sa.CheckConstraint(
            "status IN ('provisioning', 'ready', 'disabled')",
            name="ck_plugin_source_reservation_status",
        ),
        sa.CheckConstraint(
            "status = 'provisioning' OR create_in_progress = false",
            name="ck_plugin_source_reservation_completed_attempt",
        ),
    )
    op.create_index(
        "ix_plugin_source_reservations_company", _TABLE, ["company_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_source_reservations_company", table_name=_TABLE)
    op.drop_table(_TABLE)
