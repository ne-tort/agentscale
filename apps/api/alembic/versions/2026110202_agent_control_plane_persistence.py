"""Add durable agent run, approval, event, command and queue-intent tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110202"
down_revision = "2026110201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("session_id", sa.String(40), nullable=True),
        sa.Column("requested_by_employee_id", sa.String(40), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("input_payload", json_payload, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_payload", json_payload, nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_agent_runs_project_idempotency"),
    )
    op.create_index("ix_agent_runs_project_status_created", "agent_runs", ["project_id", "status", "created_at"])
    op.create_index("ix_agent_runs_session_created", "agent_runs", ["session_id", "created_at"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("tool_call_id", sa.String(128), nullable=False),
        sa.Column("approval_kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("requested_payload", json_payload, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_by_employee_id", sa.String(40), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_approval_requests_run_tool_call"),
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_index("ix_approval_requests_status_expires", "approval_requests", ["status", "expires_at"])

    op.create_table(
        "agent_control_plane_run_scopes",
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("session_id", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_acp_run_scope_tenant_project", "agent_control_plane_run_scopes", ["tenant_id", "project_id", "run_id"])

    op.create_table(
        "agent_control_plane_aggregates",
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("aggregate_type", "aggregate_id"),
    )

    op.create_table(
        "agent_control_plane_events",
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(160), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("producer", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=True),
        sa.Column("session_id", sa.String(40), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("causation_id", sa.String(36), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload", json_payload, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("aggregate_type", "aggregate_id", "sequence", name="uq_acp_events_aggregate_sequence"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_acp_events_tenant_idempotency"),
    )
    op.create_index("ix_acp_events_run_sequence", "agent_control_plane_events", ["run_id", "sequence"])
    op.execute(
        """
        CREATE FUNCTION prevent_acp_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'agent_control_plane_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_acp_events_append_only "
        "BEFORE UPDATE OR DELETE ON agent_control_plane_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_acp_event_mutation()"
    )

    op.create_table(
        "agent_control_plane_outbox_events",
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(200), nullable=False),
        sa.Column("payload", json_payload, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["event_id"], ["agent_control_plane_events.event_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_acp_outbox_idempotency"),
        sa.UniqueConstraint("tenant_id", "aggregate_type", "aggregate_id", "aggregate_version", name="uq_acp_outbox_aggregate_version"),
    )
    op.create_index("ix_acp_outbox_pending", "agent_control_plane_outbox_events", ["created_at", "event_id"])

    op.create_table(
        "agent_control_plane_enqueue_intents",
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("queue_name", sa.String(200), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("deduplication_key", sa.String(512), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_token", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["event_id"], ["agent_control_plane_outbox_events.event_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "queue_name"),
        sa.UniqueConstraint("queue_name", "deduplication_key", name="uq_acp_queue_deduplication"),
    )
    op.create_index("ix_acp_enqueue_due", "agent_control_plane_enqueue_intents", ["queue_name", "state", "available_at", "lease_until"])

    op.create_table(
        "agent_control_plane_commands",
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(64), nullable=True),
        sa.Column("command_idempotency_key", sa.String(512), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("result_payload", json_payload, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope_key"),
    )
    op.create_index("ix_acp_commands_tenant_project", "agent_control_plane_commands", ["tenant_id", "project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_acp_commands_tenant_project", table_name="agent_control_plane_commands")
    op.drop_table("agent_control_plane_commands")
    op.drop_index("ix_acp_enqueue_due", table_name="agent_control_plane_enqueue_intents")
    op.drop_table("agent_control_plane_enqueue_intents")
    op.drop_index("ix_acp_outbox_pending", table_name="agent_control_plane_outbox_events")
    op.drop_table("agent_control_plane_outbox_events")
    op.drop_index("ix_acp_events_run_sequence", table_name="agent_control_plane_events")
    op.execute("DROP TRIGGER trg_acp_events_append_only ON agent_control_plane_events")
    op.execute("DROP FUNCTION prevent_acp_event_mutation()")
    op.drop_table("agent_control_plane_events")
    op.drop_table("agent_control_plane_aggregates")
    op.drop_index("ix_acp_run_scope_tenant_project", table_name="agent_control_plane_run_scopes")
    op.drop_table("agent_control_plane_run_scopes")
    op.drop_index("ix_approval_requests_status_expires", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_project_status_created", table_name="agent_runs")
    op.drop_table("agent_runs")
