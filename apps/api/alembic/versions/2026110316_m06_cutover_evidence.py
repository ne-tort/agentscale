"""M06 partition cutover ledger + live legacy source tail (task MIG-DDL-CUTOVER-01).

Revision ``2026110316`` (``down_revision`` ``2026110315``) creates exactly two
tables, one index, one guard function and one trigger, and then re-applies the
canonical RLS registry so the two new tables are policied in the same step that
creates them.

Sources of truth
================
* ``src/prodavan/application/migration/m06_cutover/ports.py`` —
  ``CUTOVER_REQUIRED_SCHEMA`` is the reviewed specification: table names, column
  names, column types, nullability, primary keys, unique keys, checks and indexes
  are copied from it verbatim. ``cutover_schema_ddl()`` is the same specification
  rendered as ``CREATE TABLE``; ``CUTOVER_SCHEMA_BLOCKERS`` / ``CUTOVER_LEDGER_TABLE``
  / ``CUTOVER_SOURCE_TAIL_TABLE`` are the constants the port and the repository read.
* ``src/prodavan/infrastructure/persistence/models/m06_cutover_evidence.py`` — the
  ORM declaration this revision mirrors column for column, constraint for
  constraint and index for index (the model is the schema source of truth). The
  check-constraint *names* live in the model; the specification names only the
  predicates.
* ``src/prodavan/application/migration/m06_cutover/ports.py`` codecs
  ``state_payload`` / ``evidence_payload`` — the two ``*_payload`` columns hold their
  canonical JSON text, which is why they are ``TEXT`` and not ``JSONB``: the stored
  bytes are the ones whose digest the lineage chains, and a JSONB round-trip
  re-orders and re-escapes them.

Why these two tables
====================
``SqlCutoverRepository`` fails closed with ``CutoverRepositoryBlocked`` until both
tables exist. None of the five registered evidence tables can host them
(``CUTOVER_SCHEMA_BLOCKERS``):

* ``cutover_state_row_absent`` — nothing stores the current ``PartitionCutover`` of a
  ``(tenant, app_definition)`` partition. ``m06_partition_cutover_evidence`` does,
  as one appended row per transition whose ``state_payload`` is the snapshot.
* ``cutover_positive_write_ledger_absent`` — ``m06_canonical_grant_promotion_evidence``
  pins ``canonical_positive_writes = false`` (``ck_m06_grant_no_positive_writes``), so a
  promoted partition cannot be recorded there. Here
  ``ck_m06_cutover_positive_writes_only_when_promoted`` admits ``true`` only together
  with ``resulting_state = 'promoted'``.
* ``cutover_source_tail_absent`` — nothing exposes the live legacy source change
  sequence. ``m06_partition_cutover_source_tail`` is that head row; a missing row
  makes a final-delta transition refuse.
* ``cutover_state_cas_absent`` —
  ``uq_m06_cutover_partition_revision`` = ``(tenant_id, app_definition_id, revision)``
  is the atomic state CAS, and ``uq_m06_cutover_command`` =
  ``(tenant_id, app_definition_id, action, correlation_id)`` is the command
  idempotency key. A lost race surfaces as ``IntegrityError`` (the repository turns it
  into ``False``); a replayed correlation id collides at the storage layer.

Decisions recorded in this revision
===================================
* **Column types are the specification's, exactly.** ``TEXT`` for identifiers,
  digests and canonical JSON (no ``VARCHAR(n)``: a length limit would truncate a
  correct append), ``BIGINT`` for ``revision`` / ``writer_epoch`` / ``change_sequence``,
  ``BOOLEAN`` for ``canonical_positive_writes`` and ``TIMESTAMPTZ``
  (``DateTime(timezone=True)``) for ``occurred_at`` / ``updated_at``.
* **No server default anywhere.** A default on ``revision`` would fabricate a CAS
  position; a default on ``canonical_positive_writes`` would claim or deny a positive
  write; a default on ``occurred_at`` would let a transition date itself.
* **The tail table is mutable by design** (``append_only=False`` in the
  specification): it is the CAS head the legacy writer advances in place, so it gets
  no append-only trigger and no monotonicity trigger. A regression is caught by the
  cutover CAS, which compares the live sequence with the frozen one.
* **The ledger is append-only twice**: the ORM ``before_update``/``before_delete``
  guard in the model and ``m06_cutover_ledger_append_only`` /
  ``trg_m06_cutover_ledger_append_only`` here, which raises ``23514`` on UPDATE and
  DELETE for direct-SQL writers regardless of role. The exception message carries the
  reason code only — never a row value, digest or payload fragment. The per-role
  ``GRANT``/``REVOKE`` inventory is a separate privilege review.
* **No foreign key to any table.** A ``BLOCKED`` transition must be recordable for a
  partition whose upstream evidence could not be verified, and the ledger is lineage,
  not a child row. Tenant equality is enforced by the tenant-scoped RLS policy and the
  trusted writer, exactly like ``m04e_definition_version_mappings``.
* **RLS.** This revision is itself the registry re-apply for its own tables: it
  creates both tables and then walks ``policies.CANONICAL_TABLE_POLICIES`` with
  ``upgrade_guard_sql``. Creating a tenant-scoped table in one revision and relying on
  a later installing pass is the defect that hit ``agent_run_leases``,
  ``agent_run_provider_attempts`` and ``m04_scope_promotion_evidence``; the reviewer
  gate is
  ``tests/migration/rls/test_policy_install_order.py::test_every_registered_table_is_policied_after_its_creating_revision``.

What this revision does not do
==============================
* No privilege/grant statement, no backfill, no data statement, no writer, and no
  change to any earlier table or revision.
* No positive canonical write path by itself. ``SqlCutoverRepository`` regains its
  storage, but ``ports.py``'s remaining blockers (notably
  ``cutover_promotion_marker_absent``) are recorded in the model's
  ``M06_CUTOVER_EVIDENCE_DEFERRED`` and owned by the ports task.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110316"
down_revision = "2026110315"
branch_labels = None
depends_on = None

LEDGER_TABLE = "m06_partition_cutover_evidence"
SOURCE_TAIL_TABLE = "m06_partition_cutover_source_tail"

#: Creation order; ``downgrade`` reverses it.
CUTOVER_TABLES = (LEDGER_TABLE, SOURCE_TAIL_TABLE)

#: The one index ``CUTOVER_REQUIRED_SCHEMA`` names for the ledger.
LEDGER_PARTITION_INDEX = "ix_m06_cutover_partition_revision"
CUTOVER_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (LEDGER_PARTITION_INDEX, LEDGER_TABLE, ("tenant_id", "app_definition_id", "revision")),
)

APPEND_ONLY_FUNCTION = "m06_cutover_ledger_append_only"
APPEND_ONLY_TRIGGER = "trg_m06_cutover_ledger_append_only"
APPEND_ONLY_REASON = "m06_cutover_ledger_row_immutable"

#: Policies this revision is accountable for withdrawing on downgrade: exactly the
#: tables it creates. Policies owned by earlier installers stay untouched — dropping
#: them would unprotect tables this revision installed nothing for.
_POLICIES_ADDED_HERE = frozenset({revision})

_SHA256 = "'^sha256:[0-9a-f]{64}$'"

logger = logging.getLogger("alembic.runtime.migration")

_CREATE_APPEND_ONLY_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {APPEND_ONLY_FUNCTION}() RETURNS trigger AS $$
BEGIN
    -- Redacted by construction: the exception names the reason code only, never a
    -- row value, digest, correlation id or payload fragment.
    RAISE EXCEPTION '{APPEND_ONLY_REASON}' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql
"""


