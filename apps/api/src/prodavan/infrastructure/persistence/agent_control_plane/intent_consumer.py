"""Durable SQL consumer for ``agent_control_plane_enqueue_intents``.

This adapter is the host wiring the persistence zone was waiting for: a real
consumer that claims the durable handoff rows written in the same transaction as
a control-plane state transition, its domain event, and its outbox projection.

State machine (one row per ``(event_id, queue_name)``)::

    pending ──claim──▶ claimed ──reserve──▶ dispatching ──record──▶ delivered
       ▲                  │                     │                      │
       │                  │ lease expires       │ lease expires        ▼
       └──retry (record)──┘                     ▼                  (terminal)
                                          failed / quarantined

    failed ──available_at due──▶ claimable again (bounded by attempts)

Guarantees
----------
*Two workers never take one intent.*  Claiming selects candidates with
``FOR UPDATE SKIP LOCKED`` inside the same transaction that bumps
``lease_generation``/``lease_token``/``lease_until``.  A row locked by another
worker is skipped, not waited on.

*Time comes from the database.*  ``available_at`` and ``lease_until`` eligibility
is compared against the database clock through :class:`DatabaseClockPort`; the
adapter's default implementation reads it from the database.  A worker with a
skewed process clock therefore cannot steal a live lease or resurrect an expired
one early.

*Every later write is fenced.*  ``reserve``/``record_outcome`` require
``state``, ``lease_owner``, ``lease_token`` **and** a live ``lease_until``.  A
worker whose lease expired or was replaced gets ``LEASE_LOST``/``False`` and
drops its result instead of overwriting the new owner's transition.  That is the
protection behind "two simultaneous workers".

*Duplicate delivery is structurally impossible and additionally visible.*
``queue_name + deduplication_key`` is unique, and the deduplication key is the
aggregate identity plus sequence (``tenant:aggregate_type:aggregate_id:version``),
so one aggregate version has exactly one intent row.  ``delivered_at`` is a
monotonic marker that is never cleared: re-delivering an already delivered
intent answers ``ALREADY_DELIVERED`` and never re-runs a handler.  A row that
left ``claimed`` for ``dispatching`` and then lost its lease has an *unknown*
outcome; it is quarantined, never blind-retried.

*Outcome, transition and audit are one write.*  ``record_outcome`` is a single
guarded ``UPDATE`` that writes the new state, the retry time and the redacted
attempt record together, so a committed transition can never lack its evidence
and evidence can never describe a transition that did not happen.

*Audit is redacted.*  The attempt record is a ``key=value`` line built only from
closed reason codes and counters.  Event payloads, failure messages, session and
trace IDs, and exception strings never reach the row.

Schema note
-----------
Revision ``2026110202`` has no dedicated dispatch-audit table, and this task may
not author a revision.  The per-attempt audit record therefore lives in the
existing ``agent_control_plane_enqueue_intents.last_error`` column with a fixed
redacted vocabulary.  Promoting a first-class append-only dispatch-audit table is
reported as a blocker; nothing here claims it already exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.agent_runs.dispatcher import (
    APPROVAL_AGGREGATE_TYPE,
    RUN_AGGREGATE_TYPE,
    ClaimedIntent,
    EnqueueIntentDispatcher,
    IntentDispatchAction,
    IntentDispatchOutcome,
    IntentDispatchReason,
    IntentKind,
    IntentScopeViolation,
    ReservationStatus,
    ResolvedIntentTarget,
    build_execution_handlers,
    classify_intent_kind,
)
from prodavan.application.agent_runs.execution import AgentRunExecutionPort
from prodavan.domain.agent.control_plane import AgentRunStatus
from prodavan.infrastructure.persistence.models.agent_control_plane import (
    AgentRunRow,
    ApprovalRequestRow,
)

from .models import (
    AgentControlPlaneAggregateRow,
    AgentControlPlaneEnqueueIntentRow,
    AgentControlPlaneEventRow,
    AgentControlPlaneRunScopeRow,
)

#: Intent lifecycle states written by this consumer (see the module docstring).
INTENT_STATE_PENDING: Final = "pending"
INTENT_STATE_CLAIMED: Final = "claimed"
INTENT_STATE_DISPATCHING: Final = "dispatching"
INTENT_STATE_DELIVERED: Final = "delivered"
INTENT_STATE_FAILED: Final = "failed"

#: Only ``pending`` and ``failed`` rows are eligible for a new claim.  In
#: particular ``dispatching`` is *not* claimable: its handler result is unknown
#: until :meth:`SqlAlchemyIntentConsumer.release_expired` quarantines it.
INTENT_STATES_CLAIMABLE: Final[tuple[str, ...]] = (INTENT_STATE_PENDING, INTENT_STATE_FAILED)
#: States in which the dispatcher may still record an outcome for its own claim.
INTENT_STATES_OWNED: Final[tuple[str, ...]] = (INTENT_STATE_CLAIMED, INTENT_STATE_DISPATCHING)

_DEFAULT_LEASE_SECONDS: Final = 30.0
_MAX_LEASE_SECONDS: Final = 900.0
_REDACTED_RECORD_MAX: Final = 400
_OWNER_MAX: Final = 128


class DispatchRecordAction(StrEnum):
    """Actions that may appear in a redacted attempt record."""

    CLAIM = "claim"
    RESERVE = "reserve"
    DELIVER = "deliver"
    RETRY = "retry"
    REJECT = "reject"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class IntentConsumerSettings:
    """Lease policy expressed as a duration, never as a wall-clock instant."""

    lease_seconds: float = _DEFAULT_LEASE_SECONDS
    max_attempts: int = 8

    def __post_init__(self) -> None:
        if not 0 < self.lease_seconds <= _MAX_LEASE_SECONDS:
            raise ValueError(f"lease_seconds must be in (0, {_MAX_LEASE_SECONDS}]")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


class DatabaseClockPort(Protocol):
    """Read "now" from the database, never from the worker process."""

    async def now(self, session: AsyncSession) -> datetime: ...


class SqlDatabaseClock:
    """Default clock: ``now()`` inside the caller's transaction.

    PostgreSQL returns a timezone-aware ``timestamptz``.  A database that returns
    a naive timestamp (SQLite in acceptance runs) is coerced to UTC so lease
    comparisons stay total and explicit rather than silently naive.  ``now()`` is
    transaction start time, which is what a lease decision must be measured
    against: the whole claim is judged at one instant.
    """

    async def now(self, session: AsyncSession) -> datetime:
        from datetime import UTC

        value = await session.scalar(select(func.now()))
        if isinstance(value, str):
            # SQLite compiles now() to CURRENT_TIMESTAMP and returns text.
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp for now()")
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value


def redacted_attempt_record(
    *,
    action: DispatchRecordAction,
    reason: IntentDispatchReason | None = None,
    kind: IntentKind | None = None,
    attempts: int | None = None,
) -> str:
    """Build the only text this consumer ever stores about an attempt.

    Fixed vocabulary: an action name, a closed reason code, a kind name and a
    counter.  No payload, message, identifier or exception text can enter here.
    """
    parts = [f"action={action.value}"]
    if reason is not None:
        parts.append(f"reason={reason.value}")
    if kind is not None:
        parts.append(f"kind={kind.value}")
    if attempts is not None:
        parts.append(f"attempts={int(attempts)}")
    return " ".join(parts)[:_REDACTED_RECORD_MAX]


class IntentConsumerIntegrityError(RuntimeError):
    """Persisted rows contradict the intent contract; nothing was delivered."""


class SqlAlchemyIntentConsumer:
    """Claim, reserve and record durable enqueue intents.

    Each method opens its own short transaction, so a handler can run for a
    while without holding a row lock on the intent table.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue_name: str,
        settings: IntentConsumerSettings | None = None,
        clock: DatabaseClockPort | None = None,
    ) -> None:
        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must not be empty")
        self._session_factory = session_factory
        self._queue_name = queue_name.strip()
        self._settings = settings or IntentConsumerSettings()
        self._clock = clock or SqlDatabaseClock()

    @property
    def queue_name(self) -> str:
        return self._queue_name

    @property
    def settings(self) -> IntentConsumerSettings:
        return self._settings

    # ---------------------------------------------------------------- claiming

    async def claim(self, *, owner_id: str, limit: int) -> Sequence[ClaimedIntent]:
        """Lease up to ``limit`` due intents; ``SKIP LOCKED`` keeps workers apart."""
        owner = _require_owner(owner_id)
        if limit < 1:
            raise ValueError("limit must be positive")
        if limit > 500:
            raise ValueError("limit must not exceed 500 intents per round")

        async with self._session_factory() as session:
            async with session.begin():
                db_now = await self._clock.now(session)
                rows = (
                    await session.scalars(
                        select(AgentControlPlaneEnqueueIntentRow)
                        .where(
                            AgentControlPlaneEnqueueIntentRow.queue_name == self._queue_name,
                            AgentControlPlaneEnqueueIntentRow.state.in_(INTENT_STATES_CLAIMABLE),
                            AgentControlPlaneEnqueueIntentRow.available_at <= db_now,
                            AgentControlPlaneEnqueueIntentRow.attempts < self._settings.max_attempts,
                            or_(
                                AgentControlPlaneEnqueueIntentRow.lease_until.is_(None),
                                AgentControlPlaneEnqueueIntentRow.lease_until <= db_now,
                            ),
                        )
                        .order_by(
                            AgentControlPlaneEnqueueIntentRow.available_at,
                            AgentControlPlaneEnqueueIntentRow.created_at,
                            AgentControlPlaneEnqueueIntentRow.event_id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                if not rows:
                    return ()

                lease_token = uuid.uuid4().hex
                lease_until = db_now + timedelta(seconds=self._settings.lease_seconds)
                for row in rows:
                    row.state = INTENT_STATE_CLAIMED
                    row.lease_owner = owner
                    row.lease_token = lease_token
                    row.lease_generation = int(row.lease_generation or 0) + 1
                    row.lease_until = lease_until
                    row.attempts = int(row.attempts or 0) + 1
                    # The attempt counter is the durable claim evidence; any
                    # previous redacted failure record stays until this attempt
                    # produces its own outcome.
                await session.flush()

                events = {
                    event.event_id: event
                    for event in (
                        await session.scalars(
                            select(AgentControlPlaneEventRow).where(
                                AgentControlPlaneEventRow.event_id.in_(
                                    [row.event_id for row in rows]
                                )
                            )
                        )
                    ).all()
                }
                claimed: list[ClaimedIntent] = []
                for row in rows:
                    event = events.get(row.event_id)
                    if event is None:
                        # The event row is the intent's only evidence of what it
                        # means.  Without it the intent cannot be dispatched and
                        # must not be guessed at.
                        raise IntentConsumerIntegrityError(
                            "enqueue intent references a missing domain event"
                        )
                    if not event.run_id:
                        raise IntentConsumerIntegrityError(
                            "enqueue intent references an event without a run"
                        )
                    claimed.append(
                        ClaimedIntent(
                            event_id=row.event_id,
                            queue_name=row.queue_name,
                            tenant_id=row.tenant_id,
                            project_id=event.project_id,
                            deduplication_key=row.deduplication_key,
                            event_type=event.event_type,
                            aggregate_type=event.aggregate_type,
                            aggregate_id=event.aggregate_id,
                            aggregate_version=event.sequence,
                            run_id=event.run_id,
                            session_id=event.session_id,
                            occurred_at=event.occurred_at,
                            payload=event.payload or {},
                            lease_owner=owner,
                            lease_token=lease_token,
                            lease_generation=row.lease_generation,
                            attempts=row.attempts,
                            claimed_at=db_now,
                            lease_until=lease_until,
                        )
                    )
                return tuple(claimed)

    # -------------------------------------------------------------- reservation

    async def reserve(
        self,
        *,
        intent: ClaimedIntent,
        kind: IntentKind,
        target: ResolvedIntentTarget,
    ) -> ReservationStatus:
        """Move the claimed intent to ``dispatching`` before the handler runs.

        This is the durable duplicate-suppression point.  A handler must never be
        entered for an intent whose ``delivered_at`` is already set, and a row
        that is mid-dispatch under another claim is reported as an unknown
        outcome rather than being dispatched twice.
        """
        _require_claim_ownership(intent)
        if target.run_id != intent.run_id or target.tenant_id != intent.tenant_id:
            raise ValueError("reservation target does not belong to the claimed intent")

        async with self._session_factory() as session:
            async with session.begin():
                db_now = await self._clock.now(session)
                claimed = await session.execute(
                    update(AgentControlPlaneEnqueueIntentRow)
                    .where(
                        *self._fence(intent),
                        AgentControlPlaneEnqueueIntentRow.state == INTENT_STATE_CLAIMED,
                        AgentControlPlaneEnqueueIntentRow.lease_until > db_now,
                        AgentControlPlaneEnqueueIntentRow.delivered_at.is_(None),
                    )
                    .values(
                        state=INTENT_STATE_DISPATCHING,
                        last_error=redacted_attempt_record(
                            action=DispatchRecordAction.RESERVE,
                            kind=kind,
                            attempts=intent.attempts,
                        ),
                    )
                    .returning(AgentControlPlaneEnqueueIntentRow.event_id)
                    .execution_options(synchronize_session=False)
                )
                if claimed.first() is not None:
                    return ReservationStatus.RESERVED

                # The guard above failed; classify why without guessing.
                observed = (
                    await session.execute(
                        select(
                            AgentControlPlaneEnqueueIntentRow.state,
                            AgentControlPlaneEnqueueIntentRow.delivered_at,
                            AgentControlPlaneEnqueueIntentRow.lease_generation,
                        ).where(*self._identity(intent))
                    )
                ).one_or_none()
                if observed is None:
                    return ReservationStatus.CONFLICT
                if observed.delivered_at is not None:
                    return ReservationStatus.ALREADY_DELIVERED
                if observed.state == INTENT_STATE_DISPATCHING:
                    return ReservationStatus.IN_FLIGHT_UNKNOWN
                if observed.lease_generation != intent.lease_generation:
                    return ReservationStatus.LEASE_LOST
                return ReservationStatus.LEASE_LOST

    # ----------------------------------------------------------------- outcome

    async def record_outcome(
        self,
        *,
        intent: ClaimedIntent,
        kind: IntentKind | None,
        target: ResolvedIntentTarget | None,
        outcome: IntentDispatchOutcome,
    ) -> bool:
        """Write the intent transition and its redacted audit record atomically.

        ``target`` is accepted for symmetry with :meth:`reserve` and for the
        audit vocabulary (which aggregate the decision concerned); it is never
        used to change scope.
        """
        _require_claim_ownership(intent)
        if outcome.action is IntentDispatchAction.RETRY and outcome.retry_at is None:
            raise ValueError("a retry outcome must carry its durable retry_at")
        del target

        state, retry_available_at, terminal_attempts = self._transition(outcome)
        record = redacted_attempt_record(
            action=self._record_action(outcome),
            reason=outcome.reason,
            kind=kind if kind is not None else _kind_or_none(intent),
            attempts=intent.attempts,
        )

        async with self._session_factory() as session:
            async with session.begin():
                db_now = await self._clock.now(session)
                values: dict[str, Any] = {
                    "state": state,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_until": None,
                    "last_error": record,
                }
                if terminal_attempts is not None:
                    values["attempts"] = terminal_attempts
                if outcome.action is IntentDispatchAction.DELIVERED:
                    values["delivered_at"] = db_now
                if outcome.action is IntentDispatchAction.RETRY:
                    values["available_at"] = outcome.retry_at
                if outcome.action in {
                    IntentDispatchAction.REJECTED,
                    IntentDispatchAction.QUARANTINED,
                }:
                    values["available_at"] = db_now

                changed = await session.execute(
                    update(AgentControlPlaneEnqueueIntentRow)
                    .where(
                        *self._fence(intent),
                        AgentControlPlaneEnqueueIntentRow.state.in_(INTENT_STATES_OWNED),
                        AgentControlPlaneEnqueueIntentRow.lease_until > db_now,
                    )
                    .values(**values)
                    .returning(AgentControlPlaneEnqueueIntentRow.event_id)
                    .execution_options(synchronize_session=False)
                )
                return changed.first() is not None

    def _transition(
        self, outcome: IntentDispatchOutcome
    ) -> tuple[str, datetime | None, int | None]:
        """Map a decision onto the durable row state.

        Terminal refusals pin ``attempts`` to the maximum so a rejected or
        quarantined intent can never be claimed again by accident; unblocking it
        is an explicit operator action, not a timeout.
        """
        if outcome.action is IntentDispatchAction.DELIVERED:
            return INTENT_STATE_DELIVERED, None, None
        if outcome.action is IntentDispatchAction.RETRY:
            return INTENT_STATE_PENDING, outcome.retry_at, None
        return INTENT_STATE_FAILED, None, self._settings.max_attempts

    @staticmethod
    def _record_action(outcome: IntentDispatchOutcome) -> DispatchRecordAction:
        return {
            IntentDispatchAction.DELIVERED: DispatchRecordAction.DELIVER,
            IntentDispatchAction.RETRY: DispatchRecordAction.RETRY,
            IntentDispatchAction.REJECTED: DispatchRecordAction.REJECT,
            IntentDispatchAction.QUARANTINED: DispatchRecordAction.QUARANTINE,
        }[outcome.action]

    # ------------------------------------------------------------------ repair

    async def release_expired(self, *, limit: int) -> int:
        """Recover leases whose owner died.

        ``claimed`` + expired lease means the handler was never entered, so the
        intent returns to ``pending`` (or becomes terminally exhausted).
        ``dispatching`` + expired lease means a handler *did* run and its outcome
        is unknown: the row is quarantined and never blind-retried.

        Both statements are idempotent and self-guarding (state + expired lease),
        so concurrent repair workers cannot corrupt a row by racing here; a
        second worker simply updates nothing.
        """
        if limit < 1:
            raise ValueError("limit must be positive")

        async with self._session_factory() as session:
            async with session.begin():
                db_now = await self._clock.now(session)
                max_attempts = self._settings.max_attempts

                expired_claimed = (
                    select(AgentControlPlaneEnqueueIntentRow.event_id)
                    .where(
                        AgentControlPlaneEnqueueIntentRow.queue_name == self._queue_name,
                        AgentControlPlaneEnqueueIntentRow.state == INTENT_STATE_CLAIMED,
                        AgentControlPlaneEnqueueIntentRow.lease_until.is_not(None),
                        AgentControlPlaneEnqueueIntentRow.lease_until <= db_now,
                    )
                    .order_by(AgentControlPlaneEnqueueIntentRow.lease_until)
                    .limit(limit)
                    .scalar_subquery()
                )
                reclaimed = await session.execute(
                    update(AgentControlPlaneEnqueueIntentRow)
                    .where(
                        AgentControlPlaneEnqueueIntentRow.queue_name == self._queue_name,
                        AgentControlPlaneEnqueueIntentRow.state == INTENT_STATE_CLAIMED,
                        AgentControlPlaneEnqueueIntentRow.lease_until.is_not(None),
                        AgentControlPlaneEnqueueIntentRow.lease_until <= db_now,
                        AgentControlPlaneEnqueueIntentRow.event_id.in_(expired_claimed),
                    )
                    .values(
                        state=case(
                            (
                                AgentControlPlaneEnqueueIntentRow.attempts >= max_attempts,
                                INTENT_STATE_FAILED,
                            ),
                            else_=INTENT_STATE_PENDING,
                        ),
                        available_at=db_now,
                        lease_owner=None,
                        lease_token=None,
                        lease_until=None,
                        last_error=case(
                            (
                                AgentControlPlaneEnqueueIntentRow.attempts >= max_attempts,
                                redacted_attempt_record(
                                    action=DispatchRecordAction.REJECT,
                                    reason=IntentDispatchReason.ATTEMPTS_EXHAUSTED,
                                ),
                            ),
                            else_=redacted_attempt_record(
                                action=DispatchRecordAction.RETRY,
                                reason=IntentDispatchReason.LEASE_LOST,
                            ),
                        ),
                    )
                    .execution_options(synchronize_session=False)
                )

                expired_dispatching = (
                    select(AgentControlPlaneEnqueueIntentRow.event_id)
                    .where(
                        AgentControlPlaneEnqueueIntentRow.queue_name == self._queue_name,
                        AgentControlPlaneEnqueueIntentRow.state == INTENT_STATE_DISPATCHING,
                        AgentControlPlaneEnqueueIntentRow.lease_until.is_not(None),
                        AgentControlPlaneEnqueueIntentRow.lease_until <= db_now,
                    )
                    .order_by(AgentControlPlaneEnqueueIntentRow.lease_until)
                    .limit(limit)
                    .scalar_subquery()
                )
                quarantined = await session.execute(
                    update(AgentControlPlaneEnqueueIntentRow)
                    .where(
                        AgentControlPlaneEnqueueIntentRow.queue_name == self._queue_name,
                        AgentControlPlaneEnqueueIntentRow.state == INTENT_STATE_DISPATCHING,
                        AgentControlPlaneEnqueueIntentRow.lease_until.is_not(None),
                        AgentControlPlaneEnqueueIntentRow.lease_until <= db_now,
                        AgentControlPlaneEnqueueIntentRow.delivered_at.is_(None),
                        AgentControlPlaneEnqueueIntentRow.event_id.in_(expired_dispatching),
                    )
                    .values(
                        state=INTENT_STATE_FAILED,
                        # Pin the counter so a quarantined row can never be
                        # re-claimed by a timeout; clearing it is an explicit
                        # operator action.
                        attempts=max_attempts,
                        available_at=db_now,
                        lease_owner=None,
                        lease_token=None,
                        lease_until=None,
                        last_error=redacted_attempt_record(
                            action=DispatchRecordAction.QUARANTINE,
                            reason=IntentDispatchReason.OUTCOME_UNKNOWN,
                        ),
                    )
                    .execution_options(synchronize_session=False)
                )
                return int(reclaimed.rowcount or 0) + int(quarantined.rowcount or 0)

    # ------------------------------------------------------------------ fences

    def _identity(self, intent: ClaimedIntent):
        return (
            AgentControlPlaneEnqueueIntentRow.event_id == intent.event_id,
            AgentControlPlaneEnqueueIntentRow.queue_name == intent.queue_name,
        )

    def _fence(self, intent: ClaimedIntent):
        """Every write after a claim must prove it still owns that exact claim."""
        if intent.queue_name != self._queue_name:
            raise ValueError("claimed intent belongs to another queue")
        return (
            *self._identity(intent),
            AgentControlPlaneEnqueueIntentRow.lease_owner == intent.lease_owner,
            AgentControlPlaneEnqueueIntentRow.lease_token == intent.lease_token,
            AgentControlPlaneEnqueueIntentRow.lease_generation == intent.lease_generation,
        )


class SqlAlchemyIntentScopeResolver:
    """Resolve an intent's run/aggregate from persisted rows only.

    The intent payload is evidence, not authority: tenant, project, session,
    aggregate identity and sequence are all read back from the run scope, the
    run row and the aggregate cursor.  Any contradiction raises
    :class:`IntentScopeViolation` with a reason code and no data.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(self, *, intent: ClaimedIntent) -> ResolvedIntentTarget | None:
        kind = classify_intent_kind(
            event_type=intent.event_type, aggregate_type=intent.aggregate_type
        )
        if kind is None:
            return None

        async with self._session_factory() as session:
            scope = await session.get(AgentControlPlaneRunScopeRow, intent.run_id)
            if scope is None:
                raise IntentScopeViolation(IntentDispatchReason.SCOPE_UNRESOLVED)
            if scope.tenant_id != intent.tenant_id:
                raise IntentScopeViolation(IntentDispatchReason.TENANT_MISMATCH)
            if scope.project_id != intent.project_id:
                raise IntentScopeViolation(IntentDispatchReason.PAYLOAD_SCOPE_MISMATCH)
            if scope.session_id != intent.session_id:
                raise IntentScopeViolation(IntentDispatchReason.PAYLOAD_SCOPE_MISMATCH)

            run = await session.get(AgentRunRow, intent.run_id)
            if run is None or run.project_id != scope.project_id:
                raise IntentScopeViolation(IntentDispatchReason.SCOPE_UNRESOLVED)

            cursor = await session.get(
                AgentControlPlaneAggregateRow, (intent.aggregate_type, intent.aggregate_id)
            )
            if cursor is None:
                raise IntentScopeViolation(IntentDispatchReason.AGGREGATE_MISMATCH)

            approval_id: str | None = None
            approval_status: str | None = None
            if intent.aggregate_type == APPROVAL_AGGREGATE_TYPE:
                if cursor.aggregate_id != intent.aggregate_id:
                    raise IntentScopeViolation(IntentDispatchReason.AGGREGATE_MISMATCH)
                approval = await session.get(ApprovalRequestRow, intent.aggregate_id)
                if approval is None or approval.run_id != intent.run_id:
                    raise IntentScopeViolation(IntentDispatchReason.AGGREGATE_MISMATCH)
                approval_id = approval.id
                approval_status = approval.status
            elif intent.aggregate_type != RUN_AGGREGATE_TYPE or intent.aggregate_id != intent.run_id:
                raise IntentScopeViolation(IntentDispatchReason.AGGREGATE_MISMATCH)

            return ResolvedIntentTarget(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                run_id=run.id,
                session_id=scope.session_id,
                run_status=AgentRunStatus(run.status),
                aggregate_type=intent.aggregate_type,
                aggregate_id=intent.aggregate_id,
                aggregate_status=cursor.status,
                aggregate_sequence=cursor.last_sequence,
                approval_id=approval_id,
                approval_status=approval_status,
            )


def build_intent_dispatcher(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    queue_name: str,
    execution: AgentRunExecutionPort | None = None,
    settings: IntentConsumerSettings | None = None,
    clock: DatabaseClockPort | None = None,
    max_attempts: int = 8,
) -> tuple[EnqueueIntentDispatcher, SqlAlchemyIntentConsumer]:
    """Compose the durable consumer, the resolver and the policy object.

    This is the composition root host wiring calls; it returns the consumer as
    well so a caller can reuse it (e.g. to expose repair counters) without
    constructing a second one.
    """
    consumer = SqlAlchemyIntentConsumer(
        session_factory=session_factory,
        queue_name=queue_name,
        settings=settings,
        clock=clock,
    )
    dispatcher = EnqueueIntentDispatcher(
        consumer=consumer,
        resolver=SqlAlchemyIntentScopeResolver(session_factory=session_factory),
        execution=execution,
        handlers=build_execution_handlers(execution),
        max_attempts=max_attempts,
    )
    return dispatcher, consumer


def _require_owner(owner_id: str) -> str:
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("owner_id must not be empty")
    return owner_id.strip()[:_OWNER_MAX]


def _require_claim_ownership(intent: ClaimedIntent) -> None:
    if not isinstance(intent, ClaimedIntent):
        raise TypeError("intent must be a ClaimedIntent")
    if intent.lease_generation < 1:
        raise ValueError("a claimed intent must carry a positive lease generation")


def _kind_or_none(intent: ClaimedIntent) -> IntentKind | None:
    return classify_intent_kind(event_type=intent.event_type, aggregate_type=intent.aggregate_type)


__all__ = [
    "DatabaseClockPort",
    "DispatchRecordAction",
    "INTENT_STATES_CLAIMABLE",
    "INTENT_STATES_OWNED",
    "INTENT_STATE_CLAIMED",
    "INTENT_STATE_DELIVERED",
    "INTENT_STATE_DISPATCHING",
    "INTENT_STATE_FAILED",
    "INTENT_STATE_PENDING",
    "IntentConsumerIntegrityError",
    "IntentConsumerSettings",
    "SqlAlchemyIntentConsumer",
    "SqlAlchemyIntentScopeResolver",
    "SqlDatabaseClock",
    "build_intent_dispatcher",
    "redacted_attempt_record",
]
