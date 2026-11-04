"""Add the durable per-run lease fence for the Agent Run execution worker.

Revision ``2026110309`` follows ``2026110308`` and owns exactly one table,
``agent_run_leases``, plus its constraints and indexes.

Why a separate table instead of columns on ``agent_runs``
---------------------------------------------------------
``agent_runs`` is owned by ``2026110202`` and describes the run itself.  The
lease is *execution* state: it changes on every claim, heartbeat and release,
while the run row changes only on domain transitions.  Keeping the fence in its
own row lets the claim query take ``FOR UPDATE SKIP LOCKED`` on the lease row
without serialising every reader of ``agent_runs``, and it keeps the
denormalised ``tenant_id``/``project_id`` available for an index-only claim
predicate and for the tenant policy installed by ``2026110307``.

Invariants encoded in the schema
--------------------------------
* ``run_id`` is the primary key: a run can hold at most one live lease.
* The lease triple is all-or-nothing
  (``ck_agent_run_lease_token_pair``, ``ck_agent_run_lease_until_pair``), so a
  half-cleared row can never be read as a live lease.
* ``lease_generation`` and ``attempt`` are non-negative / positive and are never
  decremented; fencing only moves forward.
* ``retry_at`` is separate from ``lease_until``: a retry is a delay, not a lease.

The table is created without data and without RLS: policies are installed by
``2026110307``'s registry, which is extended separately for this table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026110309"
down_revision = "2026110308"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_leases",
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_token IS NULL)",
            name="ck_agent_run_lease_token_pair",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_agent_run_lease_until_pair",
        ),
        sa.CheckConstraint(
            "lease_generation >= 0",
            name="ck_agent_run_lease_generation_nonnegative",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_agent_run_lease_attempt_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_run_lease_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_agent_run_leases"),
    )
    op.create_index(
        "ix_agent_run_leases_claimable",
        "agent_run_leases",
        ["retry_at", "lease_until"],
    )
    op.create_index(
        "ix_agent_run_leases_tenant_project",
        "agent_run_leases",
        ["tenant_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_leases_tenant_project", table_name="agent_run_leases")
    op.drop_index("ix_agent_run_leases_claimable", table_name="agent_run_leases")
    op.drop_table("agent_run_leases")
