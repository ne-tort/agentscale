"""companies.keycloak_sub + login_email; ai_provider_keys owner_scope."""

import sqlalchemy as sa

from alembic import op

revision = "2026082620"
down_revision = "2026082615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("keycloak_sub", sa.String(length=120), nullable=True))
    op.add_column("companies", sa.Column("login_email", sa.String(length=320), nullable=True))
    op.create_index("ix_companies_keycloak_sub", "companies", ["keycloak_sub"], unique=True)
    op.create_index("ix_companies_login_email", "companies", ["login_email"], unique=False)

    op.add_column(
        "ai_provider_keys",
        sa.Column("owner_scope", sa.String(length=32), nullable=False, server_default="platform"),
    )
    op.add_column("ai_provider_keys", sa.Column("owner_company_id", sa.String(length=40), nullable=True))
    op.create_foreign_key(
        "fk_ai_provider_keys_owner_company_id",
        "ai_provider_keys",
        "companies",
        ["owner_company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_ai_provider_keys_owner_company_id", "ai_provider_keys", ["owner_company_id"])
    op.create_index("ix_ai_provider_keys_owner_scope", "ai_provider_keys", ["owner_scope"])


def downgrade() -> None:
    op.drop_index("ix_ai_provider_keys_owner_scope", table_name="ai_provider_keys")
    op.drop_index("ix_ai_provider_keys_owner_company_id", table_name="ai_provider_keys")
    op.drop_constraint("fk_ai_provider_keys_owner_company_id", "ai_provider_keys", type_="foreignkey")
    op.drop_column("ai_provider_keys", "owner_company_id")
    op.drop_column("ai_provider_keys", "owner_scope")

    op.drop_index("ix_companies_login_email", table_name="companies")
    op.drop_index("ix_companies_keycloak_sub", table_name="companies")
    op.drop_column("companies", "login_email")
    op.drop_column("companies", "keycloak_sub")
