"""M06 partition cutover ledger and live legacy source tail.

Task ``MIG-DDL-CUTOVER-01``. These two tables are the last schema blocker in front of
a real cutover: ``application/migration/m06_cutover/ports.py`` declares
``CUTOVER_REQUIRED_SCHEMA`` (the reviewed specification) and ``SqlCutoverRepository``
fails closed with :class:`CutoverRepositoryBlocked` while the tables it names are
absent. ``CUTOVER_SCHEMA_BLOCKERS`` records why none of the registered evidence
tables can host them:

* ``cutover_state_row_absent`` — no table stores the current ``PartitionCutover`` of a
  ``(tenant, app_definition)`` partition;
* ``cutover_positive_write_ledger_absent`` — ``m06_canonical_grant_promotion_evidence``
  pins ``canonical_positive_writes = false`` through ``ck_m06_grant_no_positive_writes``,
  so it cannot record a promoted partition, and writing the true flag only inside the
  redacted JSON envelope would misstate a security-relevant column;
* ``cutover_source_tail_absent`` — no table exposes the live legacy source change
  sequence a final-delta transition must compare with
  ``expected_source_change_sequence``;
* ``cutover_state_cas_absent`` — no table carries a per-partition ``revision`` with a
  unique ``(tenant, definition, revision)`` key, i.e. the only atomic state CAS
  available without ``UPDATE``;
* ``cutover_promotion_marker_absent`` — no table records that a partition has been
  promoted, so the verifiers can only prove that no writer fence advanced past a
  claimed epoch.

What a row is
=============

``m06_partition_cutover_evidence`` (append-only ledger)
    One row per partition transition. The row *is* the resulting state snapshot
    (``state_payload``, the canonical JSON of ``PartitionCutover``) and the lineage
    record (``record_payload``, the canonical JSON of ``PromotionEvidence``), so the
    repository never issues ``UPDATE`` or ``DELETE``: ``load`` is the latest revision
    and ``evidence_by_id`` is the immutable lineage head.

    Two keys carry the two contracts the port promises, and neither is a writer
    convention:

    * ``uq_m06_cutover_partition_revision`` = ``(tenant_id, app_definition_id,
      revision)`` is the **state CAS**. A lost race (two writers appending revision
      *n+1*) surfaces as an ``IntegrityError``, which is exactly what
      ``SqlCutoverRepository.transition`` turns into ``False`` — never a partial write.
    * ``uq_m06_cutover_command`` = ``(tenant_id, app_definition_id, action,
      correlation_id)`` is the **command idempotency key**. The same command cannot be
      applied twice to one partition even under a different evidence id, so a retry
      that reuses a correlation id is refused by storage rather than by retry logic.

``m06_partition_cutover_source_tail`` (mutable head row)
    One row per ``(tenant_id, app_definition_id)``: the live legacy source tail
    (``change_sequence``, ``writer_epoch``). It is written by the legacy writer and
    read by the cutover CAS; a missing row makes a final-delta transition refuse
    (``_source_tail`` returns ``None`` and ``transition`` returns ``False``), which is
    the fail-closed direction. ``append_only`` is ``False`` here on purpose: the tail
    *is* the CAS head, so it moves forward.

Deliberate decisions
====================

* **Column types are exactly ``CUTOVER_REQUIRED_SCHEMA``.** ``TEXT`` (not
  ``VARCHAR(n)``), ``BIGINT``, ``BOOLEAN`` and ``TIMESTAMPTZ``: identifiers, digests
  and canonical JSON are unbounded by contract, and a length limit would turn a
  correct append into a truncation error. ``tests/migration/m06_cutover_evidence/``
  asserts the model, the revision and the specification agree column by column, so
  the three cannot drift apart.
* **Check-constraint names are chosen here** (``ck_m06_cutover_*``); the
  specification names the *predicates* only. The predicates are copied verbatim and
  the names follow the ``ck_m06_*`` family of ``m06_canonical_grant_promotion_evidence``.
* **``ck_m06_cutover_positive_writes_only_when_promoted``** is the one place this
  schema admits ``canonical_positive_writes = true``: only a row whose
  ``resulting_state`` is ``promoted`` may claim a positive canonical write. That is
  the property ``ck_m06_grant_no_positive_writes`` cannot express, and it is asserted
  against both the model and the revision.
* **``ck_m06_cutover_prior_record_pair``** refuses a lineage link with an id and no
  digest: the next transition's CAS compares ``prior_record_digest`` with the stored
  ``lineage_head_digest``, so a half-written pair would make an honest successor
  indistinguishable from a forged one.
* **No foreign key to anything.** A cutover row must be recordable for a partition
  whose upstream evidence is blocked (that is what ``CutoverState.BLOCKED`` rows
  are), and the ledger is append-only lineage rather than a child row; tenant
  equality is enforced by the tenant-scoped RLS policy and the trusted writer, the
  same trade-off ``m04e_definition_version_mappings`` records.
* **No server default on any column.** ``revision``, ``action``,
  ``canonical_positive_writes`` and ``occurred_at`` are stated by the writer; a
  default on ``revision`` would fabricate a CAS position and a default on
  ``canonical_positive_writes`` would either claim or deny a positive write.
* **Append-only is enforced twice** for the ledger: an ORM
  ``before_update``/``before_delete`` guard here and the
  ``m06_cutover_ledger_append_only`` trigger that revision ``2026110316`` installs,
  which raises ``23514`` for direct-SQL writers too.
* **RLS.** Both tables are registered in ``rls/policies.py`` with
  ``owner_revision = "2026110316"``, tenant-scoped by ``tenant_id`` and
  ``trusted_command = "ALL"`` (the trusted migration writer needs SELECT and INSERT).
  Revision ``2026110316`` re-applies the whole registry in the same step that creates
  them, so no table exists without a policy — the defect the install-order gate
  (``tests/migration/rls/test_policy_install_order.py``) exists for.

What these tables do *not* do
=============================

They create no writer, enable no positive canonical write on their own and change no
existing table: they remove the two missing-table blockers of
``CUTOVER_SCHEMA_BLOCKERS`` (the other three stay recorded, because a promotion marker
is a design decision, not a missing column). The residual gaps are listed in
:data:`M06_CUTOVER_EVIDENCE_DEFERRED`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
    event,
    inspect as sa_inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base

#: ``^sha256:[0-9a-f]{64}$`` as a SQL predicate, kept as one fragment so the model and
#: revision ``2026110316`` cannot drift by a typo.
_SHA256 = "'^sha256:[0-9a-f]{64}$'"


def _reject_ledger_mutation(target: Any) -> None:
    """Append-only guard: a recorded cutover row is never rewritten.

    Attribute history is used (not a stored-row comparison) because a ledger row is
    appended once and never loaded for an update: *any* changed attribute on a
    persisted row is a violation, including an in-place JSON mutation.
    """
    state = sa_inspect(target)
    if state.identity is None:
        return
    changed = tuple(
        sorted(attribute.key for attribute in state.attrs if attribute.history.has_changes())
    )
    if changed:
        raise ValueError(
            "m06 partition cutover ledger is append-only; immutable fields cannot be "
            f"updated: {', '.join(changed)}"
        )


class M06PartitionCutoverEvidenceRow(Base):
    """One ``PartitionCutover`` transition: the state snapshot *and* its lineage record.

    The row is never rewritten. ``revision`` is monotonic per partition and unique per
    ``(tenant_id, app_definition_id)`` — that unique key is the state CAS — while
    ``(tenant_id, app_definition_id, action, correlation_id)`` is the command
    idempotency key. ``payload`` columns hold canonical JSON of the two immutable
    contracts in ``m06_cutover/cutover.py``; they never hold a raw upstream payload.
    """

    __tablename__ = "m06_partition_cutover_evidence"
    __table_args__ = (
        # State CAS: a lost race on revision n+1 collides here.
        UniqueConstraint(
            "tenant_id", "app_definition_id", "revision",
            name="uq_m06_cutover_partition_revision",
        ),
        # Command idempotency: one row per (partition, action, correlation).
        UniqueConstraint(
            "tenant_id", "app_definition_id", "action", "correlation_id",
            name="uq_m06_cutover_command",
        ),
        CheckConstraint(
            f"record_digest ~ {_SHA256}", name="ck_m06_cutover_record_sha256"
        ),
        CheckConstraint(
            f"command_digest ~ {_SHA256}", name="ck_m06_cutover_command_sha256"
        ),
        CheckConstraint(
            "revision >= 0 AND writer_epoch >= 0",
            name="ck_m06_cutover_revision_epoch_nonnegative",
        ),
        # The only place this schema admits a positive canonical write: a promoted row.
        CheckConstraint(
            "canonical_positive_writes = false OR resulting_state = 'promoted'",
            name="ck_m06_cutover_positive_writes_only_when_promoted",
        ),
        CheckConstraint(
            "prior_record_id IS NULL OR prior_record_digest IS NOT NULL",
            name="ck_m06_cutover_prior_record_pair",
        ),
        # Listed by CUTOVER_REQUIRED_SCHEMA. It duplicates the unique key's btree; it
        # is created because the reviewed specification names it, and dropping it is a
        # separate decision for the ports owner.
        Index(
            "ix_m06_cutover_partition_revision",
            "tenant_id", "app_definition_id", "revision",
        ),
    )

    # The lineage record id, which is also the cutover command id. No default: a row
    # must state which command produced it.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    app_definition_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Resulting PartitionCutover.revision; the CAS position.
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # CutoverAction value of the transition that produced this row.
    action: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    command_digest: Mapped[str] = mapped_column(Text, nullable=False)
    record_digest: Mapped[str] = mapped_column(Text, nullable=False)
    prior_record_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prior_record_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CutoverState value the partition reached.
    resulting_state: Mapped[str] = mapped_column(Text, nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_fencing_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_positive_writes: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Canonical JSON of PartitionCutover (``state_payload`` codec).
    state_payload: Mapped[str] = mapped_column(Text, nullable=False)
    # Canonical JSON of PromotionEvidence (``evidence_payload`` codec).
    record_payload: Mapped[str] = mapped_column(Text, nullable=False)


class M06PartitionCutoverSourceTailRow(Base):
    """Live legacy source tail of one partition; mutable by design.

    One head row per ``(tenant_id, app_definition_id)``. The cutover CAS reads it to
    compare the live ``change_sequence`` with ``expected_source_change_sequence``; a
    missing row refuses the transition instead of assuming "no changes".
    """

    __tablename__ = "m06_partition_cutover_source_tail"
    __table_args__ = (
        CheckConstraint(
            "change_sequence >= 0 AND writer_epoch >= 0",
            name="ck_m06_cutover_tail_sequence_nonnegative",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    app_definition_id: Mapped[str] = mapped_column(Text, primary_key=True)
    change_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Append-only guard (ORM layer; the database trigger is revision 2026110316)
# ---------------------------------------------------------------------------


@event.listens_for(M06PartitionCutoverEvidenceRow, "before_update")
def _reject_cutover_update(_mapper, _connection, target) -> None:
    _reject_ledger_mutation(target)


@event.listens_for(M06PartitionCutoverEvidenceRow, "before_delete")
def _reject_cutover_delete(_mapper, _connection, target) -> None:
    raise ValueError(
        "m06 partition cutover ledger is append-only; transitions are never deleted"
    )


# ---------------------------------------------------------------------------
# Static review inputs (written down; the acceptance cases live in the tests)
# ---------------------------------------------------------------------------

#: Tables this module owns. The RLS registry (``rls/policies.py``) registers exactly
#: these names with ``owner_revision`` ``2026110316``, and revision ``2026110316``
#: creates exactly these two tables.
M06_CUTOVER_EVIDENCE_TABLES = {
    "m06_partition_cutover_evidence": "M06PartitionCutoverEvidenceRow",
    "m06_partition_cutover_source_tail": "M06PartitionCutoverSourceTailRow",
}

M06_CUTOVER_EVIDENCE_ACCEPTANCE_CASES = (
    "a_second_row_for_one_partition_and_revision_is_rejected_by_the_state_cas",
    "the_same_command_correlation_cannot_be_appended_twice",
    "a_promoted_row_may_claim_canonical_positive_writes",
    "a_non_promoted_row_cannot_claim_canonical_positive_writes",
    "every_record_and_command_digest_is_sha256_formatted",
    "a_negative_revision_or_writer_epoch_is_refused",
    "a_prior_record_id_without_its_digest_is_refused",
    "the_source_tail_is_one_head_row_per_partition_with_non_negative_counters",
    "a_missing_source_tail_makes_a_final_delta_transition_refuse",
    "ledger_rows_are_never_updated_or_deleted",
    "both_tables_are_tenant_scoped_by_rls",
    "the_ledger_and_tail_columns_are_exactly_the_cutover_specification",
)

#: Gaps this DDL deliberately does not close, so a reviewer can tell a missing artefact
#: from a reviewed trade-off.
M06_CUTOVER_EVIDENCE_DEFERRED = {
    "occurred_at_bound_as_iso_string": (
        "SqlCutoverRepository._ledger_values binds evidence.occurred_at.isoformat(), a "
        "str, into a TIMESTAMPTZ column. The reviewed specification declares "
        "TIMESTAMPTZ, so the column keeps that type; under the live asyncpg driver a "
        "string parameter for a timestamptz placeholder is a type error, so the ports "
        "owner must bind the datetime object itself (or cast in the statement) before "
        "a cutover is executed against PostgreSQL. Recorded here because ports.py is "
        "owned by MIG-CUTOVER-PORTS-01 and is read-only for this task."
    ),
    "append_only_privileges": (
        "The trg_m06_cutover_ledger_append_only trigger denies UPDATE/DELETE while it "
        "is enabled, but the per-role GRANT/REVOKE inventory (no UPDATE/DELETE for api "
        "roles, INSERT only for the trusted migration writer, no ALTER on the ledger) "
        "is a separate privilege review, exactly like the M04E/M05/M06 evidence rows."
    ),
    "source_tail_monotonicity": (
        "The tail table has no trigger refusing a change_sequence regression: it is the "
        "CAS head the legacy writer owns, and the specification declares it mutable "
        "(append_only=False). A regression is caught by the cutover CAS (the frozen "
        "sequence would no longer match), not by the table."
    ),
    "promotion_marker": (
        "cutover_promotion_marker_absent stays open. The ledger records a promoted row, "
        "but no verifier reads it: SqlGateVerifier/SqlRollbackVerifier can still only "
        "prove that no dynamic_app_capability_writer_epochs row advanced past the "
        "claimed epoch. Wiring a promoted-marker read into the verifiers changes "
        "verifier semantics and belongs to the ports task, not to this DDL."
    ),
    "registry_version": (
        "policies.REGISTRY_VERSION still reads 2026110307.1. Bumping it would change "
        "application/migration/tenant_context.py::SECURITY_REVIEW_ID, the identity of "
        "the security-reviewed RLS manifest; that re-approval belongs to the RLS "
        "follow-up task, not to a table-creation task."
    ),
}

__all__ = [
    "M06PartitionCutoverEvidenceRow",
    "M06PartitionCutoverSourceTailRow",
    "M06_CUTOVER_EVIDENCE_ACCEPTANCE_CASES",
    "M06_CUTOVER_EVIDENCE_DEFERRED",
    "M06_CUTOVER_EVIDENCE_TABLES",
]
