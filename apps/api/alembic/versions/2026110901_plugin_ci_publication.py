"""Trusted CI source metadata and additive plugin artifact provenance.

Revision ID: 2026110901
Revises: 2026110801

Only new table ``plugin_module_sources`` and nullable/additive columns/constraints
on ``plugin_artifacts``. No status/current/lifecycle backfill: historical rows
stay exactly as active/inactive as they were. Existing ``oci_digest``/``image_ref``
remain the frontend+bundle OCI pin; backend is a separate immutable pair.

The pending 2026110802 revision is deliberately not loaded or modified here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2026110901"
down_revision: str | Sequence[str] | None = "2026110801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_TABLE = "plugin_module_sources"
_ARTIFACT_TABLE = "plugin_artifacts"
_DIGEST_RE = "^sha256:[0-9a-f]{64}$"
_COMMIT_RE = "^[0-9a-f]{40}$"
_FULL_NAME_RE = "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
_WORKFLOW_RE = (
    "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    "[.]github/workflows/[A-Za-z0-9_.-]+[.]ya?ml@[0-9a-f]{40}$"
)
_SECRET_REF_RE = "^(vault://|secret_ref://|secret://|secret_ref:)[A-Za-z0-9][A-Za-z0-9._/-]+$"
# Same 3..512-after-scheme contract as ORM MODULE_SOURCE_SECRET_REF_LENGTH_SQL;
# PostgreSQL ARE rejects repeat counts >255, so length() enforces the upper bound
# instead of the former {2,511} (which made every source INSERT fail).
_SECRET_REF_LENGTH_SQL = (
    "length(regexp_replace(installation_credential_ref, "
    "'^(vault://|secret_ref://|secret://|secret_ref:)', '')) BETWEEN 3 AND 512"
)
_DEFAULT_BRANCH_RE = "^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$"

_ARTIFACT_CHECKS: tuple[str, ...] = (
    "ck_plugin_artifact_commit_sha",
    "ck_plugin_artifact_backend_digest",
    "ck_plugin_artifact_backend_image_ref",
    "ck_plugin_artifact_backend_pair",
    "ck_plugin_artifact_evidence_object",
)


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # Parent must exist before the additive artifact FK is created.
    op.create_table(
        _SOURCE_TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("plugin_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False, server_default="github_app"),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=False),
        sa.Column("installation_credential_ref", sa.String(512), nullable=False),
        sa.Column("trusted_workflow_ref", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="provisioning"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "company_id", "plugin_id", name="uq_plugin_module_source_company_plugin"
        ),
        sa.UniqueConstraint("provider", "repo_id", name="uq_plugin_module_source_provider_repo"),
        sa.CheckConstraint("provider = 'github_app'", name="ck_plugin_module_source_provider"),
        sa.CheckConstraint(
            "full_name ~ '" + _FULL_NAME_RE + "'",
            name="ck_plugin_module_source_full_name",
        ),
        sa.CheckConstraint(
            "default_branch ~ '" + _DEFAULT_BRANCH_RE + "'",
            name="ck_plugin_module_source_default_branch",
        ),
        sa.CheckConstraint(
            "trusted_workflow_ref ~ '" + _WORKFLOW_RE + "'",
            name="ck_plugin_module_source_workflow_ref",
        ),
        sa.CheckConstraint(
            "installation_credential_ref ~ '" + _SECRET_REF_RE + "' AND " + _SECRET_REF_LENGTH_SQL,
            name="ck_plugin_module_source_credential_ref",
        ),
        sa.CheckConstraint(
            "status IN ('provisioning', 'ready', 'disabled')",
            name="ck_plugin_module_source_status",
        ),
    )
    op.create_index("ix_plugin_module_sources_company", _SOURCE_TABLE, ["company_id"])
    op.create_index("ix_plugin_module_sources_plugin", _SOURCE_TABLE, ["plugin_id"])

    # Additive provenance only; no UPDATE and no change to publication state.
    op.add_column(_ARTIFACT_TABLE, sa.Column("source_id", sa.String(80), nullable=True))
    op.add_column(_ARTIFACT_TABLE, sa.Column("commit_sha", sa.String(40), nullable=True))
    op.add_column(_ARTIFACT_TABLE, sa.Column("backend_digest", sa.String(71), nullable=True))
    op.add_column(_ARTIFACT_TABLE, sa.Column("backend_image_ref", sa.String(2048), nullable=True))
    op.add_column(_ARTIFACT_TABLE, sa.Column("signer_identity", sa.String(1024), nullable=True))
    op.add_column(_ARTIFACT_TABLE, sa.Column("issuer", sa.String(512), nullable=True))
    op.add_column(_ARTIFACT_TABLE, sa.Column("run_url", sa.String(2048), nullable=True))
    op.add_column(
        _ARTIFACT_TABLE,
        sa.Column(
            "verification_evidence",
            _jsonb(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_foreign_key(
        "fk_plugin_artifact_source",
        _ARTIFACT_TABLE,
        _SOURCE_TABLE,
        ["source_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_plugin_artifact_commit_sha",
        _ARTIFACT_TABLE,
        "commit_sha IS NULL OR commit_sha ~ '" + _COMMIT_RE + "'",
    )
    op.create_check_constraint(
        "ck_plugin_artifact_backend_digest",
        _ARTIFACT_TABLE,
        "backend_digest IS NULL OR backend_digest ~ '" + _DIGEST_RE + "'",
    )
    op.create_check_constraint(
        "ck_plugin_artifact_backend_image_ref",
        _ARTIFACT_TABLE,
        "backend_image_ref IS NULL OR (backend_image_ref ~ '^.+@sha256:[0-9a-f]{64}$' "
        "AND strpos(backend_image_ref, ':latest') = 0)",
    )
    op.create_check_constraint(
        "ck_plugin_artifact_backend_pair",
        _ARTIFACT_TABLE,
        "(backend_digest IS NULL AND backend_image_ref IS NULL) OR "
        "(backend_digest IS NOT NULL AND backend_image_ref IS NOT NULL "
        "AND right(backend_image_ref, 72) = '@' || backend_digest)",
    )
    op.create_check_constraint(
        "ck_plugin_artifact_evidence_object",
        _ARTIFACT_TABLE,
        "jsonb_typeof(verification_evidence) = 'object'",
    )


def downgrade() -> None:
    for name in reversed(_ARTIFACT_CHECKS):
        op.drop_constraint(name, _ARTIFACT_TABLE, type_="check")
    op.drop_constraint("fk_plugin_artifact_source", _ARTIFACT_TABLE, type_="foreignkey")
    for column in (
        "verification_evidence",
        "run_url",
        "issuer",
        "signer_identity",
        "backend_image_ref",
        "backend_digest",
        "commit_sha",
        "source_id",
    ):
        op.drop_column(_ARTIFACT_TABLE, column)
    op.drop_index("ix_plugin_module_sources_plugin", table_name=_SOURCE_TABLE)
    op.drop_index("ix_plugin_module_sources_company", table_name=_SOURCE_TABLE)
    op.drop_table(_SOURCE_TABLE)
