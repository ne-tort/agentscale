"""Durable Agent Run lease tier: claim, renewal, release and generation fencing.

Why this module exists
----------------------
``application/agent_runs/execution.py`` declares the fenced execution contract
(:class:`~prodavan.application.agent_runs.execution.AgentRunExecutionPort`) but
nothing implements its lease half.  This module owns the *policy* of that half
in one place so that any future SQL adapter cannot re-invent (and get wrong)
the rules that keep two workers from publishing the same run:

* only ``queued`` and ``retry_due`` runs are claimable, and only when they are
  due and not already leased;
* every claim mints a fresh random lease token and increments the run's
  monotonic ``generation``; cancellation and re-claim both move the generation
  forward, so an old worker's token can never match again;
* the lease deadline is derived from the **database** clock, never the API
  process clock, because two replicas do not share wall time;
* every write is a single-row compare-and-set.  ``0`` affected rows means the
  caller lost the race — that is a *normal result*, not an exception, and it is
  what makes a stale generation harmless;
* releasing a lease (graceful shutdown, undeliverable work) keeps the
  generation, so the next claim is still ordered after every write the old
  worker could have made.

Fail-closed rules encoded here
------------------------------
* A lease with no generation, no token, an unknown status or a never-observed
  deadline is refused (:class:`LeaseRefused`) rather than treated as valid.
* A renewal after the deadline is refused instead of resurrecting the lease.
* A status outside the statuses the caller is allowed to write is refused, so a
  worker cannot finish a run that another transition already moved.
* Nothing in this module degrades to process memory: hosts without a durable
  lease store must raise :class:`LeaseStorageUnavailable` with an explicit
  blocker code (see ``LEASE_STORAGE_BLOCKER``).

Blockers (see the task report; this module deliberately does not create tables)
-------------------------------------------------------------------------------
* ``agent_runs`` has no ``lease_generation``/``lease_token``/``lease_until``/
  ``available_at`` columns today, and this task's file zone excludes ORM and
  migration edits.  ``RunLeaseStore`` is the seam: the infrastructure zone must
  add the columns + constraints and implement the four CAS methods with one
  ``SELECT ... FOR UPDATE SKIP LOCKED`` claim and database time.
* A claim must also emit ``agent_run.start`` (state + event + outbox + queue
  intent) in the *same* transaction as the lease acquisition.  A pure lease
  store cannot write control-plane events, so the port adapter must compose the
  lease CAS with ``DurableAgentControlPlane.start_run``; ``LeaseSlot.sequence``
  is the observed aggregate sequence and the returned lease carries
  ``sequence + 1`` for the start transition the claim commits.
* ``agent_run_requeue_transition_unwired`` — releasing a claimed run back to
  ``queued`` (graceful shutdown, or a reconciler taking over an expired lease)
  is not an allowed control-plane transition today: the repository only accepts
  ``agent_run.start/fail/cancel/timeout/...`` with their exact source statuses.
  ``plan_release`` therefore describes a lease-tier release that must be
  accompanied by an ``agent_run.requeue`` event in the same transaction; until
  that operation exists the worker keeps the lease and lets the TTL expire.
* Claiming deliberately covers only ``queued``/``retry_due``.  A run left
  ``running`` behind an expired lease cannot be taken over without the requeue
  transition above, so crash recovery of an orphaned lease is a reconciler
  responsibility, not a claim-side shortcut.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from prodavan.domain.agent.control_plane import AgentRunStatus

from .execution import AgentRunLease
from .service import EXECUTION_BLOCKERS

LEASE_BLOCKER = EXECUTION_BLOCKERS["run_claim"]
"""Blocker surfaced while no durable claim/lease adapter is wired."""

LEASE_STORAGE_BLOCKER = "agent_run_lease_storage_missing"
"""Blocker for a host that has no durable lease columns/store."""

LEASE_REQUEUE_BLOCKER = "agent_run_requeue_transition_unwired"
"""Blocker for releasing a claimed run back to ``queued`` (needs a requeue event)."""

RETRY_DUE_STATUS = "retry_due"
"""Reserved durable status for a run whose retry window has opened.

