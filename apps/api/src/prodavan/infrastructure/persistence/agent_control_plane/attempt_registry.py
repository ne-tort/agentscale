"""Durable provider-attempt journal for Agent Run execution.

One row per provider attempt. The execution worker writes a row before calling
the provider and transitions it afterwards, so an ambiguous outcome is a
*recorded fact* rather than an inference from a missing row.

Why this table exists
---------------------
``application/agent_runs/attempts.py`` defines the journal contract
(``ProviderAttempt``, ``AttemptState``, the transition matrix, redacted
metadata) and refuses to operate without durable storage. Nothing in the
wave-1 schema could hold those rows: ``agent_runs`` has a single ``attempt``
counter and ``agent_control_plane_events`` is an aggregate event log, so
multiple attempts per run had nowhere to live.

Invariants encoded here
-----------------------
* ``attempt_id`` is the primary key and ``idempotency_key`` is unique: one
  ``(tenant, run, generation, attempt)`` tuple has exactly one row, so a retried
  worker cannot fork history.
* ``generation``/``attempt``/``sequence`` are positive; the fence only moves
  forward.
* ``finished_at`` cannot precede ``started_at``.
* ``state`` is constrained to the closed vocabulary of ``AttemptState``. The
  transition rules themselves stay in the application layer: a CHECK cannot
  express "``unknown`` may only be left by reconciliation" without duplicating
  the matrix, and duplicating it would create two sources of truth.
* ``metadata`` is redacted JSON. The journal stores digests only — never a
  prompt, payload, credential or raw provider request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base

ATTEMPT_STATES: tuple[str, ...] = (
    "started",
    "known_succeeded",
    "known_failed",
    "known_retryable",
    "unknown",
    "reconciled_succeeded",
    "reconciled_failed",
    "abandoned",
    "discarded_stale",
)


class AgentRunProviderAttemptRow(Base):
    """One durable provider attempt with redacted evidence."""

    __tablename__ = "agent_run_provider_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_agent_run_attempt_idempotency"),
        CheckConstraint("generation >= 1", name="ck_agent_run_attempt_generation_positive"),
        CheckConstraint("attempt >= 1", name="ck_agent_run_attempt_number_positive"),
        CheckConstraint("sequence >= 1", name="ck_agent_run_attempt_sequence_positive"),
        CheckConstraint(
            "metadata_dropped >= 0", name="ck_agent_run_attempt_metadata_dropped_nonnegative"
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_agent_run_attempt_finish_after_start",
        ),
        CheckConstraint(
            "state IN ("
            + ", ".join(f"'{state}'" for state in ATTEMPT_STATES)
            + ")",
            name="ck_agent_run_attempt_state_vocabulary",
        ),
        Index("ix_agent_run_attempts_run_generation", "run_id", "generation"),
        Index("ix_agent_run_attempts_state_deadline", "state", "deadline_at"),
        Index("ix_agent_run_attempts_tenant_started", "tenant_id", "started_at"),
    )

    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    lease_token_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    metadata_dropped: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ATTEMPT_STATES", "AgentRunProviderAttemptRow"]
