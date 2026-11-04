"""SQL implementation of :class:`~prodavan.application.agent_runs.lease.RunLeaseStore`.

Why this module exists
---------------------
``agent_run_leases`` (:mod:`.lease_registry`) owns *who* may advance a run and at
which fence generation; ``agent_runs`` owns *what* the run is and
``agent_control_plane_aggregates`` owns the committed aggregate sequence.  The
application tier declared the storage contract
(:class:`~prodavan.application.agent_runs.lease.RunLeaseStore`) but no host
implemented it, so every claim path failed closed.  This module is that
implementation and nothing else: no policy lives here.

Contract notes (the parts that are easy to get wrong)
----------------------------------------------------
* ``database_now`` is ``SELECT now()``.  Lease deadlines are only comparable
  inside one clock domain, and two API replicas do not share wall time.
* ``select_claim_candidate`` locks **at most one** eligible lease row with
  ``FOR UPDATE SKIP LOCKED`` and returns its snapshot.  The lock is only
  meaningful across the following ``cas_acquire``, so a caller that wants a
  real row lock (rather than a best-effort skip) wraps both calls in
  :meth:`SqlAlchemyRunLeaseStore.claim_scope`.
* every ``cas_*`` is exactly one ``UPDATE ... WHERE`` statement whose predicate
  contains the row identity, ``lease_generation`` and (for renew/release/park)
  the presented ``lease_token``/``lease_owner``, plus a status precondition via
  a correlated ``EXISTS`` on ``agent_runs``.  It returns the affected row count
  and never raises on ``0``: ``0`` is a lost race, which is the normal way a
  stale worker is made harmless.
* the status precondition is expressed against ``agent_runs.status`` (the row
  the control plane rewrites) **and** the candidate query additionally requires
  ``agent_control_plane_aggregates.status`` to agree with it.  A run without a
  committed aggregate cursor, or whose cursor disagrees with its run row, is
  never claimable: the claim is an ``agent_run.start`` compare-and-set on that
  cursor, so selecting such a run would only burn a claim.  There is no
  fallback — a missing cursor is a refusal, not a default.
* nothing here falls back to process memory and nothing writes a second table.
  A host without this store must raise
  :class:`~prodavan.application.agent_runs.lease.LeaseStorageUnavailable`.

Extensions beyond the protocol
------------------------------
Two idempotent single-statement operations are needed by the port adapter and
are *not* part of ``RunLeaseStore``; they are declared here and exposed through
:class:`RunLeaseFenceStore`:

* ``cas_park`` — clear the lease triple and optionally set ``retry_at`` while
  keeping ``lease_generation``.  Used when a worker parks a run for a retry or
  for reconciliation: the generation must **not** move, because the parked
  generation is the fence the reconciler uses.
* ``cas_fence`` — move ``lease_generation`` forward and drop the lease triple.
  Used by cancellation: every later write of the old worker is fenced out even
  if it still presents a valid-looking token.

``0`` affected rows for ``cas_fence`` is normal: a run that was never claimed
has no lease row at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.agent_runs.execution import AgentRunLease
from prodavan.application.agent_runs.lease import (
    CLAIMABLE_STATUSES,
    TERMINAL_STATUSES,
    WRITABLE_WHILE_RUNNING,
    LeaseSlot,
    RunLeaseStore,
)
from prodavan.domain.agent.control_plane import AgentRunStatus
from prodavan.infrastructure.persistence.agent_control_plane.lease_registry import (
    AgentRunLeaseRow,
)
from prodavan.infrastructure.persistence.agent_control_plane.models import (
    AgentControlPlaneAggregateRow,
)
from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow

RUN_AGGREGATE_TYPE = "agent_run"
"""``aggregate_type`` of the run cursor inside ``agent_control_plane_aggregates``."""

RELEASABLE_STATUSES: frozenset[str] = (
    CLAIMABLE_STATUSES | WRITABLE_WHILE_RUNNING | TERMINAL_STATUSES
)
"""Statuses a release may be presented with (queued/retry_due/running/terminal)."""

RENEWABLE_STATUSES: frozenset[str] = CLAIMABLE_STATUSES | WRITABLE_WHILE_RUNNING
"""Statuses a renewal may be presented with."""

PARKED_STATUSES: frozenset[str] = WRITABLE_WHILE_RUNNING
"""Statuses a parked (retry/reconcile) lease may be presented with."""


class RunLeaseFenceStore(RunLeaseStore, Protocol):
    """``RunLeaseStore`` plus the two fencing statements the port adapter needs.

    ``DurableRunLeaseManager`` only requires the base protocol; the execution
    adapter additionally requires ``cas_park`` (retry / unknown parking) and
    ``cas_fence`` (cancellation bumps the generation).  Both are single
    ``UPDATE ... WHERE`` statements returning the affected row count.
    """

    async def cas_park(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        retry_at: datetime | None,
    ) -> int: ...

    async def cas_fence(self, *, tenant_id: str, run_id: str, now: datetime) -> int: ...


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _required(value: str, name: str, *, max_length: int = 64) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _run_status_in(statuses: frozenset[str]) -> Any:
    """Correlated status precondition: the run must currently be in ``statuses``.

    Correlating on ``agent_run_leases.run_id`` keeps the statement a single
    ``UPDATE ... WHERE`` over the lease row instead of a read-then-write pair.
    """
    return exists(
        select(AgentRunRow.id).where(
            AgentRunRow.id == AgentRunLeaseRow.run_id,
            AgentRunRow.status.in_(tuple(sorted(statuses))),
        )
    )


def _slot_query() -> Any:
    lease = AgentRunLeaseRow
    run = AgentRunRow
    aggregate = AgentControlPlaneAggregateRow
    return (
        select(
            lease.run_id.label("run_id"),
            lease.tenant_id.label("tenant_id"),
            lease.project_id.label("project_id"),
            run.status.label("status"),
            aggregate.last_sequence.label("sequence"),
            lease.attempt.label("attempt"),
            lease.lease_generation.label("generation"),
            run.session_id.label("session_id"),
            lease.lease_owner.label("lease_owner"),
            lease.lease_token.label("lease_token"),
            lease.lease_until.label("lease_until"),
            lease.retry_at.label("retry_at"),
        )
        .join(run, run.id == lease.run_id)
        .join(
            aggregate,
            and_(
                aggregate.aggregate_type == RUN_AGGREGATE_TYPE,
                aggregate.aggregate_id == lease.run_id,
            ),
        )
    )


def _slot_from_row(row: Mapping[str, Any]) -> LeaseSlot:
    return LeaseSlot(
        run_id=row["run_id"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        status=row["status"],
        sequence=int(row["sequence"]),
        attempt=int(row["attempt"]),
        generation=int(row["generation"]),
        session_id=row["session_id"],
        lease_owner=row["lease_owner"],
        lease_token=row["lease_token"],
        lease_until=row["lease_until"],
        retry_at=row["retry_at"],
    )


class SqlAlchemyRunLeaseStore:
    """Durable lease store over ``agent_run_leases`` (+ run status and cursor).

    ``session_factory`` must point at the platform database that also holds
    ``agent_runs``, ``agent_control_plane_aggregates`` and ``agent_run_leases``.
    Every method is one short transaction unless the caller pinned one with
    :meth:`claim_scope`; there is no in-memory state and no second table.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._pinned: AsyncSession | None = None

    # -- transaction scopes -------------------------------------------------

    @asynccontextmanager
    async def claim_scope(self) -> AsyncIterator[None]:
        """Pin one database transaction across a claim (select → CAS).

        ``FOR UPDATE SKIP LOCKED`` only serialises two workers while the lock is
        held, so ``select_claim_candidate`` + ``cas_acquire`` must share a
        transaction for the lock to mean anything.  The transaction is committed
        when the block exits cleanly and rolled back on any exception, so a
        planning refusal (``LeaseRefused``) can never leave a lock or an open
        transaction behind.

        Nesting is refused: two pinned transactions on one store would let a
        caller commit half a claim.
        """
        if self._pinned is not None:
            raise RuntimeError("a claim scope is already active on this lease store")
        session = self._session_factory()
        self._pinned = session
        try:
            async with session.begin():
                yield
        finally:
            self._pinned = None
            await session.close()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """Join the pinned claim transaction when there is one, else open one."""
        pinned = self._pinned
        if pinned is not None:
            yield pinned
            return
        session = self._session_factory()
        async with session.begin():
            try:
                yield session
            finally:
                await session.close()

    # -- RunLeaseStore ------------------------------------------------------

    async def database_now(self) -> datetime:
        """Return ``SELECT now()`` — the only clock lease deadlines compare to."""
        async with self._session() as session:
            value = await session.scalar(select(func.now()))
        if value is None:
            raise RuntimeError("database clock did not return a timestamp")
        return _aware(value, "database_now")

    async def select_claim_candidate(self, *, now: datetime) -> LeaseSlot | None:
        """Lock at most one claimable lease row and return its snapshot.

        Eligibility is a single predicate: the run is ``queued``/``retry_due``,
        its committed cursor agrees with it and has a positive sequence, the
        retry window (if any) is open, and the previous lease (if any) has
        expired.  ``SKIP LOCKED`` means a run another worker already locked is
        skipped instead of waited for.
        """
        now = _aware(now, "now")
        lease = AgentRunLeaseRow
        run = AgentRunRow
        aggregate = AgentControlPlaneAggregateRow
        claimable = tuple(sorted(CLAIMABLE_STATUSES))
        statement = (
            _slot_query()
            .where(
                run.status.in_(claimable),
                aggregate.status == run.status,
                aggregate.last_sequence >= 1,
                lease.attempt >= 1,
                or_(lease.retry_at.is_(None), lease.retry_at <= now),
                or_(lease.lease_until.is_(None), lease.lease_until <= now),
            )
            .order_by(
                lease.retry_at.asc().nulls_first(),
                lease.lease_until.asc().nulls_first(),
                lease.run_id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True, of=lease)
        )
        async with self._session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        return None if row is None else _slot_from_row(row)

    async def read_slot(self, *, run_id: str) -> LeaseSlot | None:
        """Read one run's lease snapshot without locking or claiming anything."""
        run_id = _required(run_id, "run_id")
        statement = _slot_query().where(AgentRunLeaseRow.run_id == run_id)
        async with self._session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        return None if row is None else _slot_from_row(row)

    async def cas_acquire(
        self,
        *,
        slot: LeaseSlot,
        lease: AgentRunLease,
        now: datetime,
    ) -> int:
        """Take the lease for ``slot`` if it is still the generation we planned.

        Preconditions: same row, ``lease_generation`` unchanged, the attempt
        counter unchanged, the run still claimable, and neither the retry window
        nor a live lease in the way.  A concurrent claim (generation moved),
        cancellation (status moved) or a different attempt is a ``0``.
        """
        if slot.run_id != lease.run_id:
            raise ValueError("lease does not belong to the planned slot")
        if lease.generation != slot.generation + 1:
            raise ValueError("acquired lease generation must follow the observed generation")
        if lease.attempt != slot.attempt + 1:
            raise ValueError("acquired lease attempt must follow the observed attempt")
        now = _aware(now, "now")
        _aware(lease.lease_until, "lease_until")
        lease_row = AgentRunLeaseRow
        statement = (
            update(lease_row)
            .where(
                lease_row.run_id == slot.run_id,
                lease_row.tenant_id == slot.tenant_id,
                lease_row.lease_generation == slot.generation,
                lease_row.attempt == slot.attempt,
                or_(lease_row.retry_at.is_(None), lease_row.retry_at <= now),
                or_(lease_row.lease_until.is_(None), lease_row.lease_until <= now),
                _run_status_in(CLAIMABLE_STATUSES),
            )
            .values(
                lease_owner=lease.owner_id,
                lease_token=lease.lease_token,
                lease_until=lease.lease_until,
                lease_generation=lease.generation,
                attempt=lease.attempt,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        async with self._session() as session:
            result = await session.execute(statement)
        return int(result.rowcount or 0)

    async def cas_renew(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        lease_until: datetime,
    ) -> int:
        """Extend the deadline of a lease we still own; never resurrect one.

        The predicate requires the same generation, token and owner, a deadline
        that has not passed (``lease_until > now``) and does not move
        backwards, and a run status a fenced worker may hold.  Renewing after
        expiry is ``0`` — the run belongs to whoever claims it next.
        """
        now = _aware(now, "now")
        new_deadline = _aware(lease_until, "lease_until")
        if new_deadline <= now:
            raise ValueError("a renewal deadline must be after the database time")
        lease_row = AgentRunLeaseRow
        statement = (
            update(lease_row)
            .where(
                lease_row.run_id == lease.run_id,
                lease_row.tenant_id == lease.tenant_id,
                lease_row.lease_generation == lease.generation,
                lease_row.lease_token == lease.lease_token,
                lease_row.lease_owner == lease.owner_id,
                lease_row.lease_until.is_not(None),
                lease_row.lease_until > now,
                lease_row.lease_until <= new_deadline,
                _run_status_in(RENEWABLE_STATUSES),
            )
            .values(lease_until=new_deadline, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        async with self._session() as session:
            result = await session.execute(statement)
        return int(result.rowcount or 0)

    async def cas_release(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        status: str,
    ) -> int:
        """Drop the lease triple while keeping the generation.

        ``status`` is the run status the caller asserts the run is in for this
        release to be legitimate (``queued``/``retry_due``/``running``/terminal);
        anything else is refused before the statement is built.  Releasing back
        to ``queued`` also clears ``retry_at``, so the run is immediately due
        again; any other release keeps the parked retry window.

        Releasing keeps ``lease_generation``: the next claim increments it, so
        every write this worker could still attempt is already fenced out.
        """
        now = _aware(now, "now")
        target = _required(status, "status", max_length=32).lower()
        if target not in RELEASABLE_STATUSES:
            raise ValueError("release status is outside the releasable set")
        lease_row = AgentRunLeaseRow
        values: dict[str, Any] = {
            "lease_owner": None,
            "lease_token": None,
            "lease_until": None,
            "updated_at": now,
        }
        if target == AgentRunStatus.QUEUED.value:
            values["retry_at"] = None
        statement = (
            update(lease_row)
            .where(
                lease_row.run_id == lease.run_id,
                lease_row.tenant_id == lease.tenant_id,
                lease_row.lease_generation == lease.generation,
                lease_row.lease_token == lease.lease_token,
                lease_row.lease_owner == lease.owner_id,
                lease_row.lease_until.is_not(None),
                _run_status_in(frozenset({target})),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        async with self._session() as session:
            result = await session.execute(statement)
        return int(result.rowcount or 0)

    # -- fencing extensions -------------------------------------------------

    async def cas_park(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        retry_at: datetime | None,
    ) -> int:
        """Clear the lease triple and optionally set ``retry_at``; keep the fence.

        Parking is what ``schedule_retry`` and ``finish_unknown`` need: the
        worker stops holding the lease (so nothing is renewed for a run it is no
        longer driving) while ``lease_generation`` stays put, because that
        generation is the fence the reconciler/retry path will present.  The run
        status is deliberately untouched: the adapter must neither write a
        status without its control-plane event nor pretend an ambiguous outcome
        is terminal.
        """
        now = _aware(now, "now")
        if retry_at is not None:
            retry_at = _aware(retry_at, "retry_at")
        lease_row = AgentRunLeaseRow
        statement = (
            update(lease_row)
            .where(
                lease_row.run_id == lease.run_id,
                lease_row.tenant_id == lease.tenant_id,
                lease_row.lease_generation == lease.generation,
                lease_row.lease_token == lease.lease_token,
                lease_row.lease_owner == lease.owner_id,
                lease_row.lease_until.is_not(None),
                _run_status_in(PARKED_STATUSES),
            )
            .values(
                lease_owner=None,
                lease_token=None,
                lease_until=None,
                retry_at=retry_at,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        async with self._session() as session:
            result = await session.execute(statement)
        return int(result.rowcount or 0)

    async def cas_fence(self, *, tenant_id: str, run_id: str, now: datetime) -> int:
        """Move ``lease_generation`` forward and drop any lease, for cancellation.

        Cancellation is performed by an actor that does not hold the lease, so
        this is the one statement that is *not* token-scoped; it is tenant-scoped
        instead.  ``0`` rows means the run had no lease row at all (it was never
        claimed), which is a normal pre-cancel state rather than an error.
        """
        tenant_id = _required(tenant_id, "tenant_id")
        run_id = _required(run_id, "run_id")
        now = _aware(now, "now")
        lease_row = AgentRunLeaseRow
        statement = (
            update(lease_row)
            .where(
                lease_row.run_id == run_id,
                lease_row.tenant_id == tenant_id,
            )
            .values(
                lease_generation=lease_row.lease_generation + 1,
                lease_owner=None,
                lease_token=None,
                lease_until=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        async with self._session() as session:
            result = await session.execute(statement)
        return int(result.rowcount or 0)


__all__ = [
    "PARKED_STATUSES",
    "RELEASABLE_STATUSES",
    "RENEWABLE_STATUSES",
    "RUN_AGGREGATE_TYPE",
    "RunLeaseFenceStore",
    "SqlAlchemyRunLeaseStore",
]
