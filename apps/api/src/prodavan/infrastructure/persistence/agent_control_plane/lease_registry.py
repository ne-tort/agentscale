"""Durable per-run lease state for the Agent Run execution worker.

The ``agent_runs`` row records *what* a run is; this table records *who* is
currently allowed to advance it and at which fence generation.  The execution
contract in :mod:`prodavan.application.agent_runs.execution` compares
``generation``, ``lease_token`` and the aggregate sequence on every transition,
so the fence has to be durable: process memory cannot fence a second worker on
another replica.

Design notes
------------
* ``run_id`` is the primary key.  A run has at most one live lease, so the
  claim query can lock exactly one row with ``FOR UPDATE SKIP LOCKED`` without
  contending on the ``agent_runs`` row itself.
* ``tenant_id``/``project_id`` are denormalised from the run so the claim
  predicate and the tenant policy on this table stay index-only; they are not
  an authorization anchor (authorization still reads the run aggregate).
* The lease triple (``lease_owner``/``lease_token``/``lease_until``) is either
  fully absent (idle) or fully present (held).  A partial triple would let a
  reader treat a half-released row as live.
* ``lease_generation`` is monotonic and never decremented.  Cancellation and
  stale-worker fencing both rely on "the fence only moves forward".
* ``retry_at`` is the durable "not before" time for a retryable failure.  It is
  deliberately separate from ``lease_until``: a retry is not a lease.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


class AgentRunLeaseRow(Base):
    """Current lease fence and progress counters for one Agent Run."""

    __tablename__ = "agent_run_leases"
    __table_args__ = (
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_token IS NULL)",
            name="ck_agent_run_lease_token_pair",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_agent_run_lease_until_pair",
        ),
        CheckConstraint("lease_generation >= 0", name="ck_agent_run_lease_generation_nonnegative"),
        CheckConstraint("attempt >= 1", name="ck_agent_run_lease_attempt_positive"),
        Index("ix_agent_run_leases_claimable", "retry_at", "lease_until"),
        Index("ix_agent_run_leases_tenant_project", "tenant_id", "project_id"),
    )

    run_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["AgentRunLeaseRow"]