``AgentRunStatus`` has no retry member yet (the durable retry scheduler is a
separate migration slice).  The lease tier accepts the reserved value so the
scheduler can land without changing claim semantics, but it never *writes* it:
only the retry scheduler may move a run into ``retry_due``.
"""

CLAIMABLE_STATUSES: frozenset[str] = frozenset(
    {AgentRunStatus.QUEUED.value, RETRY_DUE_STATUS}
)
"""The only statuses a worker may claim (plus the due/deadline predicates)."""

WRITABLE_WHILE_RUNNING: frozenset[str] = frozenset({AgentRunStatus.RUNNING.value})
"""Default set of statuses a claimed worker may transition out of."""

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        AgentRunStatus.SUCCEEDED.value,
        AgentRunStatus.FAILED.value,
        AgentRunStatus.CANCELLED.value,
    }
)

DEFAULT_LEASE_TTL = timedelta(seconds=60)
_MIN_LEASE_TTL = timedelta(seconds=5)
_MAX_LEASE_TTL = timedelta(hours=1)


class LeaseRefusal(StrEnum):
    """Stable, redacted reasons why a lease operation is refused."""

    NOT_CLAIMABLE = "agent_run_lease_not_claimable"
    NOT_LEASED = "agent_run_lease_absent"
    EXPIRED = "agent_run_lease_expired"
    GENERATION_STALE = "agent_run_lease_generation_stale"
    TOKEN_MISMATCH = "agent_run_lease_token_mismatch"
    OWNER_MISMATCH = "agent_run_lease_owner_mismatch"
    STATUS_CONFLICT = "agent_run_status_conflict"
    SEQUENCE_CONFLICT = "agent_run_sequence_conflict"
    DEADLINE_INVALID = "agent_run_lease_deadline_invalid"
    GENERATION_NOT_MONOTONIC = "agent_run_generation_not_monotonic"
    STORAGE_MISSING = LEASE_STORAGE_BLOCKER


class LeaseRefused(RuntimeError):
    """A lease operation was refused; the caller must not write run state."""

    def __init__(self, reason: LeaseRefusal, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


class LeaseStorageUnavailable(RuntimeError):
    """No durable lease store exists; claiming must fail instead of degrading."""

    def __init__(self, detail: str = "durable agent run lease store is not wired") -> None:
        self.blocker = LEASE_STORAGE_BLOCKER
        super().__init__(detail)


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _required(value: str, name: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def new_lease_token() -> str:
    """Return an unguessable lease token (never derived from time or IDs)."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True, slots=True)
class CasUpdate:
    """Result of one single-row compare-and-set.

    ``0`` rows is a lost race, not an error: another worker, a cancellation or
    a reconciler won first.  ``1`` row is the applied case.  Anything else is an
    invariant violation (a per-run CAS can never touch two rows).
    """

    rows: int

    def __post_init__(self) -> None:
        if isinstance(self.rows, bool) or not isinstance(self.rows, int):
            raise ValueError("CAS row count must be an int")
        if self.rows < 0:
            raise ValueError("CAS row count cannot be negative")
        if self.rows > 1:
            raise ValueError("a single-row CAS cannot affect more than one row")

    @classmethod
    def matched(cls) -> CasUpdate:
        return cls(1)

    @classmethod
    def lost(cls) -> CasUpdate:
        return cls(0)

    @property
    def applied(self) -> bool:
        return self.rows == 1

    @property
    def lost_race(self) -> bool:
        return self.rows == 0


