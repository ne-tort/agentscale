"""Create the four ``dynamic_app_*`` tables the ORM has always declared.

Why this revision exists
========================

``models/dynamic_apps.py`` declares ``dynamic_app_definitions``,
``dynamic_app_versions``, ``dynamic_app_grants`` and ``dynamic_app_instances``, the
``dynamic_apps`` API router serves them, and ``Base.metadata`` registers them -- but no
revision in the chain created them.  They were assumed to predate the migration, so on a
database built from revisions the ORM and the schema disagreed.

That disagreement was invisible until it was not.  ``scripts/migrate.sh`` (the deployment
initContainer) finishes with ``alembic check``, which reports drift as a failure: on an
empty database the four tables are "missing", the check exits non-zero, the initContainer
CrashLoops, and the API pod never starts.  On a database that already had the tables the
check passed, which is why nothing caught it.

What this revision is *not*
==========================

It is not a second source of truth.  The columns, keys and checks below are transcribed
from the models one-for-one; if the models change, this revision does not follow -- a
later revision does, exactly as with every other table in this chain.

It is also idempotent by design.  A deployment that already carries these tables (a
retained production database, or a developer database built by an earlier bootstrap) must
upgrade cleanly rather than fail on "relation already exists".  Each table, index and
late constraint is created only when absent, and ``downgrade`` removes only what this
revision actually created -- tracked explicitly, because "drop the four tables" would
destroy real rows on a database that had them before this revision ran.

The foreign-key cycle
=====================

``dynamic_app_definitions.current_version_id`` points at ``dynamic_app_versions`` while
``dynamic_app_versions.app_definition_id`` points back at ``dynamic_app_definitions``.
SQLAlchemy cannot order that, and neither can a single ``create_table`` call: the pair is
created first without the definition-side constraint, which is then added with
``use_alter=True`` and ``DEFERRABLE INITIALLY DEFERRED`` so an application can insert a
definition and its first version in one transaction.

Revision ID: 2026110320_dynamic_apps_baseline
Revises: 2026110319
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2026110320"
down_revision: str | Sequence[str] | None = "2026110319"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEFINITION_TABLE = "dynamic_app_definitions"
VERSION_TABLE = "dynamic_app_versions"
GRANT_TABLE = "dynamic_app_grants"
INSTANCE_TABLE = "dynamic_app_instances"

#: Late cycle-breaking constraint: definitions.current_version_id -> versions.
CURRENT_VERSION_FK = "fk_dynamic_app_definition_current_version"

_LIFECYCLE_CHECK = "('draft', 'published', 'disabled', 'revoked')"
_SCOPE_CHECK = "('tenant', 'cabinet', 'project')"

#: The four tables this revision owns, in drop order (children before parents).
_OWNED_TABLES = (INSTANCE_TABLE, GRANT_TABLE, VERSION_TABLE, DEFINITION_TABLE)


def _has_table(bind: sa.engine.Connection, name: str) -> bool:
    """Fresh catalog read: ``inspect`` caches, and each check follows a DDL statement."""

    return sa.inspect(bind).has_table(name)


def _create_index(bind: sa.engine.Connection, name: str, table: str, columns: list[str]) -> None:
    if name in {index["name"] for index in sa.inspect(bind).get_indexes(table)}:
        return
    op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()

    # -- definitions (no definition-side FK yet: the cycle is broken after versions) --
    if not _has_table(bind, DEFINITION_TABLE):
        op.create_table(
            DEFINITION_TABLE,
            sa.Column("id", sa.String(80), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.String(80), nullable=False),
            sa.Column("app_id", sa.String(160), nullable=False),
            sa.Column("display_name", sa.String(260), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("scope_kind", sa.String(32), nullable=False),
            sa.Column("scope_id", sa.String(80), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("current_version_id", sa.String(80), nullable=True),
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
                "tenant_id", "app_id", name="uq_dynamic_app_definition_tenant_app"
            ),
            sa.CheckConstraint(
                f"status IN {_LIFECYCLE_CHECK}", name="ck_dynamic_app_definition_status"
            ),
            sa.CheckConstraint(
                "status <> 'published' OR current_version_id IS NOT NULL",
                name="ck_dynamic_app_definition_published_version",
            ),
            sa.CheckConstraint(
                f"scope_kind IN {_SCOPE_CHECK}",
                name="ck_dynamic_app_definition_scope_kind",
            ),
        )
    _create_index(
        bind, "ix_dynamic_app_definitions_scope", DEFINITION_TABLE, ["tenant_id", "scope_kind", "scope_id"]
    )
    _create_index(
        bind, "ix_dynamic_app_definitions_status", DEFINITION_TABLE, ["tenant_id", "status"]
    )

    # -- versions --
    if not _has_table(bind, VERSION_TABLE):
        op.create_table(
            VERSION_TABLE,
            sa.Column("id", sa.String(80), primary_key=True, nullable=False),
            sa.Column("app_definition_id", sa.String(80), nullable=False),
            sa.Column("manifest_version", sa.String(128), nullable=False),
            sa.Column("artifact_digest", sa.String(71), nullable=False),
            sa.Column("artifact_uri", sa.String(2_048), nullable=True),
            sa.Column(
                "manifest",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="{}",
            ),
            sa.Column(
                "requested_capabilities",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("rollback_from_version_id", sa.String(80), nullable=True),
            sa.Column("rollback_to_version_id", sa.String(80), nullable=True),
            sa.Column("rollback_reason", sa.Text(), nullable=True),
            sa.Column("rollback_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["app_definition_id"],
                [f"{DEFINITION_TABLE}.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "app_definition_id",
                "manifest_version",
                name="uq_dynamic_app_version_manifest_version",
            ),
            sa.UniqueConstraint(
                "app_definition_id",
                "artifact_digest",
                name="uq_dynamic_app_version_artifact_digest",
            ),
            # Target of the late definition->version pair and of the composite FKs below.
            sa.UniqueConstraint(
                "id", "app_definition_id", name="uq_dynamic_app_version_id_definition"
            ),
            sa.CheckConstraint(
                f"status IN {_LIFECYCLE_CHECK}", name="ck_dynamic_app_version_status"
            ),
            sa.CheckConstraint(
                "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
                name="ck_dynamic_app_version_digest_sha256",
            ),
            sa.CheckConstraint(
                "manifest_version <> ''",
                name="ck_dynamic_app_version_manifest_version_nonempty",
            ),
            sa.CheckConstraint(
                "manifest->>'protocol' = '1.0'",
                name="ck_dynamic_app_version_manifest_protocol",
            ),
            sa.CheckConstraint(
                "manifest ? 'appId' AND manifest ? 'version' AND manifest ? 'displayName' "
                "AND manifest ? 'entrypoints' AND manifest ? 'requestedCapabilities'",
                name="ck_dynamic_app_version_manifest_v1_fields",
            ),
            sa.ForeignKeyConstraint(
                ["rollback_from_version_id", "app_definition_id"],
                [f"{VERSION_TABLE}.id", f"{VERSION_TABLE}.app_definition_id"],
                name="fk_dynamic_app_version_rollback_from",
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["rollback_to_version_id", "app_definition_id"],
                [f"{VERSION_TABLE}.id", f"{VERSION_TABLE}.app_definition_id"],
                name="fk_dynamic_app_version_rollback_to",
                ondelete="RESTRICT",
            ),
            sa.CheckConstraint(
                "(rollback_from_version_id IS NULL) = (rollback_to_version_id IS NULL)",
                name="ck_dynamic_app_version_rollback_pair",
            ),
        )
    _create_index(
        bind,
        "ix_dynamic_app_versions_app_status",
        VERSION_TABLE,
        ["app_definition_id", "status"],
    )

    # -- late cycle-breaking constraint --
    #
    # Two cases, and the second is the one a retained database hits:
    #
    # * the constraint is absent (this revision or ``create_all`` made the tables here):
    #   create it;
    # * the constraint already exists but is not deferred (a database bootstrapped from
    #   the models before this revision existed): replace it with the canonical form.
    #   Without the replacement ``alembic check`` reports drift forever on a database
    #   whose schema is otherwise exactly what the models describe, which would leave the
    #   deployment's drift gate red with no way to fix it but a manual ``ALTER``.
    constraint_defs = {
        fk["name"]: fk
        for fk in sa.inspect(bind).get_foreign_keys(DEFINITION_TABLE)
    }
    existing_fk = constraint_defs.get(CURRENT_VERSION_FK)
    needs_replacement = existing_fk is not None and not (
        existing_fk.get("options", {}).get("deferrable")
        or "DEFERRABLE" in str(existing_fk).upper()
    )
    if needs_replacement:
        # The column is nullable and the pair is a cycle, so no row can violate the
        # constraint while it is briefly absent inside this transaction.
        op.drop_constraint(CURRENT_VERSION_FK, DEFINITION_TABLE, type_="foreignkey")
    if existing_fk is None or needs_replacement:
        op.create_foreign_key(
            CURRENT_VERSION_FK,
            DEFINITION_TABLE,
            VERSION_TABLE,
            ["current_version_id", "id"],
            ["id", "app_definition_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        )

    # -- grants --
    if not _has_table(bind, GRANT_TABLE):
        op.create_table(
            GRANT_TABLE,
            sa.Column("id", sa.String(80), primary_key=True, nullable=False),
            sa.Column("app_definition_id", sa.String(80), nullable=False),
            sa.Column("app_version_id", sa.String(80), nullable=False),
            sa.Column("scope_kind", sa.String(32), nullable=False),
            sa.Column("scope_id", sa.String(80), nullable=False),
            sa.Column(
                "grants",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="[]",
            ),
            sa.Column(
                "issued_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["app_definition_id"],
                [f"{DEFINITION_TABLE}.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "app_version_id",
                "scope_kind",
                "scope_id",
                name="uq_dynamic_app_grant_version_scope",
            ),
            sa.ForeignKeyConstraint(
                ["app_version_id", "app_definition_id"],
                [f"{VERSION_TABLE}.id", f"{VERSION_TABLE}.app_definition_id"],
                name="fk_dynamic_app_grant_version_definition",
                ondelete="CASCADE",
            ),
            sa.CheckConstraint(
                f"scope_kind IN {_SCOPE_CHECK}", name="ck_dynamic_app_grant_scope_kind"
            ),
            sa.CheckConstraint(
                "expires_at IS NULL OR expires_at > issued_at",
                name="ck_dynamic_app_grant_expiry_after_issue",
            ),
        )
    _create_index(bind, "ix_dynamic_app_grants_scope", GRANT_TABLE, ["scope_kind", "scope_id"])

    # -- instances --
    if not _has_table(bind, INSTANCE_TABLE):
        op.create_table(
            INSTANCE_TABLE,
            sa.Column("id", sa.String(80), primary_key=True, nullable=False),
            sa.Column("app_definition_id", sa.String(80), nullable=False),
            sa.Column("app_version_id", sa.String(80), nullable=False),
            sa.Column("scope_kind", sa.String(32), nullable=False),
            sa.Column("scope_id", sa.String(80), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("runtime_ref", sa.String(200), nullable=True),
            sa.Column("rollback_from_version_id", sa.String(80), nullable=True),
            sa.Column("rollback_to_version_id", sa.String(80), nullable=True),
            sa.Column("rollback_reason", sa.Text(), nullable=True),
            sa.Column("rollback_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(
                ["app_definition_id"],
                [f"{DEFINITION_TABLE}.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "app_definition_id",
                "scope_kind",
                "scope_id",
                name="uq_dynamic_app_instance_scope",
            ),
            sa.ForeignKeyConstraint(
                ["app_version_id", "app_definition_id"],
                [f"{VERSION_TABLE}.id", f"{VERSION_TABLE}.app_definition_id"],
                name="fk_dynamic_app_instance_version_definition",
                ondelete="RESTRICT",
            ),
            sa.CheckConstraint(
                f"scope_kind IN {_SCOPE_CHECK}", name="ck_dynamic_app_instance_scope_kind"
            ),
            sa.CheckConstraint(
                f"status IN {_LIFECYCLE_CHECK}", name="ck_dynamic_app_instance_status"
            ),
        )
    _create_index(
        bind,
        "ix_dynamic_app_instances_scope_status",
        INSTANCE_TABLE,
        ["scope_kind", "scope_id", "status"],
    )



def downgrade() -> None:
    """Drop the four tables -- but never silently discard real rows.

    A database that already carried these tables (a retained production one) must not lose
    its rows to ``alembic downgrade``.  DDL is unconditional here on purpose: tracking
    "did I create it" in a module-level set looked safer but would make ``downgrade`` a
    lie, because Alembic runs it in a fresh process where such a set is always empty.
    The honest rule is the other way round -- drop, but refuse while there is data.

    So: if every owned table is empty, the downgrade really removes them, which is what
    makes ``downgrade base && upgrade head`` in CI exercise this revision instead of
    no-opping.  If any table still holds rows the downgrade raises, names the tables and
    their counts, and changes nothing -- an operator who means to discard tenant data
    should say so explicitly rather than discover it in a schema rollback.
    """

    bind = op.get_bind()
    present = [name for name in _OWNED_TABLES if _has_table(bind, name)]
    if not present:
        return

    populated: dict[str, int] = {}
    for name in present:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one()
        if count:
            populated[name] = int(count)

    if populated:
        detail = ", ".join(f"{name}={count}" for name, count in populated.items())
        raise RuntimeError(
            "refusing to downgrade 2026110320: these dynamic_app tables still hold rows "
            f"({detail}). Downgrading would destroy them. Archive or delete the data "
            "deliberately first, or restore the previous revision by other means."
        )

    # The cycle-breaking constraint goes first: it is the only object pointing from
    # definitions at versions, so neither table can be dropped while it exists.
    definition_fks = (
        {fk["name"] for fk in sa.inspect(bind).get_foreign_keys(DEFINITION_TABLE)}
        if _has_table(bind, DEFINITION_TABLE)
        else set()
    )
    if CURRENT_VERSION_FK in definition_fks:
        op.drop_constraint(CURRENT_VERSION_FK, DEFINITION_TABLE, type_="foreignkey")

    for name in _OWNED_TABLES:
        if _has_table(bind, name):
            op.drop_table(name)
