"""External Chat API + outgoing webhooks: app keys + subscriptions + deliveries.

api_app_keys — machine credentials for /api/v1/external (auth-as employee).
webhook_subscriptions — outgoing webhooks (ChatGPT-style) with HMAC secret_ref.
webhook_deliveries — transactional outbox with lease + retry.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "2026101501"
down_revision = "2026101402"


def upgrade() -> None:
    op.create_table(
        "api_app_keys",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column(
            "company_id",
            sa.String(40),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cabinet_id",
            sa.String(40),
            sa.ForeignKey("cabinet_instances.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "employee_id",
            sa.String(40),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("scopes", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_api_app_keys_company", "api_app_keys", ["company_id"])
    op.create_index("ix_api_app_keys_prefix", "api_app_keys", ["token_prefix"])

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(40),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(40),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("secret_ref", sa.String(512), nullable=False),
        sa.Column("events", JSONB, nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fail_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_webhook_subs_company", "webhook_subscriptions", ["company_id"])
    op.create_index("ix_webhook_subs_project", "webhook_subscriptions", ["project_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.String(40),
            sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_del_pending", "webhook_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index("ix_webhook_del_sub", "webhook_deliveries", ["subscription_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_subscriptions")
    op.drop_table("api_app_keys")
