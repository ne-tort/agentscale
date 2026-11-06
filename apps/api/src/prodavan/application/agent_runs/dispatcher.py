"""Durable dispatch of agent control-plane enqueue intents.

``agent_control_plane_enqueue_intents`` is written in the same transaction as a
control-plane state transition, its domain event, and its outbox projection.
That row is a durable *handoff*: it proves that delivery is still owed, not that
anything was delivered.  This module is the application half of the consumer
that finally owns those rows:

1. claim a bounded batch under a database-time lease (``SELECT ... FOR UPDATE
   SKIP LOCKED`` in the persistence adapter);
2. resolve the addressed run from server-side rows and re-validate the intent
   payload against them, never trusting the payload's own claims;
3. reserve one delivery receipt for the intent, which is the durable
   duplicate-suppression point;
4. run the registered handler, which is the only place allowed to touch
   ``AgentRunExecutionPort``;
5. record the outcome and the intent transition in one transaction.

The module is deliberately transport-neutral: it never imports persistence
code.  The adapter supplies claim/reserve/record behaviour through
:class:`IntentConsumerPort` and run resolution through
:class:`IntentScopeResolverPort`.

Fail-closed rules
-----------------
* An unrecognised ``event_type``/``aggregate_type`` pair is refused
  (``intent_kind_unknown``) with a redacted audit entry.  The dispatcher never
  guesses what an unknown intent means.
* A recognised kind without a registered handler is retried, not delivered:
  nothing may report delivery for work no executor performed.  This is the
  state the host is in while ``AgentRunExecutionPort`` has no adapter.
* An intent whose aggregate sequence/status no longer matches the persisted
  aggregate is refused (``intent_state_superseded``): a stale transition must
  never be re-applied to newer state.
* A previously reserved but unfinished delivery (expired lease, unknown
  provider outcome) is quarantined, never blind-retried.
* Receipts carry reason codes only.  Payloads, reasons, session/trace IDs and
  raw exception text never reach the audit trail.

Extension points
----------------
* ``handlers`` — the kind registry.  A real broker/worker integration registers
  one handler per :class:`IntentKind`; nothing else changes.
* :class:`IntentConsumerPort` — a broker-backed consumer may implement the same
  pipeline against its own durable rows.  A broker must remain a *consumer* of
  the durable handoff, never a replacement that hides it.
* :class:`DurableIntentPollLoop` takes any :class:`DispatchRunner`, so a broker
  consumer, an admin drain endpoint, or a Kubernetes job all share one loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from prodavan.domain.agent.control_plane import AgentRunStatus

from .execution import AgentRunExecutionPort, AgentRunExecutionUnavailable

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_RETRY_BACKOFF_SECONDS = 5.0
DEFAULT_MAX_RETRY_BACKOFF_SECONDS = 300.0
DEFAULT_BATCH_SIZE = 25
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_IDLE_INTERVAL_SECONDS = 5.0

MAX_BATCH_SIZE = 200


class IntentKind(StrEnum):
    """Closed set of dispatchable control-plane intent kinds.

    Values are exactly the domain event types the durable repository accepts,
    so classification is a table lookup rather than an interpretation.
    """

    RUN_CREATED = "agent_run.created"
    RUN_STARTED = "agent_run.started"
    RUN_WAITING_APPROVAL = "agent_run.waiting_approval"
    RUN_RESUMED = "agent_run.resumed"
    RUN_SUCCEEDED = "agent_run.succeeded"
    RUN_FAILED = "agent_run.failed"
    RUN_CANCELLED = "agent_run.cancelled"
    RUN_TIMED_OUT = "agent_run.timed_out"
    APPROVAL_CREATED = "approval_request.created"
    APPROVAL_APPROVED = "approval_request.approved"
    APPROVAL_DENIED = "approval_request.denied"
    APPROVAL_CANCELLED = "approval_request.cancelled"
    APPROVAL_EXPIRED = "approval_request.expired"


_RUN_KINDS = frozenset(
    {
        IntentKind.RUN_CREATED,
        IntentKind.RUN_STARTED,
        IntentKind.RUN_WAITING_APPROVAL,
        IntentKind.RUN_RESUMED,
        IntentKind.RUN_SUCCEEDED,
        IntentKind.RUN_FAILED,
        IntentKind.RUN_CANCELLED,
        IntentKind.RUN_TIMED_OUT,
    }
)
_APPROVAL_KINDS = frozenset(
    {
        IntentKind.APPROVAL_CREATED,
        IntentKind.APPROVAL_APPROVED,
        IntentKind.APPROVAL_DENIED,
        IntentKind.APPROVAL_CANCELLED,
        IntentKind.APPROVAL_EXPIRED,
    }
)
RUN_AGGREGATE_TYPE = "agent_run"
APPROVAL_AGGREGATE_TYPE = "approval_request"


class IntentDispatchAction(StrEnum):
    """What the durable consumer must do with a claimed intent.

    ``ABANDONED`` is deliberately absent: a lost lease is decided by the
    consumer returning ``LEASE_LOST`` or ``False``, never by an outcome the
    dispatcher asks another worker to write.
    """

    DELIVERED = "delivered"
    RETRY = "retry"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class IntentDispatchReason(StrEnum):
    """Redacted reason-code taxonomy; the only text stored in audit rows."""

    DELIVERED = "intent_delivered"
    REPLAY_SUPPRESSED = "intent_delivery_replay_suppressed"
    SUPERSEDED = "intent_state_superseded"
    UNKNOWN_KIND = "intent_kind_unknown"
    HANDLER_NOT_WIRED = "intent_handler_not_wired"
    HANDLER_FAILED = "intent_handler_failed"
    EXECUTION_UNWIRED = "agent_run_durable_claim_and_lease_unwired"
    UNKNOWN_OUTCOME_UNWIRED = "agent_run_provider_outcome_reconciliation_unwired"
    SCOPE_UNRESOLVED = "intent_scope_unresolved"
    TENANT_MISMATCH = "intent_tenant_mismatch"
    AGGREGATE_MISMATCH = "intent_aggregate_mismatch"
    PAYLOAD_SCOPE_MISMATCH = "intent_payload_scope_mismatch"
    RECEIPT_CONFLICT = "dispatch_receipt_digest_conflict"
    OUTCOME_UNKNOWN = "dispatch_outcome_unknown_requires_reconciliation"
    ATTEMPTS_EXHAUSTED = "intent_attempts_exhausted"
    LEASE_LOST = "intent_lease_lost"


class ReservationStatus(StrEnum):
    """Outcome of the durable delivery reservation for one claimed intent."""

    RESERVED = "reserved"
    ALREADY_DELIVERED = "already_delivered"
    IN_FLIGHT_UNKNOWN = "in_flight_unknown"
    CONFLICT = "conflict"
    LEASE_LOST = "lease_lost"


class IntentScopeViolation(RuntimeError):
    """The resolved rows contradict the intent; only a reason code escapes."""

    def __init__(self, reason: IntentDispatchReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class ClaimedIntent:
    """One leased enqueue intent with the server-side event evidence for it.

    ``payload`` is the projected domain event body.  It is data to be checked,
    never an authority: tenant, project, session and sequence are validated
    against persisted rows before any handler runs.
    """

    event_id: str
    queue_name: str
    tenant_id: str
    project_id: str
    deduplication_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    run_id: str
    session_id: str | None
    occurred_at: datetime
    payload: Mapping[str, Any]
    lease_owner: str
    lease_token: str
    lease_generation: int
    attempts: int
    claimed_at: datetime
    lease_until: datetime

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "queue_name",
            "tenant_id",
            "project_id",
            "deduplication_key",
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "run_id",
            "lease_owner",
            "lease_token",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be empty when supplied")
        if self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")
        if self.lease_generation < 1 or self.attempts < 1:
            raise ValueError("lease generation and attempts must be positive")
        for name in ("occurred_at", "claimed_at", "lease_until"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.lease_until <= self.claimed_at:
            raise ValueError("lease_until must follow the claim time")
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")


@dataclass(frozen=True, slots=True)
class ResolvedIntentTarget:
    """Server-side resolution of the run/aggregate one intent addresses."""

    tenant_id: str
    project_id: str
    run_id: str
    session_id: str | None
    run_status: AgentRunStatus
    aggregate_type: str
    aggregate_id: str
    aggregate_status: str
    aggregate_sequence: int
    approval_id: str | None = None
    approval_status: str | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "run_id", "aggregate_type", "aggregate_id", "aggregate_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be empty when supplied")
        if self.aggregate_sequence < 1:
            raise ValueError("aggregate_sequence must be positive")
        if self.aggregate_type == APPROVAL_AGGREGATE_TYPE and self.approval_id is None:
            raise ValueError("an approval aggregate target requires its approval identity")
        if self.approval_id is not None and self.approval_status is None:
            raise ValueError("an approval target requires its persisted status")


@dataclass(frozen=True, slots=True)
class IntentDispatchOutcome:
    """One redacted dispatch decision for a claimed intent."""

    action: IntentDispatchAction
    reason: IntentDispatchReason
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.action is IntentDispatchAction.RETRY:
            if self.retry_at is None:
                raise ValueError("a retry decision requires a durable retry_at")
            if self.retry_at.tzinfo is None or self.retry_at.utcoffset() is None:
                raise ValueError("retry_at must be timezone-aware")
        elif self.retry_at is not None:
            raise ValueError("only a retry decision may carry retry_at")


def delivered(reason: IntentDispatchReason = IntentDispatchReason.DELIVERED) -> IntentDispatchOutcome:
    return IntentDispatchOutcome(action=IntentDispatchAction.DELIVERED, reason=reason)


def rejected(reason: IntentDispatchReason) -> IntentDispatchOutcome:
    return IntentDispatchOutcome(action=IntentDispatchAction.REJECTED, reason=reason)


def quarantined(reason: IntentDispatchReason = IntentDispatchReason.OUTCOME_UNKNOWN) -> IntentDispatchOutcome:
    return IntentDispatchOutcome(action=IntentDispatchAction.QUARANTINED, reason=reason)


def retry_later(
    *,
    due_at: datetime,
    reason: IntentDispatchReason,
) -> IntentDispatchOutcome:
    """Build a retry decision for a handler-side retryable failure.

    ``due_at`` must come from server time, not from a process clock: the
    durable consumer compares it with the database clock.
    """
    return IntentDispatchOutcome(
        action=IntentDispatchAction.RETRY, reason=reason, retry_at=due_at
    )


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Everything a handler may use; the execution port stays explicit."""

    intent: ClaimedIntent
    target: ResolvedIntentTarget
    kind: IntentKind
    execution: AgentRunExecutionPort | None

    @property
    def is_execution_wired(self) -> bool:
        return self.execution is not None