@dataclass(frozen=True, slots=True)
class LeasePolicy:
    """Lease duration used for claims and renewals."""

    ttl: timedelta = DEFAULT_LEASE_TTL

    def __post_init__(self) -> None:
        if not isinstance(self.ttl, timedelta):
            raise ValueError("lease ttl must be a timedelta")
        if self.ttl < _MIN_LEASE_TTL or self.ttl > _MAX_LEASE_TTL:
            raise ValueError("lease ttl is outside the supported range")

    def deadline(self, now: datetime) -> datetime:
        _aware(now, "now")
        return now + self.ttl


@dataclass(frozen=True, slots=True)
class LeaseSlot:
    """Durable snapshot of one run's lease and progress counters.

    ``sequence`` is the run aggregate's committed ``last_sequence`` at read
    time — the CAS precondition for the transition the claim commits.
    ``retry_at`` is the durable "not before" timestamp of a retryable failure
    (``None`` for a fresh run).
    """

    run_id: str
    tenant_id: str
    project_id: str
    status: str
    sequence: int
    attempt: int
    generation: int
    session_id: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id", max_length=64))
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=64))
        status = _required(str(self.status), "status", max_length=32).lower()
        known = {member.value for member in AgentRunStatus} | {RETRY_DUE_STATUS}
        if status not in known:
            raise LeaseRefused(
                LeaseRefusal.STATUS_CONFLICT,
                "run status is not a durable Agent Run status",
            )
        object.__setattr__(self, "status", status)
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _required(self.session_id, "session_id", max_length=64))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise LeaseRefused(
                LeaseRefusal.SEQUENCE_CONFLICT,
                "run aggregate sequence must be a positive integer",
            )
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        lease_fields = (self.lease_owner, self.lease_token, self.lease_until)
        if any(value is not None for value in lease_fields) and not all(
            value is not None for value in lease_fields
        ):
            raise ValueError("lease owner, token and deadline must be present together")
        if self.lease_owner is not None:
            object.__setattr__(self, "lease_owner", _required(self.lease_owner, "lease_owner"))
            object.__setattr__(self, "lease_token", _required(self.lease_token or "", "lease_token"))
            object.__setattr__(self, "lease_until", _aware(self.lease_until, "lease_until"))  # type: ignore[arg-type]
        if self.retry_at is not None:
            object.__setattr__(self, "retry_at", _aware(self.retry_at, "retry_at"))

    @property
    def holds_lease(self) -> bool:
        return self.lease_owner is not None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def lease_live(self, now: datetime) -> bool:
        _aware(now, "now")
        return self.lease_until is not None and self.lease_until > now


def is_claimable(slot: LeaseSlot, *, now: datetime) -> bool:
    """Return whether a worker may claim this run right now."""
    _aware(now, "now")
    if slot.status not in CLAIMABLE_STATUSES:
        return False
    if slot.retry_at is not None and slot.retry_at > now:
        return False
    if slot.holds_lease and slot.lease_live(now):
        return False
    return True


@dataclass(frozen=True, slots=True)
class LeaseAcquisition:
    """A planned claim plus the CAS preconditions observed before it."""

    lease: AgentRunLease
    observed_generation: int
    observed_status: str
    observed_sequence: int


def plan_acquisition(
    slot: LeaseSlot,
    *,
    owner_id: str,
    now: datetime,
    lease_until: datetime,
    token: str | None = None,
) -> LeaseAcquisition:
    """Build the lease for one claim without touching storage.

    The returned lease carries the aggregate sequence **the claim commits**:
    a claim is not a read-only observation, it is the ``agent_run.start``
    transition (``queued``/``retry_due`` -> ``running``), which advances the run
    aggregate by exactly one.  The adapter must therefore write the lease and
    the start event in one transaction, and every later write uses
    ``lease.sequence`` as its precondition.

    Raises :class:`LeaseRefused` when the run is not claimable, so a candidate
    that raced out of eligibility is a refusal instead of a best-effort write.
    """
    _aware(now, "now")
    _aware(lease_until, "lease_until")
    owner = _required(owner_id, "owner_id")
    if lease_until <= now:
        raise LeaseRefused(
            LeaseRefusal.DEADLINE_INVALID,
            "lease deadline must be after the database time of the claim",
        )
    if not is_claimable(slot, now=now):
        raise LeaseRefused(
            LeaseRefusal.NOT_CLAIMABLE,
            "run is not queued/retry_due, is not due, or is already leased",
        )
    generation = slot.generation + 1
    lease = AgentRunLease(
        tenant_id=slot.tenant_id,
        project_id=slot.project_id,
        run_id=slot.run_id,
        session_id=slot.session_id,
        owner_id=owner,
        lease_token=token or new_lease_token(),
        generation=generation,
        attempt=slot.attempt + 1,
        sequence=slot.sequence + 1,
        lease_until=lease_until,
    )
    return LeaseAcquisition(
        lease=lease,
        observed_generation=slot.generation,
        observed_status=slot.status,
        observed_sequence=slot.sequence,
    )


