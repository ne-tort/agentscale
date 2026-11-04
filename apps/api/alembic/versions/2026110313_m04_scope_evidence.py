"""M04-02 accepted scope/RLS promotion evidence DDL (task MIG-DDL-M04EVID-01).

Revision ``2026110313`` (``down_revision`` ``2026110312``) creates exactly one table,
``m04_scope_promotion_evidence``, and two indexes on it. Authored only: the migration
verification freeze forbids executing Alembic, DDL, tests, builds or git checks in this
worktree.

Sources of truth
----------------
* ``src/prodavan/infrastructure/persistence/models/m04_scope_evidence.py`` — the ORM
  declaration this revision mirrors column for column, constraint for constraint and
  index for index (the model is the schema source of truth).
* ``src/prodavan/application/migration/evidence_ports.py`` — the read port whose
  ``m04_scope_promotion_evidence_missing`` blocker this table removes: it needs an
  accepted row per ``(tenant_id, scope_kind, scope_id)`` carrying the M04 scope digest,
  the RLS digest and the scope/RLS evidence identifiers.
* ``src/prodavan/application/migration/m04_scope/scope_migration.py`` —
  ``ScopeResolutionEvidence`` / ``ScopeParentEvidence`` / ``ScopeRlsEvidence`` /
  ``ScopeActorMembershipEvidence`` are the contracts whose derived, redacted proof the
  ``gate_evidence`` envelope carries.

Decisions recorded in this revision
-----------------------------------
* ``outcome`` admits ``('blocked', 'quarantined', 'ready')``. The older promotion
  tables are CHECK-limited to ``('blocked','quarantined')`` (blocker
  ``promotion_outcome_cannot_express_ready``); they are deliberately **not** altered,
  because rewriting an applied CHECK is a data migration. The new table is the only
  place where the true status is expressible in the column itself.
* The partition key is ``(tenant_id, scope_kind, scope_id)`` and ``revision_id`` is the
  append-only identity inside it (``uq_m04_scope_promotion_revision``): a repeated
  evidence key collides instead of appending a second accepted row.
* ``canonical_positive_writes`` is pinned to ``false`` by a check, like every other
  promotion-evidence table: this row is evidence, never a positive-writer switch.
* The single-column foreign key ``fk_m04_scope_promotion_registry`` proves the accepted
  scope-registry row exists. It does **not** prove tenant equality: ``scope_registry``
  exposes no ``(id, tenant_id)`` unique key and adding one would mutate a table owned by
  revision ``2026110301`` from outside this task's zone. Trusted writer + the
  tenant-scoped RLS policy on ``tenant_id`` are the enforcement today.

What this revision does not do
------------------------------
* **No RLS.** The table is registered in
  ``infrastructure/persistence/rls/policies.py`` (``owner_revision`` ``2026110313``,
  tenant scope on ``tenant_id``), so the coverage report names it and the next registry
  re-apply revision installs ``ENABLE/FORCE ROW LEVEL SECURITY`` plus the two canonical
  policies — the same pattern as ``2026110307`` -> ``2026110310`` -> ``2026110312``. A
  table-adding revision cannot install its own policy retroactively.
* No privileges, grants, backfill, data statement or writer: the append-only writer, the
  composition-root binding and the database-side append-only privilege gate are owned by
  other tasks (``evidence_ports.py`` and ``application/migration/**`` are not touched).
* No change to any existing table, including the three older promotion-evidence tables.

The upgrade fails closed before creating anything when the foreign-key target table or
its ``id`` key is missing, so a partial schema aborts with a named blocker instead of
leaving an unvalidatable constraint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110313"
down_revision = "2026110312"
branch_labels = None
depends_on = None


EVIDENCE_TABLE = "m04_scope_promotion_evidence"
SCOPE_REGISTRY_TABLE = "scope_registry"

PARTITION_INDEX = "ix_m04_scope_promotion_partition"
RECORDED_AT_INDEX = "ix_m04_scope_promotion_recorded_at"

#: Foreign-key target owned by revision 2026110301. Verified, never re-created.
REQUIRED_TARGET_TABLE = SCOPE_REGISTRY_TABLE
REQUIRED_TARGET_KEY_COLUMNS = ("id",)

BLOCKER_PREFIX = "MIG-DDL-M04EVID-01 blocker"


def _sql_array(columns: tuple[str, ...]) -> str:
    """Render a sorted ``text[]`` literal for an order-insensitive key check."""
    ordered = sorted(columns)
    return "ARRAY[" + ", ".join(f"'{column}'" for column in ordered) + "]"


def _require_scope_registry_key() -> None:
    """Fail closed unless the FK target table and its ``id`` key exist.

    Emitted as DDL so the check works in online and ``--sql`` offline modes and never
    inspects the database from Python.
    """
    missing_table = (
        f"{BLOCKER_PREFIX}: table {REQUIRED_TARGET_TABLE} is missing; revision "
        "2026110301 must run before the M04 scope evidence partition key"
    )
    missing_key = (
        f"{BLOCKER_PREFIX}: revision 2026110301 does not expose a primary or unique key "
        f"on {REQUIRED_TARGET_TABLE} ({', '.join(REQUIRED_TARGET_KEY_COLUMNS)}); "
        f"fk_m04_scope_promotion_registry cannot be validated and the M04 scope "
        "partition must stay blocked"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF to_regclass('{REQUIRED_TARGET_TABLE}') IS NULL THEN
        RAISE EXCEPTION '{missing_table}';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS c
        WHERE c.conrelid = '{REQUIRED_TARGET_TABLE}'::regclass
          AND c.contype IN ('p', 'u')
          AND (
              SELECT array_agg(a.attname::text ORDER BY a.attname::text)
              FROM unnest(c.conkey) AS k(attnum)
              JOIN pg_attribute AS a
                ON a.attrelid = c.conrelid AND a.attnum = k.attnum
          ) = {_sql_array(REQUIRED_TARGET_KEY_COLUMNS)}
    ) THEN
        RAISE EXCEPTION '{missing_key}';
    END IF;
END
$$
"""
    )


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    # Fail closed before the evidence table exists.
    _require_scope_registry_key()

    op.create_table(
        EVIDENCE_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("scope_registry_id", sa.String(40), nullable=False),
        sa.Column("revision_id", sa.String(80), nullable=False),
        sa.Column("previous_revision_id", sa.String(80), nullable=True),
        sa.Column(
            "outcome",
            sa.String(16),
            nullable=False,
            server_default="blocked",
        ),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("canonical_digest", sa.String(71), nullable=False),
        sa.Column(
            "canonical_schema_version",
            sa.String(32),
            nullable=False,
            server_default="scope-registry.v1",
        ),
        sa.Column("checkpoint_digest", sa.String(71), nullable=False),
        sa.Column("gate_evidence_digest", sa.String(71), nullable=False),
        sa.Column("rls_policy_digest", sa.String(71), nullable=False),
        sa.Column("rls_security_review_id", sa.String(128), nullable=False),
        sa.Column("rls_database_role", sa.String(120), nullable=False),
        sa.Column("gate_evidence", json_payload, nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("writer_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column(
            "canonical_positive_writes",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "scope_kind",
            "scope_id",
            "revision_id",
            name="uq_m04_scope_promotion_revision",
        ),
        sa.ForeignKeyConstraint(
            ["scope_registry_id"],
            ["scope_registry.id"],
            name="fk_m04_scope_promotion_registry",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "outcome IN ('blocked', 'quarantined', 'ready')",
            name="ck_m04_scope_promotion_outcome",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('tenant', 'cabinet', 'project')",
            name="ck_m04_scope_promotion_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id "
            "AND cabinet_id IS NULL AND project_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND cabinet_id IS NOT NULL AND scope_id = cabinet_id "
            "AND project_id IS NULL) "
            "OR (scope_kind = 'project' AND project_id IS NOT NULL AND scope_id = project_id "
            "AND cabinet_id IS NOT NULL)",
            name="ck_m04_scope_promotion_typed_shape",
        ),
        sa.CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04_scope_promotion_tenant_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(scope_id)) > 0", name="ck_m04_scope_promotion_scope_id_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(source_snapshot_id)) > 0",
            name="ck_m04_scope_promotion_source_snapshot_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(canonical_schema_version)) > 0",
            name="ck_m04_scope_promotion_canonical_schema_nonempty",
        ),
        sa.CheckConstraint(
            "source_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_source_sha256",
        ),
        sa.CheckConstraint(
            "canonical_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_canonical_sha256",
        ),
        sa.CheckConstraint(
            "checkpoint_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_checkpoint_sha256",
        ),
        sa.CheckConstraint(
            "gate_evidence_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_gate_sha256",
        ),
        sa.CheckConstraint(
            "rls_policy_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_rls_sha256",
        ),
        sa.CheckConstraint(
            "length(btrim(rls_security_review_id)) > 0 AND length(btrim(rls_database_role)) > 0",
            name="ck_m04_scope_promotion_rls_review_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(gate_evidence) = 'object'",
            name="ck_m04_scope_promotion_gate_evidence_object",
        ),
        sa.CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04_scope_promotion_correlation_nonempty",
        ),
        sa.CheckConstraint(
            "writer_generation >= 0", name="ck_m04_scope_promotion_writer_generation_nonnegative"
        ),
        sa.CheckConstraint(
            "canonical_positive_writes = false", name="ck_m04_scope_promotion_no_positive_writes"
        ),
        sa.CheckConstraint(
            "outcome <> 'ready' OR (actor_id IS NOT NULL AND length(btrim(actor_id)) > 0)",
            name="ck_m04_scope_promotion_ready_requires_actor",
        ),
        sa.CheckConstraint(
            "outcome = 'ready' OR (reason_code IS NOT NULL AND length(btrim(reason_code)) > 0)",
            name="ck_m04_scope_promotion_non_ready_requires_reason",
        ),
    )
    op.create_index(
        PARTITION_INDEX,
        EVIDENCE_TABLE,
        ["tenant_id", "scope_kind", "scope_id", "recorded_at"],
    )
    op.create_index(RECORDED_AT_INDEX, EVIDENCE_TABLE, ["recorded_at"])


def downgrade() -> None:
    """Remove only this revision's objects, newest index first.

    No policy or privilege is dropped: this revision never installed one. The table is
    dropped after its indexes, and the foreign key disappears with it.
    """
    op.drop_index(RECORDED_AT_INDEX, table_name=EVIDENCE_TABLE)
    op.drop_index(PARTITION_INDEX, table_name=EVIDENCE_TABLE)
    op.drop_table(EVIDENCE_TABLE)