def _create_ledger_table() -> None:
    op.create_table(
        LEDGER_TABLE,
        # The lineage record id, which is also the cutover command id.
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("app_definition_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("command_digest", sa.Text(), nullable=False),
        sa.Column("record_digest", sa.Text(), nullable=False),
        sa.Column("prior_record_id", sa.Text(), nullable=True),
        sa.Column("prior_record_digest", sa.Text(), nullable=True),
        sa.Column("resulting_state", sa.Text(), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_fencing_token", sa.Text(), nullable=True),
        sa.Column("canonical_positive_writes", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        # Canonical JSON of PartitionCutover, exactly as ``state_payload`` renders it.
        sa.Column("state_payload", sa.Text(), nullable=False),
        # Canonical JSON of PromotionEvidence, exactly as ``evidence_payload`` renders it.
        sa.Column("record_payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # The state CAS: a lost race on revision n+1 collides here.
        sa.UniqueConstraint(
            "tenant_id",
            "app_definition_id",
            "revision",
            name="uq_m06_cutover_partition_revision",
        ),
        # The command idempotency key.
        sa.UniqueConstraint(
            "tenant_id",
            "app_definition_id",
            "action",
            "correlation_id",
            name="uq_m06_cutover_command",
        ),
        sa.CheckConstraint(
            f"record_digest ~ {_SHA256}", name="ck_m06_cutover_record_sha256"
        ),
        sa.CheckConstraint(
            f"command_digest ~ {_SHA256}", name="ck_m06_cutover_command_sha256"
        ),
        sa.CheckConstraint(
            "revision >= 0 AND writer_epoch >= 0",
            name="ck_m06_cutover_revision_epoch_nonnegative",
        ),
        # The only place the cutover schema admits a positive canonical write.
        sa.CheckConstraint(
            "canonical_positive_writes = false OR resulting_state = 'promoted'",
            name="ck_m06_cutover_positive_writes_only_when_promoted",
        ),
        # The successor's CAS compares prior_record_digest with the stored lineage
        # head, so a half-written lineage link must be impossible.
        sa.CheckConstraint(
            "prior_record_id IS NULL OR prior_record_digest IS NOT NULL",
            name="ck_m06_cutover_prior_record_pair",
        ),
    )


def _create_source_tail_table() -> None:
    op.create_table(
        SOURCE_TAIL_TABLE,
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("app_definition_id", sa.Text(), nullable=False),
        sa.Column("change_sequence", sa.BigInteger(), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "app_definition_id"),
        sa.CheckConstraint(
            "change_sequence >= 0 AND writer_epoch >= 0",
            name="ck_m06_cutover_tail_sequence_nonnegative",
        ),
    )


def upgrade() -> None:
    # 1. The ledger (one appended row per partition transition) and the live tail.
    _create_ledger_table()
    _create_source_tail_table()

    # 2. The partition/revision index the reviewed specification names.
    for index_name, table_name, columns in CUTOVER_INDEXES:
        op.create_index(index_name, table_name, list(columns))

    # 3. Append-only guard, so a direct-SQL writer cannot rewrite a recorded
    #    transition. The tail table is the CAS head and stays mutable on purpose.
    op.execute(_CREATE_APPEND_ONLY_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {APPEND_ONLY_TRIGGER} "
        f"BEFORE UPDATE OR DELETE ON {LEDGER_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}()"
    )

    # 4. RLS for the tables this revision just created. This is the registry re-apply
    #    for owner_revision 2026110316: a table-adding revision that leaves its own
    #    policy to "the next pass" is the defect the install-order gate exists for.
    installed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
        installed += 1
    logger.info(
        "rls %s: re-applied %d canonical table policies after creating %d cutover tables",
        revision,
        installed,
        len(CUTOVER_TABLES),
    )


def downgrade() -> None:
    """Remove only this revision's objects, in reverse creation order.

    Policies first (they belong to the tables about to disappear), then the trigger
    and its function, then the index, then the tables. Policies owned by earlier
    installers (``2026110307``, ``2026110310``, ``2026110312``, ``2026110314``,
    ``2026110315``) are never touched: withdrawing them would silently unprotect
    tables this revision never installed anything for.
    """

    removed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        if spec.owner_revision not in _POLICIES_ADDED_HERE:
            continue
        op.execute(policies.downgrade_guard_sql(spec))
        removed += 1

    op.execute(f"DROP TRIGGER IF EXISTS {APPEND_ONLY_TRIGGER} ON {LEDGER_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {APPEND_ONLY_FUNCTION}()")

    for index_name, table_name, _columns in reversed(CUTOVER_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    for table_name in reversed(CUTOVER_TABLES):
        op.drop_table(table_name)

    logger.info(
        "rls %s: withdrew %d canonical table policies and dropped %d cutover tables",
        revision,
        removed,
        len(CUTOVER_TABLES),
    )