def verify_lease(
    slot: LeaseSlot | None,
    *,
    lease: AgentRunLease,
    now: datetime,
    allowed_statuses: frozenset[str] = WRITABLE_WHILE_RUNNING,
) -> None:
    """Raise :class:`LeaseRefused` unless ``lease`` still owns this run."""
    _aware(now, "now")
    if slot is None:
        raise LeaseRefused(LeaseRefusal.NOT_LEASED, "run row is missing")
    if not slot.holds_lease:
        raise LeaseRefused(LeaseRefusal.NOT_LEASED, "run holds no lease")
    if slot.generation != lease.generation:
        raise LeaseRefused(
            LeaseRefusal.GENERATION_STALE,
            f"run generation {slot.generation} supersedes lease generation {lease.generation}",
        )
    if slot.lease_token != lease.lease_token:
        raise LeaseRefused(LeaseRefusal.TOKEN_MISMATCH, "lease token does not match the run")
    if slot.lease_owner != lease.owner_id:
        raise LeaseRefused(LeaseRefusal.OWNER_MISMATCH, "lease owner does not match the run")
    if slot.lease_until is None or slot.lease_until <= now:
        raise LeaseRefused(LeaseRefusal.EXPIRED, "lease deadline has passed in database time")
    if slot.status not in allowed_statuses:
        raise LeaseRefused(
            LeaseRefusal.STATUS_CONFLICT,
            f"run status {slot.status!r} is not writable by a fenced worker",
        )


class LeaseVerdict(StrEnum):
    """Read-only conclusion about a lease, used by heartbeat and cancellation."""

    LIVE = "live"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    TERMINAL = "terminal"
    ABSENT = "absent"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class LeaseCheck:
    """Outcome of inspecting (and optionally renewing) one lease."""

    lease: AgentRunLease
    verdict: LeaseVerdict
    reason: LeaseRefusal | None = None
    observed: LeaseSlot | None = None
    renewed_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.verdict is LeaseVerdict.LIVE and self.reason is not None:
            raise ValueError("a live lease check cannot carry a refusal reason")
        if self.verdict is not LeaseVerdict.LIVE and self.reason is None:
            raise ValueError("a non-live lease check must carry a refusal reason")

    @property
    def holds(self) -> bool:
        return self.verdict is LeaseVerdict.LIVE

    @property
    def cancelled(self) -> bool:
        """True when a newer generation (cancel or re-claim) displaced the lease."""
        return self.verdict in {LeaseVerdict.SUPERSEDED, LeaseVerdict.TERMINAL}


def inspect_lease(slot: LeaseSlot | None, *, lease: AgentRunLease, now: datetime) -> LeaseCheck:
    """Classify a lease without writing anything."""
    _aware(now, "now")
    if slot is None:
        return LeaseCheck(lease, LeaseVerdict.ABSENT, LeaseRefusal.NOT_LEASED)
    if slot.is_terminal:
        return LeaseCheck(lease, LeaseVerdict.TERMINAL, LeaseRefusal.STATUS_CONFLICT, slot)
    if slot.generation != lease.generation or slot.lease_token != lease.lease_token:
        return LeaseCheck(lease, LeaseVerdict.SUPERSEDED, LeaseRefusal.GENERATION_STALE, slot)
    if slot.lease_until is None or slot.lease_until <= now:
        return LeaseCheck(lease, LeaseVerdict.EXPIRED, LeaseRefusal.EXPIRED, slot)
    return LeaseCheck(lease, LeaseVerdict.LIVE, None, slot)


