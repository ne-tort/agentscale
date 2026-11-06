"""M05 exact plugin-instance registry, reciprocal link and promotion evidence DDL.

Revision ``2026110303`` (MIG-DDL-M05-01). Authored only: the migration verification
freeze forbids executing Alembic, DDL, tests, builds or git checks in this worktree.

Sources of truth:

* ``src/prodavan/infrastructure/persistence/models/plugin_instance_registry.py``
  (registry row, reciprocal link row, M05 promotion evidence);
* ``docs/08-migration/m05-persistence-adapter-contract.md`` (identity envelope,
  typed scope shapes, staged reciprocal anchors);
* ``docs/08-migration/m04-m06-readiness-matrix.md`` section 2.3.

Decisions recorded in this revision:

* ``plugin_instance_id`` is an exact ``String(40)`` installation identity and is
  pinned to the physical key by ``ck_m05_instance_exact_public_id``. The physical
  exact key is ``uq_m05_plugin_instance_exact_identity``; the grant-compatible
  key (the same tuple starting with ``id``) is
  ``uq_m05_plugin_instance_grant_identity``.
* Definition and version ownership are proven by two composite foreign keys, not
  by a bare ``version_id``: ``fk_m05_instance_definition_tenant`` and
  ``fk_m05_instance_version_definition_artifact``. The latter also pins the
  instance ``artifact_digest`` to the immutable version digest, so an artifact or
  definition/version mismatch is a constraint violation rather than a silent
  mismatch.
* The ``scope_registry`` <-> ``m05_plugin_instances`` relation is cyclic. The ORM
  declares both directions with ``use_alter=True`` and ``DEFERRABLE INITIALLY
  DEFERRED``; this revision realizes that declaration by adding every cyclic
  constraint after both tables exist through ``op.create_foreign_key`` and by
  dropping every foreign key before ``drop_table`` on the way back.
* Foreign targets owned by the earlier revisions of this wave are verified, never
  re-created, and a missing or non-unique target aborts the upgrade with an
  explicit blocker message. The one exception is the M05-only ``scope_registry``
  anchor keys that the M05 model module appends to the shared ``scope_registry``
  metadata from outside its owner file: revision ``2026110301`` states as an
  explicit non-goal that ``uq_scope_registry_m05_instance_*`` and the deferred
  ``fk_m05_registry_instance_*`` constraints "belong to revision 2026110303,
  which creates m05_plugin_instances first". This revision therefore owns them
  end-to-end: it creates the anchor keys only when they are absent, adds the
  reciprocal foreign keys once both tables exist, and drops both again on
  downgrade before the M05 tables disappear.
* RLS/``FORCE RLS`` policies, trusted tenant context and role privileges belong to
  revision ``2026110307``. This revision only defines the staged tables, keys and
  checks and never enables a policy, a positive writer or a backfill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110303"
down_revision = "2026110302"
branch_labels = None
depends_on = None


INSTANCE_TABLE = "m05_plugin_instances"
LINK_TABLE = "m05_plugin_instance_registry_links"
PROMOTION_TABLE = "m05_plugin_instance_promotion_evidence"
SCOPE_REGISTRY_TABLE = "scope_registry"
DEFINITION_TABLE = "m04e_dynamic_app_definitions"
VERSION_TABLE = "m04e_dynamic_app_versions"

M05_TABLES = (INSTANCE_TABLE, LINK_TABLE, PROMOTION_TABLE)

BLOCKER_PREFIX = "MIG-DDL-M05-01 blocker"

# Unique keys this revision must find (but never silently re-create) on tables
# owned by revisions 2026110301 / 2026110302.
# (table, constraint name, columns, owner revision)
REQUIRED_TARGET_UNIQUE_KEYS = (
    (DEFINITION_TABLE, "uq_m04e_definition_id_tenant", ("id", "tenant_id"), "2026110302"),
    (
        VERSION_TABLE,
        "uq_m04e_version_id_definition_artifact",
        ("id", "definition_id", "artifact_digest"),
        "2026110302",
    ),
)

# M05-only anchor keys appended to the shared ``scope_registry`` metadata by the
# M05 model module. They are the target side of the instance -> registry FK.
# Created here only when absent; dropped on downgrade (no M05 object survives).
# (constraint name, columns)
SCOPE_REGISTRY_ANCHOR_KEYS = (
    ("uq_scope_registry_m05_instance_tenant", ("id", "plugin_instance_id", "tenant_id")),
    (
        "uq_scope_registry_m05_instance_cabinet",
        ("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
    ),
    (
        "uq_scope_registry_m05_instance_project",
        ("id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
    ),
)

# Cyclic / late-bound constraints, declared in the ORM with use_alter=True.
# (name, source table, source columns, referent table, referent columns)
DEFERRED_FOREIGN_KEYS = (
    {
        "name": "fk_m05_registry_instance_link_evidence",
        "source": LINK_TABLE,
        "local_columns": ("plugin_instance_id",),
        "referent": INSTANCE_TABLE,
        "remote_columns": ("id",),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
    {
        "name": "fk_m05_instance_registry_tenant_link",
        "source": INSTANCE_TABLE,
        "local_columns": ("scope_registry_id", "plugin_instance_id", "tenant_id"),
        "referent": SCOPE_REGISTRY_TABLE,
        "remote_columns": ("id", "plugin_instance_id", "tenant_id"),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
    {
        "name": "fk_m05_instance_registry_cabinet_link",
        "source": INSTANCE_TABLE,
        "local_columns": ("scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "referent": SCOPE_REGISTRY_TABLE,
        "remote_columns": ("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
    {
        "name": "fk_m05_instance_registry_project_link",
        "source": INSTANCE_TABLE,
        "local_columns": (
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
        ),
        "referent": SCOPE_REGISTRY_TABLE,
        "remote_columns": ("id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
    {
        "name": "fk_m05_registry_instance_tenant_link",
        "source": SCOPE_REGISTRY_TABLE,
        "local_columns": ("id", "plugin_instance_id", "tenant_id"),
        "referent": INSTANCE_TABLE,
        "remote_columns": ("scope_registry_id", "plugin_instance_id", "tenant_id"),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
    {
        "name": "fk_m05_registry_instance_cabinet_link",
        "source": SCOPE_REGISTRY_TABLE,
        "local_columns": ("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "referent": INSTANCE_TABLE,
        "remote_columns": ("scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
    {
        "name": "fk_m05_registry_instance_project_link",
        "source": SCOPE_REGISTRY_TABLE,
        "local_columns": ("id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
        "referent": INSTANCE_TABLE,
        "remote_columns": (
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
        ),
        "ondelete": "RESTRICT",
        "deferrable": True,
        "initially": "DEFERRED",
        "use_alter": True,
    },
)


def _sql_array(columns: tuple[str, ...]) -> str:
    """Render a sorted ``text[]`` literal for an order-insensitive key check."""
    ordered = sorted(columns)
    return "ARRAY[" + ", ".join(f"'{column}'" for column in ordered) + "]"


def _require_unique_key(
    table: str,
    name: str,
    columns: tuple[str, ...],
    owner: str,
) -> None:
    """Fail closed when a foreign-key target key is missing or not unique.

    The check is emitted as DDL so it works in both online and ``--sql`` offline
    modes and never inspects the database from Python.
    """
    column_list = ", ".join(columns)
    message = (
        f"{BLOCKER_PREFIX}: revision {owner} does not expose unique key {name} "
        f"on {table} ({column_list}); the M05 composite foreign key cannot be "
        "validated and the instance partition must stay blocked"
    )
    missing_table = (
        f"{BLOCKER_PREFIX}: table {table} is missing; revision {owner} must run before "
        "the M05 composite foreign keys"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF to_regclass('{table}') IS NULL THEN
        RAISE EXCEPTION '{missing_table}';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS c
        WHERE c.conrelid = '{table}'::regclass
          AND c.conname = '{name}'
          AND c.contype IN ('p', 'u')
          AND (
              SELECT array_agg(a.attname::text ORDER BY a.attname::text)
              FROM unnest(c.conkey) AS k(attnum)
              JOIN pg_attribute AS a
                ON a.attrelid = c.conrelid AND a.attnum = k.attnum
          ) = {_sql_array(columns)}
    ) THEN
        RAISE EXCEPTION '{message}';
    END IF;
END
$$
"""
    )


