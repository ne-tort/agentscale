"""Add the durable provider-attempt journal for Agent Run execution.

Revision ``2026110311`` follows ``2026110310`` and owns one table,
``agent_run_provider_attempts``.

Background
----------
``application/agent_runs/attempts.py`` already defines the journal contract —
``ProviderAttempt``, ``AttemptState``, the transition matrix and the redacted
metadata rules — and refuses to operate without durable storage. The wave-1
schema had nowhere to put those rows: ``agent_runs`` keeps a single ``attempt``
counter, and ``agent_control_plane_events`` is an aggregate event log keyed by
``(aggregate_type, aggregate_id, sequence)``. Multiple provider attempts per run
therefore had no home.

Scope
-----
* one row per attempt, ``attempt_id`` primary key, ``idempotency_key`` unique so
  a retried worker cannot fork history;
* positive ``generation``/``attempt``/``sequence``;
* ``finished_at >= started_at`` when finished;
* ``state`` limited to the closed ``AttemptState`` vocabulary;
* digests only — no prompt, payload, credential or raw provider request.

The transition matrix deliberately stays in the application layer. A CHECK
cannot express "``unknown`` may only be left by reconciliation" without copying
the matrix, and two copies would drift.

Downgrade drops the table and its indexes only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110311"
down_revision = "2026110310"
branch_labels = None
depends_on = None

_ATTEMPT_STATES = (
    "started",
    "known_succeeded",
    "known_failed",
    "known_retryable",
    "unknown",
    "reconciled_succeeded",
    "reconciled_failed",
    "abandoned",
    "discarded_stale",
)


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())
    state_list = ", ".join(f"'{state}'" for state in _ATTEMPT_STATES)

    op.create_table(
        "agent_run_provider_attempts",
        sa.Column("attempt_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("request_digest", sa.String(80), nullable=False),
        sa.Column("lease_token_digest", sa.String(80), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(40), nullable=True),
        sa.Column("model_digest", sa.String(80), nullable=True),
        sa.Column("prompt_digest", sa.String(80), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("provider_request_id", sa.String(160), nullable=True),
        sa.Column(
            "metadata", json_payload, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("metadata_dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("generation >= 1", name="ck_agent_run_attempt_generation_positive"),
        sa.CheckConstraint("attempt >= 1", name="ck_agent_run_attempt_number_positive"),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_run_attempt_sequence_positive"),
        sa.CheckConstraint(
            "metadata_dropped >= 0",
            name="ck_agent_run_attempt_metadata_dropped_nonnegative",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_agent_run_attempt_finish_after_start",
        ),
        sa.CheckConstraint(
            f"state IN ({state_list})",
            name="ck_agent_run_attempt_state_vocabulary",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_run_attempt_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_agent_run_provider_attempts"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_agent_run_attempt_idempotency"
        ),
    )
    op.create_index(
        "ix_agent_run_attempts_run_generation",
        "agent_run_provider_attempts",
        ["run_id", "generation"],
    )
    op.create_index(
        "ix_agent_run_attempts_state_deadline",
        "agent_run_provider_attempts",
        ["state", "deadline_at"],
    )
    op.create_index(
        "ix_agent_run_attempts_tenant_started",
        "agent_run_provider_attempts",
        ["tenant_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_attempts_tenant_started", table_name="agent_run_provider_attempts"
    )
    op.drop_index(
        "ix_agent_run_attempts_state_deadline", table_name="agent_run_provider_attempts"
    )
    op.drop_index(
        "ix_agent_run_attempts_run_generation", table_name="agent_run_provider_attempts"
    )
    op.drop_table("agent_run_provider_attempts")