def plan_renewal(
    slot: LeaseSlot | None,
    *,
    lease: AgentRunLease,
    now: datetime,
    lease_until: datetime,
) -> LeaseCheck:
    """Plan a renewal that keeps generation/attempt/token but extends the deadline.

    A renewal never shortens the current deadline and never resurrects an
    expired lease: a heartbeat that arrives after ``lease_until`` must lose the
    run to a fresh claim, not silently take it back.
    """
    _aware(now, "now")
    _aware(lease_until, "lease_until")
    verify_lease(slot, lease=lease, now=now, allowed_statuses=CLAIMABLE_STATUSES | WRITABLE_WHILE_RUNNING)
    assert slot is not None  # verify_lease raised otherwise
    deadline = max(lease_until, slot.lease_until or lease_until)
    if deadline <= now:
        raise LeaseRefused(LeaseRefusal.DEADLINE_INVALID, "renewed deadline must be in the future")
    return LeaseCheck(lease, LeaseVerdict.LIVE, None, slot, deadline)


@dataclass(frozen=True, slots=True)
class LeaseRelease:
    """A planned, fenced release of a lease back to the queue.

    ``requires_event`` is always true: the lease tier cannot write a run status
    without the matching control-plane event, so an adapter may only honour a
    release together with an ``agent_run.requeue`` transition
    (:data:`LEASE_REQUEUE_BLOCKER`).
    """

    lease: AgentRunLease
    status: str
    observed_sequence: int
    requires_event: bool = True


def plan_release(
    slot: LeaseSlot | None,
    *,
    lease: AgentRunLease,
    now: datetime,
) -> LeaseRelease:
    """Plan releasing an owned lease back to ``queued`` (generation preserved).

    Releasing keeps the generation: the next claim increments it, so any write
    this worker could still attempt is already fenced out.
    """
    _aware(now, "now")
    verify_lease(slot, lease=lease, now=now, allowed_statuses=CLAIMABLE_STATUSES | WRITABLE_WHILE_RUNNING)
    assert slot is not None
    return LeaseRelease(
        lease=lease,
        status=AgentRunStatus.QUEUED.value,
        observed_sequence=slot.sequence,
    )


class RunLeaseStore(Protocol):
    """Durable lease storage implemented by the infrastructure zone.

    Contract for every implementation:

    * ``database_now`` returns the database clock (``SELECT now()``), because
      lease deadlines are only comparable inside one clock domain;
    * ``select_claim_candidate`` locks at most one eligible row with
      ``FOR UPDATE SKIP LOCKED`` using the ``CLAIMABLE_STATUSES``/due/deadline
      predicate and returns its snapshot;
    * every ``cas_*`` method is exactly one ``UPDATE ... WHERE`` statement whose
      predicate includes the row identity, ``lease_generation`` and (for renew
      and release) the presented ``lease_token``, plus the status precondition;
      it returns the affected row count and must never raise on ``0``;
    * no implementation may fall back to process memory or a second table.
    """

    async def database_now(self) -> datetime: ...

    async def select_claim_candidate(self, *, now: datetime) -> LeaseSlot | None: ...

    async def cas_acquire(
        self,
        *,
        slot: LeaseSlot,
        lease: AgentRunLease,
        now: datetime,
    ) -> int: ...

    async def cas_renew(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        lease_until: datetime,
    ) -> int: ...

    async def cas_release(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        status: str,
    ) -> int: ...

    async def read_slot(self, *, run_id: str) -> LeaseSlot | None: ...


