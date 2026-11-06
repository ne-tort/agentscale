"""ORM rows for immutable project runtime revisions and materialization."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class CapabilityBundleRow(Base):
    """Content-addressed capability manifest belonging to one revision."""

    __tablename__ = "capability_bundles"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    digest: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectRuntimeRevisionRow(Base):
    """Durable lifecycle state for an immutable project runtime snapshot."""

    __tablename__ = "project_runtime_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_project_runtime_revision_number"),
        CheckConstraint(
            "state IN ('planned', 'validated', 'applying', 'ready', 'failed', 'superseded')",
            name="ck_project_runtime_revision_state",
        ),
        Index(
            "ix_project_runtime_revisions_project_state_revision",
            "project_id",
            "state",
            text("revision DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("rr"))
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("capability_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", server_default="planned")
    applied_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pod_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pod_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hydrate_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bridge_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    runtime_image_digest: Mapped[str | None] = mapped_column(String(256), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_event: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MaterializationEntryRow(Base):
    """Observable generated workspace path and its source provenance."""

    __tablename__ = "materialization_entries"
    __table_args__ = (
        UniqueConstraint("revision_id", "workspace_path", name="uq_materialization_revision_path"),
        UniqueConstraint("revision_id", "idempotency_key", name="uq_materialization_revision_idempotency"),
        CheckConstraint(
            "status IN ('planned', 'staged', 'applied', 'failed')",
            name="ck_materialization_entry_status",
        ),
        CheckConstraint("generation >= 0", name="ck_materialization_entry_generation_nonnegative"),
        Index("ix_materialization_entries_revision_status", "revision_id", "status"),
        Index("ix_materialization_entries_revision_generation", "revision_id", "generation"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("me"))
    revision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("project_runtime_revisions.id", ondelete="CASCADE"), nullable=False
    )
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", server_default="planned")
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_capability_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RuntimeRevisionHeadRow(Base):
    """Resource-wide CAS head: exactly one row per runtime resource key.

    ``generation`` is the authoritative monotonic fence of the resource, not of an
    individual ``materialization_entries`` row, so an empty plan is fenced by the
    same conditional update as a populated plan::

        UPDATE runtime_revision_heads
           SET generation = generation + 1, phase = 'applying', fence_token = :token
         WHERE resource_key = :resource_key AND generation = :observed_generation

    A zero-row update means a stale observed generation and must fail closed. The
    head is the only writer of a generation; attempts record generations already
    allocated here. Generations are never decremented and never reused.
    """

    __tablename__ = "runtime_revision_heads"
    __table_args__ = (
        UniqueConstraint("resource_key", name="uq_runtime_revision_head_resource_key"),
        UniqueConstraint("fence_token", name="uq_runtime_revision_head_fence_token"),
        CheckConstraint("generation >= 0", name="ck_runtime_revision_head_generation_nonnegative"),
        CheckConstraint(
            "phase IN ('idle', 'applying', 'applied', 'failed', 'rolled_back')",
            name="ck_runtime_revision_head_phase",
        ),
        CheckConstraint("generation > 0 OR phase = 'idle'", name="ck_runtime_revision_head_idle_generation"),
        CheckConstraint(
            "resource_key <> '' AND resource_key = btrim(resource_key) AND resource_key !~ '[[:space:]]'",
            name="ck_runtime_revision_head_resource_key_well_formed",
        ),
        CheckConstraint(
            "applied_desired_digest IS NULL OR applied_desired_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_revision_head_desired_digest",
        ),
        CheckConstraint(
            "ownership_digest IS NULL OR ownership_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_revision_head_ownership_digest",
        ),
        Index("ix_runtime_revision_heads_project_phase", "project_id", "phase"),
        Index("ix_runtime_revision_heads_resource_id", "resource_id"),
        {
            "comment": (
                "Resource-wide runtime CAS head. Generation is allocated only by a conditional UPDATE "
                "on this row and is never decremented or reused, so empty plans are fenced too."
            )
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("rrh"))
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    resource_key: Mapped[str] = mapped_column(String(200), nullable=False)
    revision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("project_runtime_revisions.id", ondelete="SET NULL"), nullable=True
    )
    applied_revision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("project_runtime_revisions.id", ondelete="SET NULL"), nullable=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default="idle", server_default="idle")
    fence_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applied_desired_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ownership_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    uncertain_operations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RuntimeMaterializationAttemptRow(Base):
    """Durable materialization attempt and its idempotency receipt.

    One row per claimed generation, so a resource keeps the full attempt history
    instead of a single overwritten record. ``UNIQUE (resource_key, generation)``
    lets several applied revisions coexist for one resource (generation 1, 2, 3…)
    while preventing two claims of the same generation, and
    ``UNIQUE (resource_key, idempotency_key)`` makes a replay return the stored
    receipt. A replay that carries a different ``plan_digest`` is a conflict and
    must never overwrite the stored row.

    ``outcome``/``receipt_digest`` stay NULL only while ``phase = 'applying'``:
    the claim inserts the row, the terminal transition fills the receipt digest
    over plan digest, idempotency key, generation and outcome.
    """

    __tablename__ = "runtime_materialization_attempts"
    __table_args__ = (
        UniqueConstraint("resource_key", "idempotency_key", name="uq_runtime_attempt_resource_idempotency"),
        UniqueConstraint("resource_key", "generation", name="uq_runtime_attempt_resource_generation"),
        CheckConstraint("generation >= 1", name="ck_runtime_attempt_generation_positive"),
        CheckConstraint("observed_generation >= 0", name="ck_runtime_attempt_observed_generation_nonnegative"),
        CheckConstraint(
            "generation = observed_generation + 1",
            name="ck_runtime_attempt_single_generation_increment",
        ),
        CheckConstraint(
            "phase IN ('applying', 'applied', 'failed', 'rolled_back')",
            name="ck_runtime_attempt_phase",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('applied', 'rolled_back', 'failed')",
            name="ck_runtime_attempt_outcome",
        ),
        CheckConstraint("(phase = 'applying') = (outcome IS NULL)", name="ck_runtime_attempt_open_outcome"),
        CheckConstraint(
            "phase <> 'applying' OR applied_revision_id IS NULL",
            name="ck_runtime_attempt_open_without_applied",
        ),
        CheckConstraint(
            "(outcome IN ('applied', 'rolled_back')) = (applied_revision_id IS NOT NULL)",
            name="ck_runtime_attempt_applied_revision_link",
        ),
        CheckConstraint("(phase = 'applying') = (receipt_digest IS NULL)", name="ck_runtime_attempt_receipt_state"),
        CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_attempt_receipt_digest",
        ),
        CheckConstraint("plan_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_attempt_plan_digest"),
        CheckConstraint(
            "rollback_of_generation IS NULL OR (rollback_of_generation >= 1 "
            "AND rollback_of_generation <= observed_generation)",
            name="ck_runtime_attempt_rollback_target",
        ),
        CheckConstraint(
            "resource_key <> '' AND resource_key = btrim(resource_key) AND resource_key !~ '[[:space:]]'",
            name="ck_runtime_attempt_resource_key_well_formed",
        ),
        CheckConstraint(
            "idempotency_key <> '' AND idempotency_key = btrim(idempotency_key)",
            name="ck_runtime_attempt_idempotency_key_well_formed",
        ),
        Index(
            "ix_runtime_materialization_attempts_resource_generation",
            "resource_key",
            text("generation DESC"),
        ),
        Index("ix_runtime_materialization_attempts_revision_generation", "revision_id", "generation"),
        Index("ix_runtime_materialization_attempts_project_outcome", "project_id", "outcome"),
        Index("ix_runtime_materialization_attempts_resource_id", "resource_id"),
        {
            "comment": (
                "Durable per-resource materialization attempt history: idempotency key, observed "
                "generation, one-generation increment, outcome and receipt digest."
            )
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("rma"))
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    resource_key: Mapped[str] = mapped_column(String(200), nullable=False)
    revision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("project_runtime_revisions.id", ondelete="SET NULL"), nullable=True
    )
    applied_revision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("project_runtime_revisions.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default="applying", server_default="applying")
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    receipt_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fence_token: Mapped[str] = mapped_column(String(128), nullable=False)
    rollback_of_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RuntimeMaterializationAuditRow(Base):
    """Append-only, redacted audit of runtime materialization transitions.

    The row carries only a redacted reason code, an opaque correlation id and
    digests — never secret values, raw payloads or credential-bearing references.
    Append-only semantics are enforced by the migration trigger that rejects
    UPDATE, DELETE and TRUNCATE; restricting the runtime writer role to INSERT is
    a privilege concern owned by the RLS/privileges revision, not by this model.

    Every foreign key is ``ON DELETE RESTRICT`` on purpose: an ``ON DELETE
    CASCADE``/``SET NULL`` action would itself be an UPDATE/DELETE against audit
    evidence and would be rejected by the append-only trigger halfway through the
    parent delete. ``RESTRICT`` makes the refusal explicit and immediate. The
    consequence is deliberate: once a resource has runtime audit history, its
    project cannot be hard-deleted. Soft delete and the application-owned
    lifecycle remain the supported path; a retention/purge executor for audit is
    out of scope for this revision.
    """

    __tablename__ = "runtime_materialization_audit"
    __table_args__ = (
        CheckConstraint("event ~ '^[a-z][a-z0-9_.-]{0,63}$'", name="ck_runtime_audit_event_code"),
        CheckConstraint("outcome ~ '^[a-z][a-z0-9_.-]{0,63}$'", name="ck_runtime_audit_outcome_code"),
        CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_runtime_audit_reason_code_redacted",
        ),
        CheckConstraint("generation >= 0", name="ck_runtime_audit_generation_nonnegative"),
        CheckConstraint("resource_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_audit_resource_digest"),
        CheckConstraint(
            "correlation_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_audit_correlation_digest",
        ),
        CheckConstraint("plan_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_audit_plan_digest"),
        CheckConstraint(
            "correlation_id <> '' AND correlation_id = btrim(correlation_id)",
            name="ck_runtime_audit_correlation_id_well_formed",
        ),
        Index("ix_runtime_materialization_audit_resource_recorded", "resource_key", "recorded_at"),
        Index("ix_runtime_materialization_audit_project_recorded", "project_id", "recorded_at"),
        Index("ix_runtime_materialization_audit_attempt_id", "attempt_id"),
        {
            "comment": (
                "Append-only redacted materialization audit (reason code + correlation id). "
                "UPDATE/DELETE/TRUNCATE are rejected by trigger; INSERT-only grants belong to "
                "the RLS/privileges revision."
            )
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("raud"))
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("resources.id", ondelete="RESTRICT"), nullable=True
    )
    resource_key: Mapped[str] = mapped_column(String(200), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("runtime_materialization_attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
