"""M04E definition/version backfill ledger DDL (task MIG-DDL-M04E-LEDGER-01).

Revision ``2026110315`` (``down_revision`` ``2026110314``) creates exactly four
tables, nine indexes, four triggers and one guard function, and then re-applies the
canonical RLS registry so the four new tables are policied in the same step that
creates them.

Authored only: the migration verification freeze forbids executing Alembic, DDL,
tests, builds or git checks in this worktree.

Sources of truth
----------------
* ``src/prodavan/infrastructure/persistence/models/m04e_ledger.py`` — the ORM
  declaration this revision mirrors column for column, constraint for constraint and
  index for index (the model is the schema source of truth).
* ``src/prodavan/application/migration/m04e_ports.py`` — ``EVIDENCE_STORE_SCHEMA_GAPS``
  (the seven reviewed reasons ``SqlM04EEvidenceStore`` refuses) and
  ``EVIDENCE_STORE_REQUIRED_DDL`` (the four objects, with their column lists). Those
  constants are the specification for this revision, not an inspiration: every column
  they name exists below and every gap they state is answered by a named object.
* ``src/prodavan/application/migration/m04e_definition_version/backfill.py`` —
  ``BackfillContext`` / ``BackfillCheckpoint`` / ``BackfillRecord`` /
  ``EvidenceStatus`` / ``BackfillOutcome`` are the contracts whose fields these tables
  store. In particular ``EvidenceStatus`` admits ``accepted`` — the state the two
  oldest promotion-evidence tables cannot express.

Why four tables and not one envelope
------------------------------------
Packing the ledger and the cursor into the redacted ``gate_evidence`` JSONB column of
``m04e_definition_version_promotion_evidence`` was implemented once and rejected as
invented schema: a plain-SQL cursor query and an exact per-source lookup are
impossible over a redacted envelope, and ``find_observation`` could not replay a
changed-digest conflict exactly. These four tables mirror the two accepted precedents
for redacted per-source evidence (``scope_owner_mappings`` /
``scope_owner_quarantine`` / ``scope_owner_audit`` and
``dynamic_app_capability_grant_mappings`` / ``..._quarantine`` / ``..._audit``).

Decisions recorded in this revision
-----------------------------------
* **``m04e_definition_version_mappings``** is the per-source first-mapping ledger.
  ``uq_m04e_mapping_source`` = ``(tenant_id, definition_id, source_kind, source_id,
  source_schema_version)`` makes a second mapping row for an already-mapped source
  identity **impossible at the storage layer**, instead of a writer convention. Gap 1
  of ``EVIDENCE_STORE_SCHEMA_GAPS`` is answered here.
* **``m04e_definition_version_mappings.status``** admits ``('accepted', 'replay',
  'quarantined', 'conflict', 'blocked')`` — the closed ``BackfillOutcome`` vocabulary —
  instead of the ``('blocked','quarantined')`` limit of ``ck_m04e_promotion_outcome``.
  Gap 6 (``m04e_promotion_accepted_outcome_unrepresentable``) is answered by *this*
  table; the old table is deliberately not altered, because rewriting an applied CHECK
  is a data migration. ``status`` has no server default: a default of ``'accepted'``
  would let a careless insert claim acceptance, a default of ``'blocked'`` would
  fabricate a refusal.
* **``m04e_definition_version_quarantine``** carries the
  ``observed_source_digest`` / ``observed_artifact_digest`` pair (gap 2). Exactly one of
  the two is present (``ck_m04e_quarantine_single_observation``) and
  ``uq_m04e_quarantine_observation`` includes both, so ``find_observation`` reads one
  exact row. The constraint is declared ``NULLS NOT DISTINCT``: PostgreSQL treats NULLs
  as distinct in a plain unique constraint, so without it a second identical *artifact*
  observation (where the source digest is NULL) would not collide and the "exact"
  replay would silently stop being exact. This needs PostgreSQL 15+; the repository
  pins ``postgres:16.15``.
* **``m04e_definition_version_checkpoints``** is the durable cursor (gap 3):
  ``uq_m04e_checkpoint_partition`` = ``(tenant_id, definition_id, phase)`` is the single
  head row, ``uq_m04e_checkpoint_token`` makes the CAS token globally unique,
  ``ck_m04e_checkpoint_sequence_nonnegative`` and
  ``ck_m04e_checkpoint_complete_only_version`` bound the state space, and
  ``trg_m04e_checkpoint_monotonic`` refuses a sequence regression, a phase regression,
  a lineage rebind and an un-completion for **direct SQL** as well as ORM writers — the
  CAS itself is ``UPDATE ... WHERE tenant_id = ? AND definition_id = ? AND phase = ?
  AND sequence = :observed_sequence AND checkpoint_token = :expected_token``; a
  zero-row update is a stale token and must fail closed.
* **``m04e_definition_version_audit``** is the append-only redacted per-page stream
  (gap 4): ``event_kind``, ``reason_code``, ``correlation_id``, ``checkpoint``,
  ``phase``, ``sequence``, ``actor``, ``transaction_id`` and five numeric counters.
  ``ck_m04e_audit_counters_balance`` forces a committed page to account for every
  classification it wrote and a blocked page to write none.
* **No foreign key to ``m04e_dynamic_app_definitions``.** A blocked page must be able to
  record evidence for a partition whose canonical definition could not be resolved
  (``m04e_candidate_proof_definition_absent``); a hard FK would make that truthful row
  impossible to write, so the ledger could only ever store the happy path. Tenant
  equality of ``definition_id`` is enforced by the tenant-scoped RLS policy and the
  trusted migration writer — the same trade-off ``scope_owner_quarantine`` records for
  ``resolved_tenant_id``.
* **Append-only for the three immutable ledgers** is enforced by
  ``m04e_ledger_append_only`` / ``trg_m04e_ledger_append_only_<table>``, which raises
  ``23514`` on UPDATE **and** DELETE regardless of role, in addition to the ORM
  ``before_update``/``before_delete`` guards in the model. The per-role
  ``GRANT``/``REVOKE`` inventory stays a separate privilege review.
* **RLS.** This revision is itself the registry re-apply for its own tables: it creates
  the tables and then walks ``policies.CANONICAL_TABLE_POLICIES`` with
  ``upgrade_guard_sql``, so ``m04e_definition_version_*`` is policied at the moment it
  exists. Creating a tenant-scoped table in one revision and relying on a later
  installing pass is exactly the defect that hit ``agent_run_leases``,
  ``agent_run_provider_attempts`` and ``m04_scope_promotion_evidence``; the reviewer
  gate for that rule is
  ``tests/migration/rls/test_policy_install_order.py::test_every_registered_table_is_policied_after_its_creating_revision``.
* **No payload, manifest body, description, token or secret** reaches a column: only
  identifiers, reason codes, sha256 digests, JSONB arrays/objects of derived proof
  digests, cursor tokens, actor/transaction/correlation ids and counters.

What this revision does not do
------------------------------
* No privilege/grant statement, no backfill, no data statement, no writer.
* No change to any existing table, including the three M04E tables of revision
  ``2026110302``: their two-state ``outcome`` CHECK and their four NOT NULL sha256 gate
  proofs are gaps 5–7 of ``EVIDENCE_STORE_SCHEMA_GAPS`` and stay untouched here.
* No positive canonical write path. ``m04e_ports.py`` and the composition root are owned
  by other tasks; this revision removes four missing-table blockers, it does not make a
  page commit.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110315"
down_revision = "2026110314"
branch_labels = None
depends_on = None

MAPPING_TABLE = "m04e_definition_version_mappings"
QUARANTINE_TABLE = "m04e_definition_version_quarantine"
CHECKPOINT_TABLE = "m04e_definition_version_checkpoints"
AUDIT_TABLE = "m04e_definition_version_audit"

#: Creation order; ``downgrade`` reverses it.
LEDGER_TABLES = (MAPPING_TABLE, QUARANTINE_TABLE, CHECKPOINT_TABLE, AUDIT_TABLE)

MAPPING_PARTITION_INDEX = "ix_m04e_mapping_partition"
MAPPING_CURSOR_INDEX = "ix_m04e_mapping_cursor"
MAPPING_SOURCE_INDEX = "ix_m04e_mapping_source_lookup"

QUARANTINE_PARTITION_INDEX = "ix_m04e_quarantine_partition"
QUARANTINE_SOURCE_INDEX = "ix_m04e_quarantine_source_lookup"

CHECKPOINT_SNAPSHOT_INDEX = "ix_m04e_checkpoint_snapshot"
CHECKPOINT_COMPLETION_INDEX = "ix_m04e_checkpoint_completion"

AUDIT_PARTITION_INDEX = "ix_m04e_audit_partition_time"
AUDIT_CHECKPOINT_INDEX = "ix_m04e_audit_checkpoint"
AUDIT_CORRELATION_INDEX = "ix_m04e_audit_correlation"

#: Index name -> (table, columns), in creation order and reversed on downgrade.
LEDGER_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (MAPPING_PARTITION_INDEX, MAPPING_TABLE, ("tenant_id", "definition_id", "recorded_at")),
    (MAPPING_CURSOR_INDEX, MAPPING_TABLE, ("tenant_id", "definition_id", "phase", "source_id")),
    (MAPPING_SOURCE_INDEX, MAPPING_TABLE, ("source_kind", "source_id", "source_schema_version")),
    (QUARANTINE_PARTITION_INDEX, QUARANTINE_TABLE, ("tenant_id", "definition_id", "recorded_at")),
    (QUARANTINE_SOURCE_INDEX, QUARANTINE_TABLE, ("source_kind", "source_id", "source_schema_version")),
    (CHECKPOINT_SNAPSHOT_INDEX, CHECKPOINT_TABLE, ("source_snapshot_id",)),
    (CHECKPOINT_COMPLETION_INDEX, CHECKPOINT_TABLE, ("tenant_id", "definition_id", "phase_complete")),
    (AUDIT_PARTITION_INDEX, AUDIT_TABLE, ("tenant_id", "definition_id", "recorded_at")),
    (AUDIT_CHECKPOINT_INDEX, AUDIT_TABLE, ("tenant_id", "definition_id", "checkpoint")),
    (AUDIT_CORRELATION_INDEX, AUDIT_TABLE, ("correlation_id",)),
)

APPEND_ONLY_FUNCTION = "m04e_ledger_append_only"
APPEND_ONLY_REASON = "m04e_ledger_row_immutable"

#: The three ledgers that are never rewritten or deleted. The checkpoint table is
#: mutable by design (it is the CAS head) and gets its own monotonicity trigger.
APPEND_ONLY_TABLES: tuple[str, ...] = (MAPPING_TABLE, QUARANTINE_TABLE, AUDIT_TABLE)
APPEND_ONLY_TRIGGER = "trg_m04e_ledger_append_only_{table}"

CHECKPOINT_GUARD_FUNCTION = "m04e_checkpoint_advance_guard"
CHECKPOINT_GUARD_TRIGGER = "trg_m04e_checkpoint_monotonic"
CHECKPOINT_REGRESSION_REASON = "m04e_checkpoint_sequence_regression"
CHECKPOINT_PHASE_REWIND_REASON = "m04e_checkpoint_phase_rewind"
CHECKPOINT_REBIND_REASON = "m04e_checkpoint_lineage_rebind"
CHECKPOINT_REOPEN_REASON = "m04e_checkpoint_reopened"
CHECKPOINT_TOKEN_REUSE_REASON = "m04e_checkpoint_token_reused"

#: Policies this revision is accountable for withdrawing on downgrade: exactly the
#: four tables it creates. Policies owned by earlier installers stay untouched —
#: ``2026110307``, ``2026110310``, ``2026110312`` and ``2026110314`` own theirs.
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

_CREATE_CHECKPOINT_GUARD_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {CHECKPOINT_GUARD_FUNCTION}() RETURNS trigger AS $$
BEGIN
    -- Which cursor this is must never change: rebinding a head row would attach a
    -- durable cursor to another partition or another snapshot lineage.
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
        OR NEW.definition_id IS DISTINCT FROM OLD.definition_id
        OR NEW.source_snapshot_id IS DISTINCT FROM OLD.source_snapshot_id
        OR NEW.source_schema_version IS DISTINCT FROM OLD.source_schema_version
        OR NEW.source_snapshot_digest IS DISTINCT FROM OLD.source_snapshot_digest
        OR NEW.canonical_schema_version IS DISTINCT FROM OLD.canonical_schema_version
        OR NEW.writer_generation IS DISTINCT FROM OLD.writer_generation
    THEN
        RAISE EXCEPTION '{CHECKPOINT_REBIND_REASON}' USING ERRCODE = '23514';
    END IF;

    -- A cursor only moves forward. A regression would re-read or skip a page, so it
    -- is refused even when the offending writer is direct SQL.
    IF NEW.sequence < OLD.sequence THEN
        RAISE EXCEPTION '{CHECKPOINT_REGRESSION_REASON}' USING ERRCODE = '23514';
    END IF;

    -- The backfill walks definition -> version and never back again.
    IF OLD.phase = 'version' AND NEW.phase <> 'version' THEN
        RAISE EXCEPTION '{CHECKPOINT_PHASE_REWIND_REASON}' USING ERRCODE = '23514';
    END IF;

    -- Only an exhausted version stream completes, and a completed stream is final.
    IF OLD.phase_complete AND NOT NEW.phase_complete THEN
        RAISE EXCEPTION '{CHECKPOINT_REOPEN_REASON}' USING ERRCODE = '23514';
    END IF;
    IF NEW.phase_complete AND NEW.phase <> 'version' THEN
        RAISE EXCEPTION '{CHECKPOINT_REOPEN_REASON}' USING ERRCODE = '23514';
    END IF;

    -- The CAS token must be renewed on every advance, so a token cannot be
    -- presented twice against two different cursors.
    IF NEW.checkpoint_token = OLD.checkpoint_token THEN
        RAISE EXCEPTION '{CHECKPOINT_TOKEN_REUSE_REASON}' USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def _json_payload() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _create_mappings_table() -> None:
    op.create_table(
        MAPPING_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("source_schema_version", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        # No server default: the writer states the outcome (see module docstring).
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("canonical_digest", sa.String(71), nullable=True),
        sa.Column("canonical_identity", _json_payload(), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("proof_evidence", _json_payload(), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=True),
        sa.Column("verified_artifact_digest", sa.String(71), nullable=True),
        sa.Column("checkpoint", sa.String(160), nullable=False),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # Gap 1: the exact source key. A second mapping for an already-mapped source
        # identity collides here instead of being appended by a buggy writer.
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "source_kind",
            "source_id",
            "source_schema_version",
            name="uq_m04e_mapping_source",
        ),
        sa.CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_mapping_tenant_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(definition_id)) > 0", name="ck_m04e_mapping_definition_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(source_kind)) > 0 AND length(btrim(source_id)) > 0 "
            "AND length(btrim(source_schema_version)) > 0",
            name="ck_m04e_mapping_source_nonempty",
        ),
        sa.CheckConstraint(
            "phase IN ('definition', 'version')", name="ck_m04e_mapping_phase"
        ),
        # Gap 6: an ACCEPTED mapping is representable in the column itself.
        sa.CheckConstraint(
            "status IN ('accepted', 'replay', 'quarantined', 'conflict', 'blocked')",
            name="ck_m04e_mapping_status",
        ),
        sa.CheckConstraint(
            f"source_digest ~ {_SHA256}", name="ck_m04e_mapping_source_sha256"
        ),
        sa.CheckConstraint(
            f"canonical_digest IS NULL OR canonical_digest ~ {_SHA256}",
            name="ck_m04e_mapping_canonical_sha256",
        ),
        sa.CheckConstraint(
            "(artifact_digest IS NULL OR artifact_digest ~ " + _SHA256 + ") "
            "AND (verified_artifact_digest IS NULL OR verified_artifact_digest ~ "
            + _SHA256 + ")",
            name="ck_m04e_mapping_artifact_sha256",
        ),
        # ``CASE`` rather than ``AND``: PostgreSQL does not promise left-to-right
        # evaluation, and ``jsonb_array_length`` raises on a non-array value.
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(canonical_identity) <> 'array' THEN false "
            "WHEN status <> 'accepted' THEN true "
            "ELSE jsonb_array_length(canonical_identity) = "
            "CASE phase WHEN 'definition' THEN 2 ELSE 3 END END",
            name="ck_m04e_mapping_canonical_identity_shape",
        ),
        sa.CheckConstraint(
            "status <> 'accepted' OR canonical_digest IS NOT NULL",
            name="ck_m04e_mapping_accepted_has_canonical_digest",
        ),
        sa.CheckConstraint(
            "status = 'accepted' OR (reason_code IS NOT NULL "
            "AND length(btrim(reason_code)) > 0)",
            name="ck_m04e_mapping_non_accepted_requires_reason",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proof_evidence) = 'object'",
            name="ck_m04e_mapping_proof_evidence_object",
        ),
        sa.CheckConstraint(
            "length(btrim(checkpoint)) > 0", name="ck_m04e_mapping_checkpoint_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04e_mapping_correlation_nonempty",
        ),
    )


def _create_quarantine_table() -> None:
    op.create_table(
        QUARANTINE_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("source_schema_version", sa.String(32), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("observed_source_digest", sa.String(71), nullable=True),
        sa.Column("observed_artifact_digest", sa.String(71), nullable=True),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("blocker_evidence", _json_payload(), nullable=False),
        sa.Column("checkpoint", sa.String(160), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="open"
        ),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # Gap 2: the exact observation key. ``NULLS NOT DISTINCT`` keeps the key exact
        # when one of the two observed digests is NULL (artifact observations), which a
        # plain unique constraint would let through as duplicates.
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "source_kind",
            "source_id",
            "source_schema_version",
            "observed_source_digest",
            "observed_artifact_digest",
            name="uq_m04e_quarantine_observation",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_quarantine_tenant_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(definition_id)) > 0",
            name="ck_m04e_quarantine_definition_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(source_kind)) > 0 AND length(btrim(source_id)) > 0 "
            "AND length(btrim(source_schema_version)) > 0",
            name="ck_m04e_quarantine_source_nonempty",
        ),
        sa.CheckConstraint(
            "phase IN ('definition', 'version')", name="ck_m04e_quarantine_phase"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'archived')",
            name="ck_m04e_quarantine_status",
        ),
        # One observation is exactly one changed digest, never both and never neither.
        sa.CheckConstraint(
            "(observed_source_digest IS NOT NULL) <> (observed_artifact_digest IS NOT NULL)",
            name="ck_m04e_quarantine_single_observation",
        ),
        sa.CheckConstraint(
            "(observed_source_digest IS NULL OR observed_source_digest ~ " + _SHA256 + ") "
            "AND (observed_artifact_digest IS NULL OR observed_artifact_digest ~ "
            + _SHA256 + ")",
            name="ck_m04e_quarantine_observed_sha256",
        ),
        sa.CheckConstraint(
            f"source_digest ~ {_SHA256}", name="ck_m04e_quarantine_source_sha256"
        ),
        sa.CheckConstraint(
            "observed_source_digest IS NULL OR observed_source_digest <> source_digest",
            name="ck_m04e_quarantine_observation_differs",
        ),
        sa.CheckConstraint(
            "length(btrim(reason_code)) > 0", name="ck_m04e_quarantine_reason_nonempty"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blocker_evidence) = 'array'",
            name="ck_m04e_quarantine_blocker_evidence_array",
        ),
        sa.CheckConstraint(
            "length(btrim(checkpoint)) > 0", name="ck_m04e_quarantine_checkpoint_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04e_quarantine_correlation_nonempty",
        ),
    )


def _create_checkpoint_table() -> None:
    op.create_table(
        CHECKPOINT_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("after_source_id", sa.String(160), nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "phase_complete", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_schema_version", sa.String(32), nullable=False),
        sa.Column("source_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("canonical_schema_version", sa.String(32), nullable=False),
        sa.Column("writer_generation", sa.Integer(), nullable=True),
        sa.Column("checkpoint_token", sa.String(160), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # Gap 3: one head row per partition+phase, and one row per CAS token.
        sa.UniqueConstraint(
            "tenant_id", "definition_id", "phase", name="uq_m04e_checkpoint_partition"
        ),
        sa.UniqueConstraint("checkpoint_token", name="uq_m04e_checkpoint_token"),
        sa.CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_checkpoint_tenant_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(definition_id)) > 0",
            name="ck_m04e_checkpoint_definition_nonempty",
        ),
        sa.CheckConstraint(
            "phase IN ('definition', 'version')", name="ck_m04e_checkpoint_phase"
        ),
        sa.CheckConstraint(
            "sequence >= 0", name="ck_m04e_checkpoint_sequence_nonnegative"
        ),
        sa.CheckConstraint(
            "writer_generation IS NULL OR writer_generation >= 0",
            name="ck_m04e_checkpoint_writer_generation_nonnegative",
        ),
        sa.CheckConstraint(
            "phase_complete = false OR phase = 'version'",
            name="ck_m04e_checkpoint_complete_only_version",
        ),
        sa.CheckConstraint(
            "after_source_id IS NULL OR length(btrim(after_source_id)) > 0",
            name="ck_m04e_checkpoint_cursor_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(source_snapshot_id)) > 0",
            name="ck_m04e_checkpoint_source_snapshot_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(source_schema_version)) > 0",
            name="ck_m04e_checkpoint_source_schema_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(canonical_schema_version)) > 0",
            name="ck_m04e_checkpoint_canonical_schema_nonempty",
        ),
        sa.CheckConstraint(
            f"source_snapshot_digest ~ {_SHA256}",
            name="ck_m04e_checkpoint_source_sha256",
        ),
        sa.CheckConstraint(
            "length(btrim(checkpoint_token)) > 0",
            name="ck_m04e_checkpoint_token_nonempty",
        ),
    )


def _create_audit_table() -> None:
    op.create_table(
        AUDIT_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(160), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("checkpoint", sa.String(160), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("transaction_id", sa.String(160), nullable=False),
        sa.Column(
            "record_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "accepted_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "quarantined_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocker_evidence", _json_payload(), nullable=False, server_default="[]"),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_m04e_audit_event"),
        sa.CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_audit_tenant_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(definition_id)) > 0",
            name="ck_m04e_audit_definition_nonempty",
        ),
        sa.CheckConstraint(
            "phase IN ('definition', 'version')", name="ck_m04e_audit_phase"
        ),
        # Gap 4: the redacted per-page event vocabulary is closed, not free text.
        sa.CheckConstraint(
            "event_kind IN ('page_committed', 'page_blocked')",
            name="ck_m04e_audit_event_kind",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR length(btrim(reason_code)) > 0",
            name="ck_m04e_audit_reason_nonempty",
        ),
        sa.CheckConstraint(
            "event_kind <> 'page_blocked' OR (reason_code IS NOT NULL "
            "AND length(btrim(reason_code)) > 0)",
            name="ck_m04e_audit_blocked_requires_reason",
        ),
        sa.CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04e_audit_correlation_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(checkpoint)) > 0", name="ck_m04e_audit_checkpoint_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(actor)) > 0", name="ck_m04e_audit_actor_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(transaction_id)) > 0",
            name="ck_m04e_audit_transaction_nonempty",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_m04e_audit_sequence_nonnegative"),
        sa.CheckConstraint(
            "accepted_count >= 0 AND replay_count >= 0 AND quarantined_count >= 0 "
            "AND conflict_count >= 0 AND record_count >= 0",
            name="ck_m04e_audit_counters_nonnegative",
        ),
        # A committed page accounts for every classification it wrote; a blocked page
        # writes none. A row cannot claim a partial page.
        sa.CheckConstraint(
            "(event_kind = 'page_committed' AND record_count = accepted_count + "
            "replay_count + quarantined_count + conflict_count) "
            "OR (event_kind = 'page_blocked' AND record_count = 0 AND accepted_count = 0 "
            "AND replay_count = 0 AND quarantined_count = 0 AND conflict_count = 0)",
            name="ck_m04e_audit_counters_balance",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blocker_evidence) = 'array'",
            name="ck_m04e_audit_blocker_evidence_array",
        ),
    )


def upgrade() -> None:
    # 1. The four ledger tables (order matters only for readability: no inter-ledger FK).
    _create_mappings_table()
    _create_quarantine_table()
    _create_checkpoint_table()
    _create_audit_table()

    # 2. Partition/cursor/source indexes.
    for index_name, table_name, columns in LEDGER_INDEXES:
        op.create_index(index_name, table_name, list(columns))

    # 3. Append-only + monotonicity guards, so a direct-SQL writer cannot rewrite
    #    recorded evidence or walk a cursor backwards.
    op.execute(_CREATE_APPEND_ONLY_FUNCTION)
    for table_name in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {APPEND_ONLY_TRIGGER.format(table=table_name)} "
            f"BEFORE UPDATE OR DELETE ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}()"
        )
    op.execute(_CREATE_CHECKPOINT_GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {CHECKPOINT_GUARD_TRIGGER} "
        f"BEFORE UPDATE ON {CHECKPOINT_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {CHECKPOINT_GUARD_FUNCTION}()"
    )

    # 4. RLS for the tables this revision just created. This is the registry re-apply
    #    for owner_revision 2026110315: a table-adding revision that leaves its own
    #    policy to "the next pass" is the defect the install-order gate exists for.
    installed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
        installed += 1
    logger.info(
        "rls %s: re-applied %d canonical table policies after creating %d M04E ledger "
        "tables",
        revision,
        installed,
        len(LEDGER_TABLES),
    )


def downgrade() -> None:
    """Remove only this revision's objects, in reverse creation order.

    Policies first (they belong to the tables about to disappear), then the triggers
    and their functions, then the indexes, then the tables. Policies owned by earlier
    installers (``2026110307``, ``2026110310``, ``2026110312``, ``2026110314``) are
    never touched: withdrawing them would silently unprotect tables this revision never
    installed anything for.
    """
    removed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        if spec.owner_revision not in _POLICIES_ADDED_HERE:
            continue
        op.execute(policies.downgrade_guard_sql(spec))
        removed += 1

    op.execute(f"DROP TRIGGER IF EXISTS {CHECKPOINT_GUARD_TRIGGER} ON {CHECKPOINT_TABLE}")
    for table_name in APPEND_ONLY_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS {APPEND_ONLY_TRIGGER.format(table=table_name)} "
            f"ON {table_name}"
        )
    op.execute(f"DROP FUNCTION IF EXISTS {CHECKPOINT_GUARD_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {APPEND_ONLY_FUNCTION}()")

    for index_name, table_name, _columns in reversed(LEDGER_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    for table_name in reversed(LEDGER_TABLES):
        op.drop_table(table_name)

    logger.info(
        "rls %s: withdrew %d canonical table policies and dropped %d M04E ledger tables",
        revision,
        removed,
        len(LEDGER_TABLES),
    )