class DurableRunLeaseManager:
    """Lease half of the execution port, expressed over ``RunLeaseStore``.

    The manager never raises on a lost race and never trusts the process clock.
    The port adapter composes these calls with the control-plane transaction so
    that acquisition also writes ``agent_run.start`` and its event.
    """

    def __init__(self, *, store: RunLeaseStore, policy: LeasePolicy | None = None) -> None:
        if not callable(getattr(store, "cas_acquire", None)):
            raise TypeError("store must implement the RunLeaseStore contract")
        self._store = store
        self._policy = policy or LeasePolicy()

    async def acquire_next(self, *, owner_id: str) -> AgentRunLease | None:
        owner = _required(owner_id, "owner_id")
        now = _aware(await self._store.database_now(), "database_now")
        slot = await self._store.select_claim_candidate(now=now)
        if slot is None:
            return None
        try:
            acquisition = plan_acquisition(
                slot,
                owner_id=owner,
                now=now,
                lease_until=self._policy.deadline(now),
            )
        except LeaseRefused:
            # The candidate stopped being eligible between SELECT and planning.
            return None
        update = CasUpdate(
            await self._store.cas_acquire(slot=slot, lease=acquisition.lease, now=now)
        )
        if update.lost_race:
            return None
        return acquisition.lease

    async def inspect(self, *, lease: AgentRunLease) -> LeaseCheck:
        now = _aware(await self._store.database_now(), "database_now")
        slot = await self._store.read_slot(run_id=lease.run_id)
        return inspect_lease(slot, lease=lease, now=now)

    async def renew(self, *, lease: AgentRunLease) -> LeaseCheck:
        now = _aware(await self._store.database_now(), "database_now")
        slot = await self._store.read_slot(run_id=lease.run_id)
        try:
            planned = plan_renewal(
                slot,
                lease=lease,
                now=now,
                lease_until=self._policy.deadline(now),
            )
        except LeaseRefused as refused:
            verdict = (
                LeaseVerdict.EXPIRED
                if refused.reason is LeaseRefusal.EXPIRED
                else LeaseVerdict.SUPERSEDED
                if refused.reason in {LeaseRefusal.GENERATION_STALE, LeaseRefusal.TOKEN_MISMATCH}
                else LeaseVerdict.REFUSED
            )
            return LeaseCheck(lease, verdict, refused.reason, slot)
        assert planned.renewed_until is not None
        update = CasUpdate(
            await self._store.cas_renew(
                lease=lease,
                now=now,
                lease_until=planned.renewed_until,
            )
        )
        if update.lost_race:
            return LeaseCheck(lease, LeaseVerdict.SUPERSEDED, LeaseRefusal.GENERATION_STALE, slot)
        return planned

    async def release(self, *, lease: AgentRunLease) -> CasUpdate:
        now = _aware(await self._store.database_now(), "database_now")
        slot = await self._store.read_slot(run_id=lease.run_id)
        plan = plan_release(slot, lease=lease, now=now)
        return CasUpdate(
            await self._store.cas_release(lease=lease, now=now, status=plan.status)
        )


__all__ = [
    "CLAIMABLE_STATUSES",
    "CasUpdate",
    "DEFAULT_LEASE_TTL",
    "DurableRunLeaseManager",
    "LEASE_BLOCKER",
    "LEASE_REQUEUE_BLOCKER",
    "LEASE_STORAGE_BLOCKER",
    "LeaseAcquisition",
    "LeaseCheck",
    "LeasePolicy",
    "LeaseRefusal",
    "LeaseRefused",
    "LeaseRelease",
    "LeaseSlot",
    "LeaseStorageUnavailable",
    "LeaseVerdict",
    "RETRY_DUE_STATUS",
    "RunLeaseStore",
    "TERMINAL_STATUSES",
    "WRITABLE_WHILE_RUNNING",
    "inspect_lease",
    "is_claimable",
    "new_lease_token",
    "plan_acquisition",
    "plan_release",
    "plan_renewal",
    "verify_lease",
]