def _verify_foreign_target_keys() -> None:
    """Verify every M04/M04E foreign target before adding any M05 constraint."""
    for table, name, columns, owner in REQUIRED_TARGET_UNIQUE_KEYS:
        _require_unique_key(table, name, columns, owner)


def _ensure_scope_registry_anchor_keys() -> None:
    """Create the M05-only registry anchor keys owned by this revision.

    Revision ``2026110301`` deliberately does not emit them. ``IF NOT EXISTS``
    keeps the step idempotent if a later revision of that table decides to
    declare the same named keys.
    """
    for name, columns in SCOPE_REGISTRY_ANCHOR_KEYS:
        missing_table = (
            f"{BLOCKER_PREFIX}: table {SCOPE_REGISTRY_TABLE} is missing; revision "
            "2026110301 must run before the M05 reciprocal registry anchors"
        )
        op.execute(
            f"""
DO $$
BEGIN
    IF to_regclass('{SCOPE_REGISTRY_TABLE}') IS NULL THEN
        RAISE EXCEPTION '{missing_table}';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS c
        WHERE c.conrelid = '{SCOPE_REGISTRY_TABLE}'::regclass
          AND c.conname = '{name}'
    ) THEN
        ALTER TABLE {SCOPE_REGISTRY_TABLE}
            ADD CONSTRAINT {name} UNIQUE ({", ".join(columns)});
    END IF;
END
$$
"""
        )


