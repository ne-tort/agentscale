"""Content storage schema — assets, aliases, bindings, ACL."""

import sqlalchemy as sa
from alembic import op

revision = "2026082706"
down_revision = "2026082705"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_assets",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("owner_scope", sa.String(length=32), server_default="company", nullable=False),
        sa.Column(
            "owner_company_id",
            sa.String(length=40),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visibility", sa.String(length=32), server_default="company", nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=260), nullable=True),
        sa.Column("tags", sa.dialects.postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column(
            "created_by",
            sa.String(length=40),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_content_assets_owner_company_id", "content_assets", ["owner_company_id"])

    op.create_table(
        "content_blob_versions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=40),
            sa.ForeignKey("content_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("size", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column("object_metadata", sa.dialects.postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("asset_id", "version", name="uq_content_blob_asset_version"),
    )
    op.create_index("ix_content_blob_versions_asset_id", "content_blob_versions", ["asset_id"])

    op.create_table(
        "content_aliases",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("slug", sa.String(length=128), nullable=False, unique=True),
        sa.Column("owner_scope", sa.String(length=32), server_default="company", nullable=False),
        sa.Column(
            "owner_company_id",
            sa.String(length=40),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visibility", sa.String(length=32), server_default="company", nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_content_aliases_owner_company_id", "content_aliases", ["owner_company_id"])

    op.create_table(
        "content_alias_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "alias_id",
            sa.String(length=40),
            sa.ForeignKey("content_aliases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(length=40),
            sa.ForeignKey("content_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blob_version_id",
            sa.String(length=40),
            sa.ForeignKey("content_blob_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "bound_by",
            sa.String(length=40),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_content_alias_bindings_alias_id", "content_alias_bindings", ["alias_id"])
    op.create_index(
        "ix_content_alias_bindings_alias_current",
        "content_alias_bindings",
        ["alias_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    op.create_table(
        "content_acl_entries",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("resource_kind", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.String(length=40), nullable=False),
        sa.Column("principal_kind", sa.String(length=32), nullable=False),
        sa.Column("principal_id", sa.String(length=40), nullable=False),
        sa.Column("permission", sa.String(length=16), server_default="read", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "resource_kind",
            "resource_id",
            "principal_kind",
            "principal_id",
            "permission",
            name="uq_content_acl_grant",
        ),
    )
    op.create_index(
        "ix_content_acl_resource",
        "content_acl_entries",
        ["resource_kind", "resource_id"],
    )

    op.create_table(
        "content_asset_links",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=40),
            sa.ForeignKey("content_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("link_kind", sa.String(length=64), nullable=False),
        sa.Column("link_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("link_kind", "link_id", name="uq_content_asset_link_target"),
    )
    op.create_index("ix_content_asset_links_asset_id", "content_asset_links", ["asset_id"])

    op.add_column(
        "project_attachments",
        sa.Column(
            "content_asset_id",
            sa.String(length=40),
            sa.ForeignKey("content_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("project_attachments", "content_asset_id")
    op.drop_index("ix_content_asset_links_asset_id", table_name="content_asset_links")
    op.drop_table("content_asset_links")
    op.drop_index("ix_content_acl_resource", table_name="content_acl_entries")
    op.drop_table("content_acl_entries")
    op.drop_index("ix_content_alias_bindings_alias_current", table_name="content_alias_bindings")
    op.drop_index("ix_content_alias_bindings_alias_id", table_name="content_alias_bindings")
    op.drop_table("content_alias_bindings")
    op.drop_index("ix_content_aliases_owner_company_id", table_name="content_aliases")
    op.drop_table("content_aliases")
    op.drop_index("ix_content_blob_versions_asset_id", table_name="content_blob_versions")
    op.drop_table("content_blob_versions")
    op.drop_index("ix_content_assets_owner_company_id", table_name="content_assets")
    op.drop_table("content_assets")