class DispatchHandler(Protocol):
    """One registered handler per intent kind (the broker extension point)."""

    async def __call__(self, context: DispatchContext) -> IntentDispatchOutcome: ...


class IntentConsumerPort(Protocol):
    """Durable claim/reserve/record pipeline owned by the persistence adapter.

    Implementations must:

    * claim under a database-time lease with ``SKIP LOCKED`` so two workers never
      take one intent;
    * fence every write on lease token + generation + current state, returning
      ``False``/``LEASE_LOST`` instead of overwriting another worker's lease;
    * reserve exactly one delivery receipt per intent, keyed by queue,
      deduplication key and intent kind, so a re-delivery cannot duplicate work;
    * write the intent transition, the delivery receipt and the attempt audit
      entry in one transaction.
    """

    async def claim(self, *, owner_id: str, limit: int) -> Sequence[ClaimedIntent]: ...

    async def reserve(
        self,
        *,
        intent: ClaimedIntent,
        kind: IntentKind,
        target: ResolvedIntentTarget,
    ) -> ReservationStatus: ...

    async def record_outcome(
        self,
        *,
        intent: ClaimedIntent,
        kind: IntentKind | None,
        target: ResolvedIntentTarget | None,
        outcome: IntentDispatchOutcome,
    ) -> bool: ...

    async def release_expired(self, *, limit: int) -> int: ...