def _drop_scope_registry_anchor_keys() -> None:
    """Release the M05 anchor keys once every M05 foreign key is gone.

    By then no M05 object depends on these keys, so a plain ``DROP CONSTRAINT``
    (without ``IF EXISTS``) is intentional: a missing key means the staged state
    was already mutated outside Alembic, and downgrade must fail loudly instead of
    leaving the registry in an unknown shape.
    """
    for name, _columns in SCOPE_REGISTRY_ANCHOR_KEYS:
        op.drop_constraint(name, SCOPE_REGISTRY_TABLE, type_="unique")


def _create_deferred_foreign_keys() -> None:
    """Add the cyclic registry/instance links once both sides exist."""
    for spec in DEFERRED_FOREIGN_KEYS:
        op.create_foreign_key(
            spec["name"],
            spec["source"],
            spec["referent"],
            list(spec["local_columns"]),
            list(spec["remote_columns"]),
            ondelete=spec["ondelete"],
            deferrable=spec["deferrable"],
            initially=spec["initially"],
        )


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    # Fail closed before any M05 object exists.
    _verify_foreign_target_keys()
    _ensure_scope_registry_anchor_keys()

    op.create_table(
        INSTANCE_TABLE,
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("plugin_instance_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("version_id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("scope_registry_id", sa.String(40), nullable=True),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=True),
        sa.Column("session_id", sa.String(160), nullable=True),
        sa.Column(
            "lifecycle",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'quarantined'"),
        ),
        sa.Column(
            "runtime_generation", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "writer_generation", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("source_kind", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("source_schema_version", sa.String(40), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("checkpoint", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plugin_instance_id",
            "definition_id",
            "version_id",
            "tenant_id",
            "scope_kind",
            "scope_id",
            name="uq_m05_plugin_instance_exact_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "definition_id",
            "version_id",
            "tenant_id",
            "scope_kind",
            "scope_id",
            name="uq_m05_plugin_instance_grant_identity",
        ),
        sa.UniqueConstraint(
            "source_kind",
            "source_id",
            "source_schema_version",
            name="uq_m05_instance_source_identity",
        ),
        sa.UniqueConstraint(
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            name="uq_m05_instance_registry_tenant_link",
        ),
        sa.UniqueConstraint(
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            name="uq_m05_instance_registry_cabinet_link",
        ),
        sa.UniqueConstraint(
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
            name="uq_m05_instance_registry_project_link",
        ),
        sa.UniqueConstraint(
            "plugin_instance_id", "tenant_id", name="uq_m05_plugin_instance_tenant_anchor"
        ),
        sa.UniqueConstraint(
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            name="uq_m05_plugin_instance_cabinet_anchor",
        ),
        sa.UniqueConstraint(
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
            name="uq_m05_plugin_instance_project_anchor",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_m05_instance_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "definition_id", "artifact_digest"],
            [
                "m04e_dynamic_app_versions.id",
                "m04e_dynamic_app_versions.definition_id",
                "m04e_dynamic_app_versions.artifact_digest",
            ],
            name="fk_m05_instance_version_definition_artifact",
            ondelete="RESTRICT",
        ),
        # The registry links are deliberately absent here: the registry <-> instance
        # relation is cyclic and is realized by _create_deferred_foreign_keys().
        sa.CheckConstraint("scope_kind IN ('plugin_instance')", name="ck_m05_instance_scope_kind"),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'published', 'disabled', 'revoked', 'quarantined')",
            name="ck_m05_instance_lifecycle",
        ),
        sa.CheckConstraint("plugin_instance_id = id", name="ck_m05_instance_exact_public_id"),
        sa.CheckConstraint(
            "plugin_instance_id <> '' AND plugin_instance_id = btrim(plugin_instance_id) "
            "AND plugin_instance_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_m05_instance_id_canonical",
        ),
        sa.CheckConstraint(
            "tenant_id <> '' AND tenant_id = btrim(tenant_id) "
            "AND tenant_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_m05_instance_tenant_canonical_id",
        ),
        sa.CheckConstraint(
            "scope_id <> '' AND scope_id = btrim(scope_id) "
            "AND scope_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_m05_instance_scope_canonical_id",
        ),
        sa.CheckConstraint(
            "(cabinet_id IS NULL OR cabinet_id <> '') "
            "AND (project_id IS NULL OR project_id <> '')",
            name="ck_m05_instance_parent_scope_nonempty",
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m05_instance_artifact_sha256",
        ),
        sa.CheckConstraint(
            "runtime_generation >= 0 AND writer_generation >= 0",
            name="ck_m05_instance_generations_nonnegative",
        ),
        sa.CheckConstraint(
            "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id "
            "AND (project_id IS NULL OR cabinet_id IS NOT NULL)",
            name="ck_m05_instance_typed_scope_shape",
        ),
        sa.CheckConstraint(
            "lifecycle <> 'published' OR "
            "(subject_id IS NOT NULL AND subject_id <> '' "
            "AND session_id IS NOT NULL AND session_id <> '' AND scope_registry_id IS NOT NULL)",
            name="ck_m05_instance_published_evidence_required",
        ),
        sa.CheckConstraint(
            "length(btrim(source_kind)) > 0", name="ck_m05_instance_source_kind_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(source_id)) > 0", name="ck_m05_instance_source_id_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(source_schema_version)) > 0",
            name="ck_m05_instance_source_schema_nonempty",
        ),
        sa.CheckConstraint(
            "source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m05_instance_source_sha256"
        ),
    )
    op.create_index(
        "ix_m05_instance_tenant_scope_lifecycle",
        INSTANCE_TABLE,
        ["tenant_id", "scope_kind", "scope_id", "lifecycle"],
    )

    op.create_table(
        LINK_TABLE,
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("plugin_instance_id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column(
            "owner_state",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'quarantined'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plugin_instance_id", name="uq_m05_registry_link_instance"),
        # fk_m05_registry_instance_link_evidence is added after m05_plugin_instances
        # exists (see _create_deferred_foreign_keys).
        sa.CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_m05_registry_link_state",
        ),
        sa.CheckConstraint(
            "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id",
            name="ck_m05_registry_link_typed_shape",
        ),
    )
    op.create_index(
        "ix_m05_registry_link_tenant_state", LINK_TABLE, ["tenant_id", "owner_state"]
    )

    op.create_table(
        PROMOTION_TABLE,
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("partition_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default=sa.text("'blocked'")),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_schema_version", sa.String(32), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("target_digest", sa.String(71), nullable=False),
        sa.Column("composite_key_evidence", json_payload, nullable=False),
        sa.Column("reciprocal_fk_evidence", json_payload, nullable=False),
        sa.Column("quarantine_counts", json_payload, nullable=False),
        sa.Column("lifecycle_distribution", json_payload, nullable=False),
        sa.Column("dependency_digests", json_payload, nullable=False),
        sa.Column("checkpoint", sa.String(128), nullable=True),
        sa.Column("gate_evidence", json_payload, nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "quarantined_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("archived_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "canonical_positive_writes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("writer_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "fencing_generation", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("decision_owner", sa.String(200), nullable=True),
        sa.Column("reviewer", sa.String(200), nullable=True),
        sa.Column("previous_revision_id", sa.String(80), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "rollback_gate",
            sa.Text(),
            nullable=False,
            server_default=sa.text(
                "'legacy owner remains authoritative; preserve evidence and repair forward'"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "partition_id",
            "revision_id",
            name="uq_m05_instance_promotion_revision",
        ),
        sa.CheckConstraint(
            "outcome IN ('blocked', 'quarantined')", name="ck_m05_instance_promotion_outcome"
        ),
        sa.CheckConstraint(
            "canonical_positive_writes = false", name="ck_m05_instance_no_positive_writes"
        ),
        sa.CheckConstraint(
            "source_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m05_instance_promotion_source_sha256",
        ),
        sa.CheckConstraint(
            "target_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m05_instance_promotion_target_sha256",
        ),
        sa.CheckConstraint(
            "accepted_count >= 0 AND quarantined_count >= 0 AND archived_count >= 0",
            name="ck_m05_instance_promotion_counts_nonnegative",
        ),
    )
    op.create_index(
        "ix_m05_instance_promotion_partition_time",
        PROMOTION_TABLE,
        ["tenant_id", "definition_id", "partition_id", "recorded_at"],
    )

    # Both sides of the cyclic relation now exist: add the named, deferred links.
    _create_deferred_foreign_keys()


def downgrade() -> None:
    # Foreign keys first, and in reverse creation order: the tables cannot be dropped
    # while the reciprocal links exist. Then release the registry anchor keys this
    # revision added to the still-existing scope_registry table.
    for spec in reversed(DEFERRED_FOREIGN_KEYS):
        op.drop_constraint(spec["name"], spec["source"], type_="foreignkey")

    _drop_scope_registry_anchor_keys()

    op.drop_index("ix_m05_instance_promotion_partition_time", table_name=PROMOTION_TABLE)
    op.drop_table(PROMOTION_TABLE)
    op.drop_index("ix_m05_registry_link_tenant_state", table_name=LINK_TABLE)
    op.drop_table(LINK_TABLE)
    op.drop_index("ix_m05_instance_tenant_scope_lifecycle", table_name=INSTANCE_TABLE)
    op.drop_table(INSTANCE_TABLE)
