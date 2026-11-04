"""M04E definition/version backfill ledger: mappings, quarantine, checkpoints, audit.

Task ``MIG-DDL-M04E-LEDGER-01``.

Why these four tables exist
---------------------------
``application/migration/m04e_ports.py`` declares the ``EvidenceStore`` boundary
``SqlM04EEvidenceStore`` and refuses every mutating method with
:data:`~prodavan.application.migration.m04e_ports.EVIDENCE_STORE_SCHEMA_GAPS`.
The reasons are specific and none of them is a matter of taste:

* there is no per-source first-mapping ledger, so ``find_existing`` cannot answer
  and returning ``None`` would license a second append for an already-mapped
  source identity — the opposite of the fail-closed rule;
* no table carries an ``observed_source_digest`` / ``observed_artifact_digest``
  pair, so a changed-digest conflict cannot be replayed exactly
  (``find_observation`` has no storage);
* no table carries the durable cursor (``phase``, ``after_source_id``,
  ``sequence``, ``phase_complete``) or a CAS token, so a page cannot advance its
  cursor atomically with the evidence it classifies;
* no table stores the redacted per-page audit trail.

The three registered M04E tables (revision ``2026110302``) cannot carry any of it:
``m04e_dynamic_app_definitions`` / ``m04e_dynamic_app_versions`` are canonical
identity rows with no ``source_kind``, ``source_schema_version``, ``observed_*``,
``reason_code``, ``correlation_id`` or cursor column, and
``m04e_definition_version_promotion_evidence`` is a **per-partition** promotion
record whose ``outcome`` is CHECK-limited to ``('blocked','quarantined')``
(``ck_m04e_promotion_outcome``) with four NOT NULL sha256 gate proofs that a
blocked page cannot truthfully supply. Packing a ledger and a cursor into the
redacted ``gate_evidence`` JSONB envelope was implemented once and rejected as
invented schema: a plain-SQL cursor query and an exact per-source lookup are
impossible over a redacted envelope.

These four tables are the reviewed replacement. They mirror the shape of
``scope_owner_mappings`` / ``scope_owner_quarantine`` / ``scope_owner_audit``
(M04-01) and ``dynamic_app_capability_grant_mappings`` /
``..._quarantine`` / ``..._audit`` (M06), because those are the two accepted
precedents for "redacted per-source evidence + append-shaped audit" in this
schema.

What a row is (and is not)
--------------------------
* **mappings** — the first mapping ever recorded for one exact source identity
  ``(tenant_id, definition_id, source_kind, source_id, source_schema_version)``:
  the source digest, the canonical digest and identity, the phase, the outcome
  status (``accepted`` is admissible here — this is the table a reader selects
  ``status = 'accepted'`` from without decoding an envelope), the redacted proof
  digests, the artifact digests, the page checkpoint token and the correlation
  id.
* **quarantine** — one changed-digest *observation*: the source identity, the
  originally recorded source digest, exactly one observed digest (source **or**
  artifact, never both), a redacted reason code, redacted blocker evidence, the
  checkpoint token and the correlation id. This is what makes ``find_observation``
  exact: the unique key is the source identity plus the observed digest.
* **checkpoints** — the durable cursor of one ``(tenant_id, definition_id,
  phase)`` stream: ``after_source_id``, ``sequence``, ``phase_complete``, the
  source/canonical schema lineage, ``writer_generation`` and the
  ``checkpoint_token`` a writer must present to advance it (CAS).
* **audit** — the append-only, redacted per-page event stream: ``event_kind``,
  ``reason_code``, ``correlation_id``, ``checkpoint``, ``phase``, ``sequence``,
  ``actor``, ``transaction_id`` and the numeric page counters. No payload,
  manifest body, description or secret reaches a column in any of the four.

None of these rows is an authorization decision. There is no
``canonical_positive_writes`` flag here on purpose: unlike the promotion-evidence
tables, a ledger row does not gate a positive writer at all, it only records what
the backfill mapped. Authorization stays owned by the promotion-evidence tables
and the legacy writer.

Deliberate decisions
--------------------
* ``m04e_definition_version_mappings.status`` admits
  ``('accepted', 'replay', 'quarantined', 'conflict', 'blocked')`` — the closed
  :class:`~prodavan.application.migration.m04e_definition_version.backfill.BackfillOutcome`
  vocabulary — instead of the two-state ``('blocked','quarantined')`` limit of
  ``ck_m04e_promotion_outcome``. Blocker
  ``m04e_promotion_accepted_outcome_unrepresentable`` is exactly this: the only
  registered M04E evidence table could not record an ACCEPTED mapping. The old
  table is left untouched (rewriting an applied CHECK is a data migration).
* ``status`` has **no server default**: a writer must state the outcome. A
  default of ``'accepted'`` would let a careless insert claim acceptance and a
  default of ``'blocked'`` would fabricate a refusal.
* No foreign key to ``m04e_dynamic_app_definitions``. The ledger must be able to
  record **blocked** evidence for a partition whose canonical definition could not
  be resolved (``m04e_candidate_proof_definition_absent``); a hard FK would make
  that truthful row impossible to write, i.e. the table would only be able to
  store the happy path. Tenant equality of ``definition_id`` is instead enforced
  by the tenant-scoped RLS policy and the trusted migration writer, exactly as
  ``scope_owner_quarantine`` does for its ``resolved_tenant_id``.
* ``uq_m04e_quarantine_observation`` is declared ``NULLS NOT DISTINCT`` and the
  table requires exactly one of the two observed digests. Without that, a second
  identical *artifact* observation (where ``observed_source_digest`` is NULL)
  would not collide — PostgreSQL treats NULLs as distinct in a plain unique
  constraint — and the exact replay ``find_observation`` promises would silently
  stop being exact. ``NULLS NOT DISTINCT`` requires PostgreSQL 15+; the repo pins
  ``postgres:16.15``.
* Checkpoint monotonicity is expressed with what a CHECK and a unique key can
  express: one head row per ``(tenant_id, definition_id, phase)``, a globally
  unique ``checkpoint_token``, ``sequence >= 0``, ``phase_complete`` only in the
  ``version`` phase, and a non-empty cursor whenever a value is present. The
  *advance* itself is a CAS (``UPDATE ... WHERE tenant_id = ? AND definition_id
  = ? AND phase = ? AND sequence = ?``); a zero-row update is a stale token and
  must fail closed. A sequence regression, a phase rewind, a lineage rebind, an
  un-completion and token reuse are refused by the ORM guard below **and** by the
  ``trg_m04e_checkpoint_monotonic`` trigger revision ``2026110315`` installs, so a
  direct-SQL writer is covered too. The residual gap is a role allowed to
  ``ALTER TABLE ... DISABLE TRIGGER`` — see :data:`M04E_LEDGER_DEFERRED`.
* Append-only is enforced twice for the three immutable ledgers: an ORM
  ``before_update``/``before_delete`` guard and a database trigger
  (``m04e_ledger_append_only`` / ``trg_m04e_ledger_append_only_<table>``,
  revision ``2026110315``) that raises on UPDATE/DELETE regardless of role. The
  per-role ``REVOKE``/``GRANT`` inventory is a separate privilege review.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
    inspect as sa_inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


#: The closed backfill outcome vocabulary
#: (``backfill.BackfillOutcome`` / ``backfill.EvidenceStatus``). ``accepted`` is the
#: state the two oldest promotion tables cannot express.
_MAPPING_STATUSES = "('accepted', 'replay', 'quarantined', 'conflict', 'blocked')"

#: Only two backfill phases exist; ``phase_complete`` is only reachable in the second.
_PHASES = "('definition', 'version')"

#: One observation is exactly one changed digest, never both (see module notes).
_QUARANTINE_STATES = "('open', 'resolved', 'archived')"

#: A page either commits its classifications or records why it could not.
_AUDIT_EVENT_KINDS = "('page_committed', 'page_blocked')"

#: ``^sha256:[0-9a-f]{64}$`` as a SQL predicate. Kept as one fragment per column
#: family so the model and revision cannot drift by a typo.
_SHA256 = "'^sha256:[0-9a-f]{64}$'"

_SOURCE_KEY = ("tenant_id", "definition_id", "source_kind", "source_id", "source_schema_version")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _reject_evidence_mutation(target: Any, owner: str) -> None:
    """Append-only guard: a recorded ledger row is never rewritten.

    Attribute history is used (not a stored-row comparison) because a ledger row is
    appended once and never loaded for an update in a legitimate path: *any*
    changed attribute on a persisted row is a violation, including an in-place
    JSON mutation the ORM could not see otherwise.
    """
    state = sa_inspect(target)
    if state.identity is None:
        return
    changed = tuple(
        sorted(
            attribute.key
            for attribute in state.attrs
            if attribute.history.has_changes()
        )
    )
    if changed:
        raise ValueError(
            f"{owner} is append-only; immutable fields cannot be updated: {', '.join(changed)}"
        )


class M04EDefinitionVersionMappingRow(Base):
    """First mapping ever recorded for one exact source identity.

    The partition is ``(tenant_id, definition_id)``; the identity inside it is
    ``uq_m04e_mapping_source`` = ``(tenant_id, definition_id, source_kind,
    source_id, source_schema_version)``, so a second mapping row for an
    already-mapped source identity is impossible at the storage layer rather than
    by writer discipline.
    """

    __tablename__ = "m04e_definition_version_mappings"
    __table_args__ = (
        UniqueConstraint(*_SOURCE_KEY, name="uq_m04e_mapping_source"),
        CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_mapping_tenant_nonempty"
        ),
        CheckConstraint(
            "length(btrim(definition_id)) > 0", name="ck_m04e_mapping_definition_nonempty"
        ),
        CheckConstraint(
            "length(btrim(source_kind)) > 0 AND length(btrim(source_id)) > 0 "
            "AND length(btrim(source_schema_version)) > 0",
            name="ck_m04e_mapping_source_nonempty",
        ),
        CheckConstraint(f"phase IN {_PHASES}", name="ck_m04e_mapping_phase"),
        CheckConstraint(f"status IN {_MAPPING_STATUSES}", name="ck_m04e_mapping_status"),
        CheckConstraint(
            f"source_digest ~ {_SHA256}", name="ck_m04e_mapping_source_sha256"
        ),
        CheckConstraint(
            f"canonical_digest IS NULL OR canonical_digest ~ {_SHA256}",
            name="ck_m04e_mapping_canonical_sha256",
        ),
        CheckConstraint(
            "(artifact_digest IS NULL OR artifact_digest ~ " + _SHA256 + ") "
            "AND (verified_artifact_digest IS NULL OR verified_artifact_digest ~ "
            + _SHA256 + ")",
            name="ck_m04e_mapping_artifact_sha256",
        ),
        # ``CASE`` rather than ``AND``: PostgreSQL does not promise left-to-right
        # evaluation, and ``jsonb_array_length`` raises on a non-array value.
        CheckConstraint(
            "CASE WHEN jsonb_typeof(canonical_identity) <> 'array' THEN false "
            "WHEN status <> 'accepted' THEN true "
            "ELSE jsonb_array_length(canonical_identity) = "
            "CASE phase WHEN 'definition' THEN 2 ELSE 3 END END",
            name="ck_m04e_mapping_canonical_identity_shape",
        ),
        CheckConstraint(
            "status <> 'accepted' OR canonical_digest IS NOT NULL",
            name="ck_m04e_mapping_accepted_has_canonical_digest",
        ),
        CheckConstraint(
            "status = 'accepted' OR (reason_code IS NOT NULL "
            "AND length(btrim(reason_code)) > 0)",
            name="ck_m04e_mapping_non_accepted_requires_reason",
        ),
        CheckConstraint(
            "jsonb_typeof(proof_evidence) = 'object'",
            name="ck_m04e_mapping_proof_evidence_object",
        ),
        CheckConstraint(
            "length(btrim(checkpoint)) > 0", name="ck_m04e_mapping_checkpoint_nonempty"
        ),
        CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04e_mapping_correlation_nonempty",
        ),
        Index(
            "ix_m04e_mapping_partition", "tenant_id", "definition_id", "recorded_at"
        ),
        Index(
            "ix_m04e_mapping_cursor", "tenant_id", "definition_id", "phase", "source_id"
        ),
        Index("ix_m04e_mapping_source_lookup", "source_kind", "source_id", "source_schema_version"),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("m04emap")
    )
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    # No server default: the writer states the outcome (see module notes).
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    # ``(tenant_id, app_id)`` for a definition, ``(tenant_id, definition_id,
    # manifest_version)`` for a version — the contract's ``canonical_identity``.
    canonical_identity: Mapped[list] = mapped_column(JSONB, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Redacted derived digests only: ``{proof name: sha256 digest}``.
    proof_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    verified_artifact_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    # Opaque page cursor token the row was committed under; the matching
    # ``m04e_definition_version_checkpoints`` row carries the same value.
    checkpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class M04EDefinitionVersionQuarantineRow(Base):
    """One changed-digest observation for an already-mapped source identity.

    ``uq_m04e_quarantine_observation`` is the exact key ``find_observation`` reads:
    the source identity plus the observed digest. Exactly one of the two observed
    digests is present (``ck_m04e_quarantine_single_observation``) and it must
    differ from the originally recorded ``source_digest``
    (``ck_m04e_quarantine_observation_differs``), so a row can never be a
    non-observation.
    """

    __tablename__ = "m04e_definition_version_quarantine"
    __table_args__ = (
        UniqueConstraint(
            *_SOURCE_KEY,
            "observed_source_digest",
            "observed_artifact_digest",
            name="uq_m04e_quarantine_observation",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_quarantine_tenant_nonempty"
        ),
        CheckConstraint(
            "length(btrim(definition_id)) > 0",
            name="ck_m04e_quarantine_definition_nonempty",
        ),
        CheckConstraint(
            "length(btrim(source_kind)) > 0 AND length(btrim(source_id)) > 0 "
            "AND length(btrim(source_schema_version)) > 0",
            name="ck_m04e_quarantine_source_nonempty",
        ),
        CheckConstraint(f"phase IN {_PHASES}", name="ck_m04e_quarantine_phase"),
        CheckConstraint(
            f"status IN {_QUARANTINE_STATES}", name="ck_m04e_quarantine_status"
        ),
        CheckConstraint(
            "(observed_source_digest IS NOT NULL) <> (observed_artifact_digest IS NOT NULL)",
            name="ck_m04e_quarantine_single_observation",
        ),
        CheckConstraint(
            "(observed_source_digest IS NULL OR observed_source_digest ~ " + _SHA256 + ") "
            "AND (observed_artifact_digest IS NULL OR observed_artifact_digest ~ "
            + _SHA256 + ")",
            name="ck_m04e_quarantine_observed_sha256",
        ),
        CheckConstraint(
            f"source_digest ~ {_SHA256}", name="ck_m04e_quarantine_source_sha256"
        ),
        CheckConstraint(
            "observed_source_digest IS NULL OR observed_source_digest <> source_digest",
            name="ck_m04e_quarantine_observation_differs",
        ),
        CheckConstraint(
            "length(btrim(reason_code)) > 0", name="ck_m04e_quarantine_reason_nonempty"
        ),
        CheckConstraint(
            "jsonb_typeof(blocker_evidence) = 'array'",
            name="ck_m04e_quarantine_blocker_evidence_array",
        ),
        CheckConstraint(
            "length(btrim(checkpoint)) > 0", name="ck_m04e_quarantine_checkpoint_nonempty"
        ),
        CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04e_quarantine_correlation_nonempty",
        ),
        Index(
            "ix_m04e_quarantine_partition",
            "tenant_id",
            "definition_id",
            "recorded_at",
        ),
        Index(
            "ix_m04e_quarantine_source_lookup",
            "source_kind",
            "source_id",
            "source_schema_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("m04equar")
    )
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # The digest of the first (still authoritative) mapping this observation contradicts.
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    observed_source_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    observed_artifact_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    # Redacted, actionable guidance strings; never a payload excerpt.
    blocker_evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    checkpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class M04EDefinitionVersionCheckpointRow(Base):
    """Durable cursor of one ``(tenant_id, definition_id, phase)`` stream.

    The head row is read ``FOR UPDATE`` and advanced by CAS::

        UPDATE m04e_definition_version_checkpoints
           SET after_source_id = :after_source_id, sequence = :sequence,
               phase_complete = :complete, checkpoint_token = :token,
               updated_at = now()
         WHERE tenant_id = :tenant_id AND definition_id = :definition_id
           AND phase = :phase AND sequence = :observed_sequence

    A zero-row update is a stale ``sequence``/token and must fail closed; nothing
    here silently retries. ``checkpoint_token`` is globally unique so a token can
    never be presented for two different cursors, and the guard below refuses a
    sequence regression, a phase regression and an un-completion for ORM writers.
    """

    __tablename__ = "m04e_definition_version_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "definition_id", "phase", name="uq_m04e_checkpoint_partition"
        ),
        UniqueConstraint("checkpoint_token", name="uq_m04e_checkpoint_token"),
        CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_checkpoint_tenant_nonempty"
        ),
        CheckConstraint(
            "length(btrim(definition_id)) > 0",
            name="ck_m04e_checkpoint_definition_nonempty",
        ),
        CheckConstraint(f"phase IN {_PHASES}", name="ck_m04e_checkpoint_phase"),
        CheckConstraint("sequence >= 0", name="ck_m04e_checkpoint_sequence_nonnegative"),
        CheckConstraint(
            "writer_generation IS NULL OR writer_generation >= 0",
            name="ck_m04e_checkpoint_writer_generation_nonnegative",
        ),
        CheckConstraint(
            "phase_complete = false OR phase = 'version'",
            name="ck_m04e_checkpoint_complete_only_version",
        ),
        CheckConstraint(
            "after_source_id IS NULL OR length(btrim(after_source_id)) > 0",
            name="ck_m04e_checkpoint_cursor_nonempty",
        ),
        CheckConstraint(
            "length(btrim(source_snapshot_id)) > 0",
            name="ck_m04e_checkpoint_source_snapshot_nonempty",
        ),
        CheckConstraint(
            "length(btrim(source_schema_version)) > 0",
            name="ck_m04e_checkpoint_source_schema_nonempty",
        ),
        CheckConstraint(
            "length(btrim(canonical_schema_version)) > 0",
            name="ck_m04e_checkpoint_canonical_schema_nonempty",
        ),
        CheckConstraint(
            f"source_snapshot_digest ~ {_SHA256}",
            name="ck_m04e_checkpoint_source_sha256",
        ),
        CheckConstraint(
            "length(btrim(checkpoint_token)) > 0",
            name="ck_m04e_checkpoint_token_nonempty",
        ),
        Index("ix_m04e_checkpoint_snapshot", "source_snapshot_id"),
        Index(
            "ix_m04e_checkpoint_completion",
            "tenant_id",
            "definition_id",
            "phase_complete",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("m04ecp")
    )
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    after_source_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    phase_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_snapshot_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    writer_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkpoint_token: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class M04EDefinitionVersionAuditRow(Base):
    """Append-only, redacted per-page backfill event.

    ``event_kind`` is closed: a page either committed its classifications
    (``page_committed``) or recorded why it could not (``page_blocked``). The
    counters must balance — a committed page accounts for every classification it
    wrote, a blocked page writes no classification at all — which is what
    ``ck_m04e_audit_counters_balance`` enforces. UPDATE and DELETE are refused by
    the database trigger installed in revision ``2026110315``.
    """

    __tablename__ = "m04e_definition_version_audit"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_m04e_audit_event"),
        CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04e_audit_tenant_nonempty"
        ),
        CheckConstraint(
            "length(btrim(definition_id)) > 0", name="ck_m04e_audit_definition_nonempty"
        ),
        CheckConstraint(f"phase IN {_PHASES}", name="ck_m04e_audit_phase"),
        CheckConstraint(
            f"event_kind IN {_AUDIT_EVENT_KINDS}", name="ck_m04e_audit_event_kind"
        ),
        CheckConstraint(
            "reason_code IS NULL OR length(btrim(reason_code)) > 0",
            name="ck_m04e_audit_reason_nonempty",
        ),
        CheckConstraint(
            "event_kind <> 'page_blocked' OR (reason_code IS NOT NULL "
            "AND length(btrim(reason_code)) > 0)",
            name="ck_m04e_audit_blocked_requires_reason",
        ),
        CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04e_audit_correlation_nonempty",
        ),
        CheckConstraint(
            "length(btrim(checkpoint)) > 0", name="ck_m04e_audit_checkpoint_nonempty"
        ),
        CheckConstraint(
            "length(btrim(actor)) > 0", name="ck_m04e_audit_actor_nonempty"
        ),
        CheckConstraint(
            "length(btrim(transaction_id)) > 0",
            name="ck_m04e_audit_transaction_nonempty",
        ),
        CheckConstraint("sequence >= 0", name="ck_m04e_audit_sequence_nonnegative"),
        CheckConstraint(
            "accepted_count >= 0 AND replay_count >= 0 AND quarantined_count >= 0 "
            "AND conflict_count >= 0 AND record_count >= 0",
            name="ck_m04e_audit_counters_nonnegative",
        ),
        CheckConstraint(
            "(event_kind = 'page_committed' AND record_count = accepted_count + "
            "replay_count + quarantined_count + conflict_count) "
            "OR (event_kind = 'page_blocked' AND record_count = 0 AND accepted_count = 0 "
            "AND replay_count = 0 AND quarantined_count = 0 AND conflict_count = 0)",
            name="ck_m04e_audit_counters_balance",
        ),
        CheckConstraint(
            "jsonb_typeof(blocker_evidence) = 'array'",
            name="ck_m04e_audit_blocker_evidence_array",
        ),
        Index(
            "ix_m04e_audit_partition_time", "tenant_id", "definition_id", "recorded_at"
        ),
        Index("ix_m04e_audit_checkpoint", "tenant_id", "definition_id", "checkpoint"),
        Index("ix_m04e_audit_correlation", "correlation_id"),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("m04eaudit")
    )
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    checkpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    accepted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    quarantined_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    conflict_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Redacted, actionable strings for a blocked page; never a payload excerpt.
    blocker_evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Append-only guards (ORM layer; the database trigger is revision 2026110315)
# ---------------------------------------------------------------------------


@event.listens_for(M04EDefinitionVersionMappingRow, "before_update")
def _reject_mapping_update(_mapper, _connection, target) -> None:
    _reject_evidence_mutation(target, "m04e definition/version mapping ledger")


@event.listens_for(M04EDefinitionVersionMappingRow, "before_delete")
def _reject_mapping_delete(_mapper, _connection, target) -> None:
    raise ValueError("m04e definition/version mapping ledger is append-only; rows are never deleted")


@event.listens_for(M04EDefinitionVersionQuarantineRow, "before_update")
def _reject_quarantine_update(_mapper, _connection, target) -> None:
    _reject_evidence_mutation(target, "m04e definition/version quarantine ledger")


@event.listens_for(M04EDefinitionVersionQuarantineRow, "before_delete")
def _reject_quarantine_delete(_mapper, _connection, target) -> None:
    raise ValueError(
        "m04e definition/version quarantine ledger is append-only; observations are never deleted"
    )


@event.listens_for(M04EDefinitionVersionAuditRow, "before_update")
def _reject_audit_update(_mapper, _connection, target) -> None:
    _reject_evidence_mutation(target, "m04e definition/version audit ledger")


@event.listens_for(M04EDefinitionVersionAuditRow, "before_delete")
def _reject_audit_delete(_mapper, _connection, target) -> None:
    raise ValueError("m04e definition/version audit ledger is append-only; events are never deleted")


#: Fields of a checkpoint row that identify *which* cursor it is. Rebinding any of
#: them would silently attach a durable cursor to another partition or snapshot
#: lineage, so they are immutable for the ORM writer.
_CHECKPOINT_LINEAGE_FIELDS = (
    "id",
    "tenant_id",
    "definition_id",
    "source_snapshot_id",
    "source_schema_version",
    "source_snapshot_digest",
    "canonical_schema_version",
    "writer_generation",
)

_PHASE_ORDER = {"definition": 0, "version": 1}


@event.listens_for(M04EDefinitionVersionCheckpointRow, "before_update")
def _reject_checkpoint_regression(_mapper, _connection, target) -> None:
    """Refuse a rebind, a sequence regression, a phase regression or an un-completion.

    The advance itself is the caller's CAS predicate; this guard only rejects the
    states a CAS must never produce, so a programming error cannot persist a cursor
    that moves backwards and re-reads or skips a page.
    """
    state = sa_inspect(target)
    if state.identity is None:
        return
    rebound = tuple(
        sorted(
            name
            for name in _CHECKPOINT_LINEAGE_FIELDS
            if state.attrs[name].history.has_changes()
        )
    )
    if rebound:
        raise ValueError(
            f"m04e checkpoint lineage cannot be rebound: {', '.join(rebound)}"
        )

    sequence_history = state.attrs["sequence"].history
    if sequence_history.deleted:
        previous = sequence_history.deleted[0]
        if int(target.sequence) < int(previous):
            raise ValueError(
                "m04e checkpoint sequence must not regress "
                f"(observed {previous}, attempted {target.sequence})"
            )

    phase_history = state.attrs["phase"].history
    if phase_history.deleted:
        previous_phase = phase_history.deleted[0]
        if _PHASE_ORDER[str(target.phase)] < _PHASE_ORDER[str(previous_phase)]:
            raise ValueError(
                "m04e checkpoint phase must not regress "
                f"(observed {previous_phase}, attempted {target.phase})"
            )

    complete_history = state.attrs["phase_complete"].history
    if complete_history.deleted and complete_history.deleted[0] is True and not target.phase_complete:
        raise ValueError("an exhausted m04e checkpoint cannot be reopened")


# ---------------------------------------------------------------------------
# Static review inputs (written down, never executed here: verification freeze)
# ---------------------------------------------------------------------------

#: Tables this module owns. The RLS registry (``rls/policies.py``) registers exactly
#: these names with ``owner_revision`` ``2026110315``.
M04E_LEDGER_TABLES = {
    "m04e_definition_version_mappings": "M04EDefinitionVersionMappingRow",
    "m04e_definition_version_quarantine": "M04EDefinitionVersionQuarantineRow",
    "m04e_definition_version_checkpoints": "M04EDefinitionVersionCheckpointRow",
    "m04e_definition_version_audit": "M04EDefinitionVersionAuditRow",
}

M04E_LEDGER_ACCEPTANCE_CASES = (
    "a_second_mapping_for_one_exact_source_identity_is_rejected",
    "a_mapping_row_can_express_the_accepted_outcome",
    "accepted_mapping_requires_canonical_digest_and_typed_identity",
    "non_accepted_mapping_requires_a_redacted_reason_code",
    "every_source_and_canonical_digest_is_sha256_formatted",
    "an_observation_carries_exactly_one_observed_digest",
    "a_repeated_observation_collides_even_when_one_digest_is_null",
    "a_source_observation_that_does_not_change_the_digest_is_refused",
    "one_checkpoint_head_per_partition_and_phase",
    "checkpoint_token_is_unique_so_a_stale_cas_token_cannot_be_reused",
    "only_an_exhausted_version_stream_can_be_complete",
    "checkpoint_sequence_and_phase_never_regress",
    "checkpoint_lineage_cannot_be_rebound",
    "audit_counter_balance_accounts_for_every_classification",
    "blocked_page_writes_no_classification_counters",
    "blocked_page_requires_a_redacted_reason_code",
    "audit_rows_are_never_updated_or_deleted",
    "no_payload_or_secret_material_is_persisted_in_any_ledger",
    "all_four_ledgers_are_tenant_scoped_by_rls",
    "evidence_store_gaps_are_named_by_the_models_that_close_them",
)

#: Gaps this DDL deliberately does not close, recorded so a reviewer can tell a
#: missing artefact from a reviewed trade-off.
M04E_LEDGER_DEFERRED = {
    "writer_and_composition_root": (
        "No production writer appends ledger rows and SqlM04EEvidenceStore still "
        "raises EvidenceStoreUnavailable: m04e_ports.py and the composition root are "
        "owned by MIG-M04E-PORTS-01 / the wiring task. These tables remove the four "
        "missing-table blockers; they do not by themselves make a page commit."
    ),
    "direct_sql_checkpoint_regression": (
        "The ORM guard and the trg_m04e_checkpoint_monotonic trigger installed by "
        "revision 2026110315 both refuse a sequence regression, a phase rewind, a "
        "lineage rebind, an un-completion and token reuse. What neither covers is a "
        "role holding ALTER TABLE: DISABLE TRIGGER or DROP TRIGGER on the checkpoint "
        "table. Closing that is the role privilege review named in "
        "append_only_privileges, not an ORM concern."
    ),
    "append_only_privileges": (
        "The append-only trigger denies UPDATE/DELETE for the three immutable ledgers "
        "regardless of role *while it is enabled*, but the per-role GRANT/REVOKE "
        "inventory (no UPDATE/DELETE for api roles, INSERT only for the trusted "
        "migration writer, no ALTER on the ledger tables) is a separate privilege "
        "review, exactly like the M04E/M05/M06 promotion rows."
    ),
    "promotion_evidence_two_state_check": (
        "ck_m04e_promotion_outcome on m04e_definition_version_promotion_evidence still "
        "admits only ('blocked','quarantined') and its four sha256 gate proofs are "
        "still NOT NULL. Those are gaps 5-7 of EVIDENCE_STORE_SCHEMA_GAPS and are not "
        "closed here: rewriting an applied CHECK plus relaxing NOT NULL proofs is a "
        "data migration on a table owned by revision 2026110302."
    ),
    "definition_reference_integrity": (
        "There is no foreign key from a ledger row to m04e_dynamic_app_definitions: a "
        "blocked page must be able to record evidence for a partition whose canonical "
        "definition could not be resolved (m04e_candidate_proof_definition_absent), and "
        "a hard FK would make that row impossible to write. Tenant equality is enforced "
        "by the tenant-scoped RLS policy and the trusted writer."
    ),
    "checkpoint_registry_version": (
        "policies.REGISTRY_VERSION still reads 2026110307.1. Bumping it would change "
        "application/migration/tenant_context.py::SECURITY_REVIEW_ID, the identity of "
        "the security-reviewed RLS manifest; that re-approval belongs to the RLS "
        "follow-up task, not to a table-creation task."
    ),
}

__all__ = [
    "M04EDefinitionVersionAuditRow",
    "M04EDefinitionVersionCheckpointRow",
    "M04EDefinitionVersionMappingRow",
    "M04EDefinitionVersionQuarantineRow",
    "M04E_LEDGER_ACCEPTANCE_CASES",
    "M04E_LEDGER_DEFERRED",
    "M04E_LEDGER_TABLES",
]