class FencedRunClaimPort(Protocol):
    """Narrow per-run claim an executor must expose to receive a dispatch.

    ``AgentRunExecutionPort.claim_next`` claims *whatever* run is eligible next
    and cannot be addressed by an intent, so it cannot deliver a specific
    enqueue intent without stealing another run's turn.  Delivering one intent
    needs an addressable, fenced claim: the run must match the intent, the
    generation must advance, and the returned lease must be the one this
    dispatch owns.

    ``AgentRunExecutionPort`` (frozen by another task) does not declare this
    method, so :class:`AgentRunExecutionHandler` refuses to guess and records
    ``agent_run_durable_claim_and_lease_unwired`` until an adapter supplies it.
    """

    async def claim_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        owner_id: str,
        lease_until: datetime,
    ) -> "ClaimedRunLease | None": ...


@dataclass(frozen=True, slots=True)
class ClaimedRunLease:
    """Minimal fenced claim an executor returns when it starts an intent's run."""

    tenant_id: str
    project_id: str
    run_id: str
    owner_id: str
    lease_token: str
    generation: int
    lease_until: datetime

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "run_id", "owner_id", "lease_token"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
            raise ValueError("lease_until must be timezone-aware")


class AgentRunExecutionHandler:
    """Default handler for ``agent_run.created``: start the addressed run.

    The handler verifies that the executor's claim addresses exactly the run
    named by the intent.  A claim for any other run is refused rather than
    accepted: an intent may never be marked delivered for work performed on a
    different aggregate.
    """

    def __init__(self, *, lease_seconds: float = 30.0, retry_delay_seconds: float = 5.0) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be positive")
        if lease_seconds <= retry_delay_seconds:
            raise ValueError("the execution lease must outlive the dispatch retry delay")
        self._lease_seconds = lease_seconds
        self._retry_delay_seconds = retry_delay_seconds

    async def __call__(self, context: DispatchContext) -> IntentDispatchOutcome:
        execution = context.execution
        if execution is None:
            raise AgentRunExecutionUnavailable()
        claim_run = getattr(execution, "claim_run", None)
        if claim_run is None:
            # A frozen port without an addressable claim cannot deliver an intent
            # without stealing another run; refuse instead of approximating.
            raise AgentRunExecutionUnavailable()

        now = context.intent.claimed_at
        lease = await claim_run(
            tenant_id=context.target.tenant_id,
            project_id=context.target.project_id,
            run_id=context.target.run_id,
            expected_sequence=context.target.aggregate_sequence,
            expected_status=context.target.run_status,
            owner_id=context.intent.lease_owner,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if lease is None:
            # The run is not claimable yet (state moved on or the executor is
            # behind).  Retry: this intent has not been delivered.
            return retry_later(
                due_at=now + timedelta(seconds=self._retry_delay_seconds),
                reason=IntentDispatchReason.HANDLER_FAILED,
            )
        if (
            lease.run_id != context.target.run_id
            or lease.tenant_id != context.target.tenant_id
            or lease.project_id != context.target.project_id
        ):
            raise ValueError("executor claimed a different run than the intent addresses")
        return delivered()


def build_execution_handlers(
    execution: AgentRunExecutionPort | None,
    *,
    lease_seconds: float = 30.0,
    retry_delay_seconds: float = 5.0,
) -> dict[IntentKind, DispatchHandler]:
    """Default registry: only ``agent_run.created`` starts work today.

    Every other kind is an already-applied state transition whose external
    projection is a separate integration; they are intentionally left
    unregistered so they retry instead of pretending to be delivered.
    """
    if execution is None:
        return {}
    return {
        IntentKind.RUN_CREATED: AgentRunExecutionHandler(
            lease_seconds=lease_seconds, retry_delay_seconds=retry_delay_seconds
        )
    }


class IntentScopeResolverPort(Protocol):
    """Resolve the addressed run/aggregate from persisted rows only."""

    async def resolve(self, *, intent: ClaimedIntent) -> ResolvedIntentTarget | None: ...


@dataclass(frozen=True, slots=True)
class DispatchBatchReport:
    """Bounded, redacted counters for one dispatch round."""

    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    rejected: int = 0
    quarantined: int = 0
    lease_lost: int = 0
    repaired: int = 0

    @property
    def has_work(self) -> bool:
        return self.claimed > 0


class DispatchRunner(Protocol):
    """Any bounded dispatcher, including a broker-backed one."""

    async def dispatch_batch(self, *, owner_id: str, limit: int) -> DispatchBatchReport: ...


def classify_intent_kind(*, event_type: str, aggregate_type: str) -> IntentKind | None:
    """Map one projected event onto a dispatchable kind, or ``None``.

    ``None`` means the intent is not part of the closed contract and must be
    refused rather than interpreted.
    """
    if not isinstance(event_type, str) or not isinstance(aggregate_type, str):
        return None
    try:
        kind = IntentKind(event_type)
    except ValueError:
        return None
    if aggregate_type == RUN_AGGREGATE_TYPE and kind in _RUN_KINDS:
        return kind
    if aggregate_type == APPROVAL_AGGREGATE_TYPE and kind in _APPROVAL_KINDS:
        return kind
    return None


class EnqueueIntentDispatcher:
    """Resolve, validate, dispatch and durably record enqueue intents.

    The dispatcher owns policy (which kinds are dispatchable, when to retry,
    when to give up) and delegates every durable effect to
    :class:`IntentConsumerPort`.  It never touches a database session itself.
    """

    def __init__(
        self,
        *,
        consumer: IntentConsumerPort,
        resolver: IntentScopeResolverPort,
        execution: AgentRunExecutionPort | None = None,
        handlers: Mapping[IntentKind, DispatchHandler] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        max_retry_backoff_seconds: float = DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_backoff_seconds <= 0 or max_retry_backoff_seconds <= 0:
            raise ValueError("retry backoff bounds must be positive")
        if max_retry_backoff_seconds < retry_backoff_seconds:
            raise ValueError("max_retry_backoff_seconds must not be below retry_backoff_seconds")
        self._consumer = consumer
        self._resolver = resolver
        self._execution = execution
        self._handlers: dict[IntentKind, DispatchHandler] = dict(handlers or {})
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_retry_backoff_seconds = max_retry_backoff_seconds

    @property
    def registered_kinds(self) -> frozenset[IntentKind]:
        return frozenset(self._handlers)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def register(self, kind: IntentKind, handler: DispatchHandler) -> None:
        """Register the executor for one kind (host wiring / broker cutover)."""
        if kind in self._handlers:
            raise ValueError(f"a handler is already registered for {kind.value}")
        self._handlers[kind] = handler

    async def dispatch_batch(
        self,
        *,
        owner_id: str,
        limit: int = DEFAULT_BATCH_SIZE,
        repair_expired: int = 0,
    ) -> DispatchBatchReport:
        """Claim and dispatch at most ``limit`` due intents once."""
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not 1 <= limit <= MAX_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}")
        if repair_expired < 0:
            raise ValueError("repair_expired must not be negative")

        repaired = 0
        if repair_expired:
            repaired = await self._consumer.release_expired(limit=repair_expired)

        intents = await self._consumer.claim(owner_id=owner_id, limit=limit)
        delivered = retried = rejected_count = quarantined_count = lease_lost = 0
        for intent in intents:
            decision = await self._dispatch_one(intent)
            if decision is None:
                lease_lost += 1
                continue
            target, outcome = decision
            try:
                recorded = await self._consumer.record_outcome(
                    intent=intent,
                    kind=classify_intent_kind(
                        event_type=intent.event_type, aggregate_type=intent.aggregate_type
                    ),
                    target=target,
                    outcome=outcome,
                )
            except Exception as exc:  # noqa: BLE001 - the lease decides, not the loop
                # The lease still fences this row: an unrecorded outcome expires
                # and is re-claimed, which the reservation then quarantines.
                logger.warning(
                    "agent intent outcome not recorded error=%s kind=%s",
                    type(exc).__qualname__,
                    intent.event_type,
                )
                continue
            if not recorded:
                lease_lost += 1
                continue
            if outcome.action is IntentDispatchAction.DELIVERED:
                delivered += 1
            elif outcome.action is IntentDispatchAction.RETRY:
                retried += 1
            elif outcome.action is IntentDispatchAction.REJECTED:
                rejected_count += 1
            else:
                quarantined_count += 1
        return DispatchBatchReport(
            claimed=len(intents),
            delivered=delivered,
            retried=retried,
            rejected=rejected_count,
            quarantined=quarantined_count,
            lease_lost=lease_lost,
            repaired=repaired,
        )

    async def _dispatch_one(
        self, intent: ClaimedIntent
    ) -> tuple[ResolvedIntentTarget | None, IntentDispatchOutcome] | None:
        """Return the resolution and decision, or ``None`` when the lease was lost."""
        kind = classify_intent_kind(event_type=intent.event_type, aggregate_type=intent.aggregate_type)
        if kind is None:
            return None, rejected(IntentDispatchReason.UNKNOWN_KIND)

        try:
            target = await self._resolver.resolve(intent=intent)
        except IntentScopeViolation as violation:
            return None, rejected(violation.reason)
        if target is None:
            return None, rejected(IntentDispatchReason.SCOPE_UNRESOLVED)

        mismatch = self._scope_reason(intent=intent, target=target, kind=kind)
        if mismatch is not None:
            return None, rejected(mismatch)

        if self._superseded(intent=intent, target=target):
            return None, rejected(IntentDispatchReason.SUPERSEDED)

        handler = self._handlers.get(kind)
        if handler is None:
            # Legitimate work, unwired executor: retry, never claim delivery.
            return target, self._retry(intent=intent, reason=IntentDispatchReason.HANDLER_NOT_WIRED)

        try:
            reservation = await self._consumer.reserve(intent=intent, kind=kind, target=target)
        except Exception as exc:  # noqa: BLE001 - reservation failure is a retry, not a crash
            logger.warning(
                "agent intent reservation failed error=%s kind=%s",
                type(exc).__qualname__,
                kind.value,
            )
            return target, self._retry(intent=intent, reason=IntentDispatchReason.HANDLER_FAILED)

        if reservation is ReservationStatus.LEASE_LOST:
            return None
        if reservation is ReservationStatus.CONFLICT:
            return target, rejected(IntentDispatchReason.RECEIPT_CONFLICT)
        if reservation is ReservationStatus.ALREADY_DELIVERED:
            return target, delivered(IntentDispatchReason.REPLAY_SUPPRESSED)
        if reservation is ReservationStatus.IN_FLIGHT_UNKNOWN:
            return target, quarantined(IntentDispatchReason.OUTCOME_UNKNOWN)

        context = DispatchContext(intent=intent, target=target, kind=kind, execution=self._execution)
        try:
            outcome = await handler(context)
        except AgentRunExecutionUnavailable:
            return target, self._retry(intent=intent, reason=IntentDispatchReason.EXECUTION_UNWIRED)
        except Exception as exc:  # noqa: BLE001 - handler faults become a bounded retry
            logger.warning(
                "agent intent handler failed error=%s kind=%s",
                type(exc).__qualname__,
                kind.value,
            )
            return target, self._retry(intent=intent, reason=IntentDispatchReason.HANDLER_FAILED)
        if not isinstance(outcome, IntentDispatchOutcome):
            raise TypeError("a dispatch handler must return IntentDispatchOutcome")
        return target, outcome

    def _retry(self, *, intent: ClaimedIntent, reason: IntentDispatchReason) -> IntentDispatchOutcome:
        if intent.attempts >= self._max_attempts:
            # Poison-pill guard: stop spending attempts on a row that cannot be
            # delivered, and leave a terminal, queryable refusal behind.
            return rejected(IntentDispatchReason.ATTEMPTS_EXHAUSTED)
        delay = min(
            self._max_retry_backoff_seconds,
            self._retry_backoff_seconds * (2 ** (intent.attempts - 1)),
        )
        return IntentDispatchOutcome(
            action=IntentDispatchAction.RETRY,
            reason=reason,
            retry_at=intent.claimed_at + timedelta(seconds=delay),
        )

    def _scope_reason(
        self,
        *,
        intent: ClaimedIntent,
        target: ResolvedIntentTarget,
        kind: IntentKind,
    ) -> IntentDispatchReason | None:
        """Re-check the intent against resolved rows and its own payload."""
        if target.tenant_id != intent.tenant_id:
            return IntentDispatchReason.TENANT_MISMATCH
        if intent.project_id != target.project_id:
            return IntentDispatchReason.PAYLOAD_SCOPE_MISMATCH
        if intent.run_id != target.run_id:
            return IntentDispatchReason.AGGREGATE_MISMATCH
        if kind in _RUN_KINDS:
            if intent.aggregate_type != RUN_AGGREGATE_TYPE or intent.aggregate_id != target.run_id:
                return IntentDispatchReason.AGGREGATE_MISMATCH
        elif intent.aggregate_type != APPROVAL_AGGREGATE_TYPE or intent.aggregate_id != target.approval_id:
            return IntentDispatchReason.AGGREGATE_MISMATCH

        linkage = intent.payload.get("control_plane")
        if linkage is not None:
            if not isinstance(linkage, Mapping):
                return IntentDispatchReason.PAYLOAD_SCOPE_MISMATCH
            if linkage.get("tenant_id") != intent.tenant_id:
                return IntentDispatchReason.TENANT_MISMATCH
            if linkage.get("project_id") != target.project_id:
                return IntentDispatchReason.PAYLOAD_SCOPE_MISMATCH
            if linkage.get("session_id") != target.session_id:
                return IntentDispatchReason.PAYLOAD_SCOPE_MISMATCH
        return None

    def _superseded(self, *, intent: ClaimedIntent, target: ResolvedIntentTarget) -> bool:
        """A stale intent must never re-apply an old transition to newer state."""
        if intent.aggregate_version != target.aggregate_sequence:
            return True
        announced = intent.payload.get("status")
        if announced is not None and announced != target.aggregate_status:
            return True
        if target.aggregate_type == APPROVAL_AGGREGATE_TYPE and target.approval_status != announced:
            return True
        return False


class DurableIntentPollLoop:
    """Durable poll loop: claim a batch, dispatch it, sleep, repeat.

    Nothing here is an in-memory queue: every round reads the durable intent
    rows. An empty round is not a delivery; it only means nothing was due.
    """

    def __init__(
        self,
        *,
        runner: DispatchRunner,
        owner_id: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        idle_interval_seconds: float = DEFAULT_IDLE_INTERVAL_SECONDS,
        repair_batch_size: int = 0,
    ) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
        if interval_seconds <= 0 or idle_interval_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if repair_batch_size < 0:
            raise ValueError("repair_batch_size must not be negative")
        self._runner = runner
        self._owner_id = owner_id
        self._batch_size = batch_size
        self._interval = interval_seconds
        self._idle_interval = idle_interval_seconds
        self._repair_batch_size = repair_batch_size
        self._stop = asyncio.Event()

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def request_stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> DispatchBatchReport:
        return await self._runner.dispatch_batch(
            owner_id=self._owner_id,
            limit=self._batch_size,
            repair_expired=self._repair_batch_size,
        )

    async def serve(self, *, max_rounds: int | None = None) -> DispatchBatchReport:
        """Run rounds until stopped. Faults are logged by class name only."""
        if max_rounds is not None and max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        rounds = 0
        last = DispatchBatchReport()
        while not self._stop.is_set():
            if max_rounds is not None and rounds >= max_rounds:
                return last
            rounds += 1
            try:
                last = await self.run_once()
            except Exception as exc:  # noqa: BLE001 - one bad round must not stop the loop
                logger.warning("agent intent dispatch round failed error=%s", type(exc).__qualname__)
                delay = self._interval
            else:
                delay = self._interval if last.has_work else self._idle_interval
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue
        return last


__all__ = [
    "APPROVAL_AGGREGATE_TYPE",
    "AgentRunExecutionHandler",
    "ClaimedIntent",
    "ClaimedRunLease",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_IDLE_INTERVAL_SECONDS",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_RETRY_BACKOFF_SECONDS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DispatchBatchReport",
    "DispatchContext",
    "DispatchHandler",
    "DispatchRunner",
    "DurableIntentPollLoop",
    "EnqueueIntentDispatcher",
    "FencedRunClaimPort",
    "IntentConsumerPort",
    "IntentDispatchAction",
    "IntentDispatchOutcome",
    "IntentDispatchReason",
    "IntentKind",
    "IntentScopeResolverPort",
    "IntentScopeViolation",
    "MAX_BATCH_SIZE",
    "RUN_AGGREGATE_TYPE",
    "ReservationStatus",
    "ResolvedIntentTarget",
    "build_execution_handlers",
    "classify_intent_kind",
    "delivered",
    "quarantined",
    "rejected",
    "retry_later",
]