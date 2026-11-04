"""Real execution handler for ``agent_run.*`` control-plane enqueue intents.

Problem this module solves
--------------------------
``infrastructure/persistence/agent_control_plane/intent_consumer.py`` claims the
durable handoff rows written in the same transaction as a control-plane
transition (``FOR UPDATE SKIP LOCKED``, database-clock lease, fencing, redacted
audit) and ``application/agent_runs/dispatcher.py`` owns the dispatch policy.
What was missing is the **executor**: no handler was registered for any kind, so
every claimed intent was recorded as ``intent_handler_not_wired``, retried with
exponential backoff and finally parked terminally.  The API committed a run, the
consumer leased the intent, and nothing ever executed.

This module is that executor.  It is a :class:`DispatchHandler`, so it plugs into
the existing registry (``EnqueueIntentDispatcher.register`` /
``handlers=``) and changes no policy of its own: classification, scope
resolution, supersession, reservation and the outcome write stay where they
already are.

How one intent becomes one executed turn
----------------------------------------
``agent_run.created`` is the only kind that means "start executing", so it is the
only registered one (:data:`EXECUTABLE_INTENT_KINDS`).  For that kind:

1. **address the run.**  The dispatcher already resolved the run, its scope and
   its committed aggregate sequence from persisted rows.  The handler turns that
   resolution into an :class:`AddressableRunScope`: exactly one run, exactly one
   aggregate version, exactly the announced status.
2. **claim it through the frozen adapter.**  ``AgentRunExecutionPort.claim_next``
   claims *whatever* run is eligible next, so it cannot deliver a specific intent
   without stealing another run's turn (the dispatcher documents this gap as
   ``agent_run_durable_claim_and_lease_unwired``).  Instead of re-implementing
   the adapter's two-step claim, the handler hands the adapter a **narrowed lease
   store** (:class:`IntentAddressedLeaseStore`) whose only possible claim
   candidate is the addressed run.  The adapter therefore performs its own
   ``claim_scope`` → ``plan_acquisition`` → ``cas_acquire`` → ``agent_run.start``
   sequence with all of its fencing and compensation intact; nothing about
   generations, tokens, deadlines or CAS predicates is duplicated here.
3. **execute the turn through the real worker.**  The same per-intent adapter is
   handed to :class:`AgentRunWorker`, whose ``tick`` runs the production
   claim → preflight → provider attempt journal → ``AgentTurnRunner`` →
   settlement path (heartbeat loop, ``unknown`` parking, stale-result discard,
   retry scheduling).  No second agent runtime is introduced: the turn itself
   still goes through ``SessionAgentTurnRunner`` and the durable
   ``agent_turns`` ledger.
4. **translate the tick into a dispatch outcome.**  The worker's decision is
   mapped onto the closed ``IntentDispatchOutcome`` vocabulary: a settled,
   parked-for-reconciliation or retry-scheduled run means the intent's work was
   performed (``delivered``); a superseded fence is quarantined, never retried; a
   deterministic scope refusal is terminal.

Idempotency (delivering one intent twice never executes twice)
-------------------------------------------------------------
Three independent layers, none of which trusts this module:

* **consumer** — ``reserve`` answers ``ALREADY_DELIVERED`` for a delivered intent
  and ``IN_FLIGHT_UNKNOWN`` for one that is mid-dispatch under another claim, so
  the dispatcher answers without ever entering a handler;
* **address** — the addressed candidate must still be the announced aggregate
  version and status; a run that already started, succeeded, failed or was
  cancelled is not claimable, so a re-delivered intent finds nothing to execute
  and answers ``delivered(intent_delivery_replay_suppressed)`` for a terminal run
  (or quarantines a run somebody else is currently driving);
* **turn ledger** — ``RunTurnRequest.turn_id`` is derived from the run and its
  fence generation, and ``AgentSessionService.start_detached_turn`` reserves that
  id durably, so even a duplicated call is an observation, not a second provider
  call.

Fail-closed rules encoded here
------------------------------
* A kind this handler does not execute is refused **terminally**
  (``intent_kind_unknown``): it never guesses what an unregistered kind means and
  never walks it through a retry ladder.
* A missing dependency (lease store, execution factory, run source, attempt
  journal, turn runner) is a **named blocker**: the intent is retried with
  ``agent_run_durable_claim_and_lease_unwired`` and is emphatically *not*
  delivered, because no execution happened.
* Retries from this module are bounded by the same poison-pill rule the
  dispatcher applies to its own decisions: at ``max_attempts`` a retry becomes a
  terminal ``intent_attempts_exhausted`` refusal instead of an endless ladder
  (the dispatcher records a handler's decision verbatim, so the bound has to
  live here).
* A stale fence is quarantined (``dispatch_outcome_unknown_requires_reconciliation``)
  rather than retried: retrying would re-execute a turn another generation may
  already be running.
* A transient read/projection failure is a bounded retry; a missing run row or a
  project-scope mismatch is a terminal ``intent_scope_unresolved`` refusal.

Secrets and payloads
--------------------
The handler reads **no** intent payload text: the prompt lives on the run row and
is read by the worker, and the retry decision carries reason codes only.  The
consumer's lease token never leaves ``ClaimedIntent``; the run lease owner is
derived from the consumer's *owner* id (an identity, not a credential).  Log
lines carry a kind name, a closed reason/blocker code and an exception class;
never a payload, a prompt, an identifier tuple or an exception message.

Blockers this module does not hide
----------------------------------
* ``agent_run_intent_reason_vocabulary_closed`` — ``IntentDispatchReason`` is a
  closed enum owned by ``dispatcher.py`` (outside this task's file zone), so a
  missing dependency has to be *recorded* as the existing
  ``agent_run_durable_claim_and_lease_unwired`` code even though the precise
  blocker (``agent_run_intent_turn_runner_unwired``, …) is known here.  The
  precise code is exposed on :meth:`AgentRunIntentHandler.report` and in the log;
  adding it to the audited vocabulary needs a one-line addition to
  ``dispatcher.py``.
* ``agent_run_approval_projection_unwired`` — approval kinds and the post-start
  ``agent_run_*`` projection kinds stay unregistered: their delivery is an
  external integration, not a run execution.  :class:`TerminalIntentRefusalHandler`
  exists so a host that decides those kinds will never be projected can park them
  terminally instead of letting them retry for a day.
* ``agent_run_intent_host_registration_pending`` — exporting the factory is not
  wiring it: the host resource (``core/infra/agent_run_worker_resource.py``,
  another task's zone) must still register these handlers on its dispatcher.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.application.agent_runs.attempts import ProviderAttemptJournal
from prodavan.application.agent_runs.dispatcher import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    ClaimedIntent,
    DispatchContext,
    DispatchHandler,
    IntentDispatchAction,
    IntentDispatchOutcome,
    IntentDispatchReason,
    IntentKind,
    ResolvedIntentTarget,
    delivered,
    quarantined,
    rejected,
)
from prodavan.application.agent_runs.execution import (
    AgentRunExecutionPort,
    AgentRunLease,
)
from prodavan.application.agent_runs.execution_adapter import FencedLeaseStore
from prodavan.application.agent_runs.lease import (
    TERMINAL_STATUSES,
    LeasePolicy,
    LeaseSlot,
    is_claimable,
)
from prodavan.application.agent_runs.worker import (
    AgentRunWorker,
    AgentTurnRunner,
    RunInputSource,
    WorkerConfig,
    WorkerConfigError,
    WorkerTick,
    WorkerTickStatus,
    WorkerUnavailable,
)
from prodavan.domain.agent.control_plane import AgentRunStatus

logger = logging.getLogger(__name__)

#: The only intent kind that means "execute a turn".  Every other kind is an
#: already-applied transition whose delivery is an external projection.
EXECUTABLE_INTENT_KINDS: frozenset[IntentKind] = frozenset({IntentKind.RUN_CREATED})

#: Run lease owner prefix.  It never carries the intent lease token: the run
#: lease is a different fence and must be derivable from an identity, not a
#: credential.
DEFAULT_OWNER_PREFIX = "agent-run-intent"
_OWNER_MAX = 128

DEFAULT_RETRY_DELAY_SECONDS = 5.0

#: Named blockers (a stable, redacted vocabulary, safe for logs and health).
BLOCKER_COMPOSITION_MISSING = "agent_run_intent_pipeline_missing"
BLOCKER_LEASE_STORE_UNWIRED = "agent_run_intent_lease_store_unwired"
BLOCKER_EXECUTION_FACTORY_UNWIRED = "agent_run_intent_execution_factory_unwired"
BLOCKER_EXECUTION_FACTORY_FAILED = "agent_run_intent_execution_factory_failed"
BLOCKER_RUN_SOURCE_UNWIRED = "agent_run_intent_run_input_source_unwired"
BLOCKER_JOURNAL_UNWIRED = "agent_run_intent_attempt_journal_unwired"
BLOCKER_TURN_RUNNER_UNWIRED = "agent_run_intent_turn_runner_unwired"
BLOCKER_WORKER_REFUSED = "agent_run_intent_worker_unavailable"
BLOCKER_WORKER_CONFIG_INVALID = "agent_run_intent_worker_config_invalid"

#: Tick statuses that mean "the addressed run got its one settlement write", i.e.
#: the intent's work was performed and must never be retried.
_SETTLED_TICK_STATUSES: frozenset[WorkerTickStatus] = frozenset(
    {
        WorkerTickStatus.SUCCEEDED,
        WorkerTickStatus.FAILED,
        WorkerTickStatus.RETRY_SCHEDULED,
        WorkerTickStatus.UNKNOWN_RECORDED,
    }
)

_FENCED_STORE_METHODS: tuple[str, ...] = (
    "database_now",
    "claim_scope",
    "select_claim_candidate",
    "cas_acquire",
    "cas_renew",
    "cas_release",
    "cas_park",
    "cas_fence",
    "read_slot",
)


def _required(value: str, name: str, *, max_length: int = _OWNER_MAX) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _status_value(value: Any) -> str | None:
    """Read a run status as the durable string, whatever shape the read port used."""
    if isinstance(value, AgentRunStatus):
        return value.value
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


class IntentHandlerUnavailable(RuntimeError):
    """A named dependency is missing or refused; nothing may report delivery."""

    def __init__(self, blocker: str, detail: str = "") -> None:
        self.blocker = _required(blocker, "blocker")
        self.detail = detail
        super().__init__(f"{self.blocker}: {detail}" if detail else self.blocker)


@dataclass(frozen=True, slots=True)
class AddressableRunScope:
    """The one run (and one aggregate version) an intent addresses.

    Every field is copied from the server-side resolution the dispatcher
    performed; nothing here is taken from the intent payload.
    """

    run_id: str
    tenant_id: str
    project_id: str
    session_id: str | None
    expected_status: AgentRunStatus
    expected_sequence: int

    def __post_init__(self) -> None:
        for name in ("run_id", "tenant_id", "project_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name, max_length=64))
        if self.session_id is not None:
            object.__setattr__(
                self, "session_id", _required(self.session_id, "session_id", max_length=64)
            )
        if not isinstance(self.expected_status, AgentRunStatus):
            raise ValueError("expected_status must be an AgentRunStatus")
        if (
            isinstance(self.expected_sequence, bool)
            or not isinstance(self.expected_sequence, int)
            or self.expected_sequence < 1
        ):
            raise ValueError("expected_sequence must be a positive integer")

    @classmethod
    def from_target(cls, target: ResolvedIntentTarget) -> AddressableRunScope:
        return cls(
            run_id=target.run_id,
            tenant_id=target.tenant_id,
            project_id=target.project_id,
            session_id=target.session_id,
            expected_status=target.run_status,
            expected_sequence=target.aggregate_sequence,
        )


class IntentAddressedLeaseStore:
    """A ``FencedLeaseStore`` whose only claim candidate is one addressed run.

    The adapter's ``claim_next`` is not addressable by contract, so intent
    delivery needs an addressed *candidate*.  This wrapper narrows exactly that
    one read (``select_claim_candidate``) and delegates **every** write verbatim,
    which keeps the fencing where it already lives:

    * ``cas_acquire``/``cas_renew``/``cas_release``/``cas_park``/``cas_fence`` are
      the store's own single-statement CAS predicates — generation, attempt,
      token, owner, deadline and status unchanged;
    * ``claim_scope`` pins the *store's* transaction, so the narrowed select and
      the CAS share one transaction exactly as in an unaddressed claim;
    * reads (``read_slot``, ``database_now``) are not narrowed: the adapter's
      start step and compensation must see the real row.

    The narrowing predicate is the addressing fence: the candidate is offered
    only when it is the same run, tenant, project and session the intent
    addressed, at exactly the announced aggregate sequence and status, and still
    claimable under the shared lease policy.  Anything else is "no candidate",
    which the adapter already treats as "nothing to claim" (never as a
    best-effort write).
    """

    def __init__(self, *, store: FencedLeaseStore, scope: AddressableRunScope) -> None:
        for name in _FENCED_STORE_METHODS:
            if not callable(getattr(store, name, None)):
                raise IntentHandlerUnavailable(
                    BLOCKER_LEASE_STORE_UNWIRED, f"lease store does not implement {name}"
                )
        if not isinstance(scope, AddressableRunScope):
            raise TypeError("scope must be an AddressableRunScope")
        self._store = store
        self._scope = scope

    @property
    def scope(self) -> AddressableRunScope:
        return self._scope

    # -- narrowed read ------------------------------------------------------

    async def select_claim_candidate(
        self, *, now: datetime, statuses: frozenset[str] | None = None
    ) -> LeaseSlot | None:
        """Return the addressed run's slot only if it is still exactly that run."""
        _aware(now, "now")
        slot = await self._store.read_slot(run_id=self._scope.run_id)
        if slot is None:
            return None
        if slot.tenant_id != self._scope.tenant_id:
            return None
        if slot.project_id != self._scope.project_id:
            return None
        if self._scope.session_id is not None and slot.session_id not in (
            None,
            self._scope.session_id,
        ):
            return None
        if statuses is not None and slot.status not in statuses:
            return None
        if slot.status != self._scope.expected_status.value:
            return None
        if slot.sequence != self._scope.expected_sequence:
            return None
        if not is_claimable(slot, now=now):
            return None
        return slot

    # -- verbatim delegation ------------------------------------------------

    def claim_scope(self):  # noqa: ANN201 - context manager returned verbatim
        """Share the underlying store's pinned transaction, never a second one."""
        return self._store.claim_scope()

    async def database_now(self) -> datetime:
        return await self._store.database_now()

    async def read_slot(self, *, run_id: str) -> LeaseSlot | None:
        return await self._store.read_slot(run_id=run_id)

    async def cas_acquire(
        self, *, slot: LeaseSlot, lease: AgentRunLease, now: datetime
    ) -> int:
        return await self._store.cas_acquire(slot=slot, lease=lease, now=now)

    async def cas_renew(
        self, *, lease: AgentRunLease, now: datetime, lease_until: datetime
    ) -> int:
        return await self._store.cas_renew(lease=lease, now=now, lease_until=lease_until)

    async def cas_release(self, *, lease: AgentRunLease, now: datetime, status: str) -> int:
        return await self._store.cas_release(lease=lease, now=now, status=status)

    async def cas_park(
        self, *, lease: AgentRunLease, now: datetime, retry_at: datetime | None
    ) -> int:
        return await self._store.cas_park(lease=lease, now=now, retry_at=retry_at)

    async def cas_fence(self, *, tenant_id: str, run_id: str, now: datetime) -> int:
        return await self._store.cas_fence(tenant_id=tenant_id, run_id=run_id, now=now)


@dataclass(frozen=True, slots=True)
class IntentExecutionPipeline:
    """Everything one intent needs to be executed, resolved once at startup.

    ``None`` is not "not needed" — it is "not assembled yet", and it is reported
    as a named blocker instead of being worked around.  ``execution_factory`` is
    called with the *narrowed* lease store and must return the real, fenced
    ``AgentRunExecutionPort`` (the host closes over its control plane and run
    read port; the application tier never imports infrastructure).
    """

    lease_store: FencedLeaseStore | None = None
    execution_factory: Callable[[FencedLeaseStore], AgentRunExecutionPort] | None = None
    runs: RunInputSource | None = None
    journal: ProviderAttemptJournal | None = None
    runner: AgentTurnRunner | None = None

    def __post_init__(self) -> None:
        if self.lease_store is not None:
            for name in _FENCED_STORE_METHODS:
                if not callable(getattr(self.lease_store, name, None)):
                    raise ValueError(f"lease_store must implement FencedLeaseStore.{name}")
        if self.execution_factory is not None and not callable(self.execution_factory):
            raise ValueError("execution_factory must be callable")
        if self.runs is not None and not callable(getattr(self.runs, "get_run", None)):
            raise ValueError("runs must implement RunInputSource.get_run")
        if self.journal is not None:
            for name in ("begin", "finish", "unresolved_for_run"):
                if not callable(getattr(self.journal, name, None)):
                    raise ValueError(f"journal must implement ProviderAttemptJournal.{name}")
        if self.runner is not None and not callable(getattr(self.runner, "execute", None)):
            raise ValueError("runner must implement AgentTurnRunner.execute")

    @property
    def blockers(self) -> tuple[str, ...]:
        """Named blockers for the members that are not assembled (fixed order)."""
        missing: list[str] = []
        if self.lease_store is None:
            missing.append(BLOCKER_LEASE_STORE_UNWIRED)
        if self.execution_factory is None:
            missing.append(BLOCKER_EXECUTION_FACTORY_UNWIRED)
        if self.runs is None:
            missing.append(BLOCKER_RUN_SOURCE_UNWIRED)
        if self.journal is None:
            missing.append(BLOCKER_JOURNAL_UNWIRED)
        if self.runner is None:
            missing.append(BLOCKER_TURN_RUNNER_UNWIRED)
        return tuple(missing)

    @property
    def wired(self) -> bool:
        return not self.blockers


@dataclass(slots=True)
class IntentHandlerReport:
    """Bounded, redacted counters for one handler instance (no payloads)."""

    delivered: int = 0
    retried: int = 0
    rejected: int = 0
    quarantined: int = 0
    replays: int = 0
    ticks: int = 0
    last_action: str | None = None
    last_reason: str | None = None
    last_blocker: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "retried": self.retried,
            "rejected": self.rejected,
            "quarantined": self.quarantined,
            "replays": self.replays,
            "ticks": self.ticks,
            "last_action": self.last_action,
            "last_reason": self.last_reason,
            "last_blocker": self.last_blocker,
        }


class AgentRunIntentHandler:
    """``DispatchHandler`` that executes the ``agent_run.created`` intent.

    One instance may serve one kind or a whole registry: it holds no per-intent
    state besides redacted counters, and every dispatch builds its own narrowed
    lease store + adapter + worker, so two intents can never share a fence.
    """

    def __init__(
        self,
        *,
        pipeline: IntentExecutionPipeline | None = None,
        owner_prefix: str = DEFAULT_OWNER_PREFIX,
        lease_policy: LeasePolicy | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        max_retry_backoff_seconds: float = DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if pipeline is not None and not isinstance(pipeline, IntentExecutionPipeline):
            raise TypeError("pipeline must be an IntentExecutionPipeline")
        if lease_policy is not None and not isinstance(lease_policy, LeasePolicy):
            raise TypeError("lease_policy must be a LeasePolicy")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_backoff_seconds <= 0 or max_retry_backoff_seconds <= 0:
            raise ValueError("retry backoff bounds must be positive")
        if max_retry_backoff_seconds < retry_backoff_seconds:
            raise ValueError("max_retry_backoff_seconds must not be below retry_backoff_seconds")
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be positive")
        self._pipeline = pipeline
        self._owner_prefix = _required(owner_prefix, "owner_prefix", max_length=64)
        self._lease_policy = lease_policy or LeasePolicy()
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_retry_backoff_seconds = max_retry_backoff_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._report = IntentHandlerReport()
        self._logged_blockers: set[str] = set()

    # -- introspection -----------------------------------------------------

    @property
    def pipeline(self) -> IntentExecutionPipeline | None:
        return self._pipeline

    @property
    def blockers(self) -> tuple[str, ...]:
        """Named blockers this handler would report right now (fixed codes only)."""
        pipeline = self._pipeline
        if pipeline is None:
            missing = [BLOCKER_COMPOSITION_MISSING]
        else:
            missing = list(pipeline.blockers)
        last = self._report.last_blocker
        if last is not None and last not in missing:
            missing.append(last)
        return tuple(missing)

    def report(self) -> dict[str, Any]:
        """Redacted handler state for a health probe or an operator."""
        return {
            "handler": "agent_run_intent_handler",
            "kinds": sorted(kind.value for kind in EXECUTABLE_INTENT_KINDS),
            "blocked": bool(self.blockers),
            "blockers": list(self.blockers),
            **self._report.snapshot(),
        }

    # -- DispatchHandler ---------------------------------------------------

    async def __call__(self, context: DispatchContext) -> IntentDispatchOutcome:
        if not isinstance(context, DispatchContext):
            raise TypeError("the intent handler requires a DispatchContext")
        if context.kind not in EXECUTABLE_INTENT_KINDS:
            # Nothing here may interpret a kind it does not execute: retrying it
            # would walk a handoff nobody owns towards a poison pill, so the
            # refusal is terminal and redacted.
            return self._record(rejected(IntentDispatchReason.UNKNOWN_KIND))

        pipeline = self._pipeline
        blockers = (BLOCKER_COMPOSITION_MISSING,) if pipeline is None else pipeline.blockers
        if blockers:
            self._note_blocker(blockers[0], kind=context.kind)
            # A missing executor is legitimate work that simply cannot run yet:
            # retry (bounded) and never claim delivery for it.
            return self._record(self._retry(context.intent, IntentDispatchReason.EXECUTION_UNWIRED))

        assert pipeline is not None  # narrowed by the blockers check above
        try:
            return self._record(await self._execute(context, pipeline))
        except IntentHandlerUnavailable as unavailable:
            self._note_blocker(unavailable.blocker, kind=context.kind)
            return self._record(self._retry(context.intent, IntentDispatchReason.EXECUTION_UNWIRED))

    # -- execution ---------------------------------------------------------

    async def _execute(
        self,
        context: DispatchContext,
        pipeline: IntentExecutionPipeline,
    ) -> IntentDispatchOutcome:
        intent = context.intent
        target = context.target
        scope = AddressableRunScope.from_target(target)
        store = IntentAddressedLeaseStore(store=_lease_store(pipeline), scope=scope)
        port = _execution_port(pipeline, store)

        # Only the worker's own composition refusals are translated here; an
        # unexpected fault propagates and the dispatcher turns it into its own
        # bounded ``intent_handler_failed`` retry, which keeps the reason honest.
        try:
            worker = AgentRunWorker(
                port=port,
                runs=_run_source(pipeline),
                journal=_journal(pipeline),
                runner=_turn_runner(pipeline),
                config=WorkerConfig(owner_id=self._owner_id(intent), lease_policy=self._lease_policy),
                clock=self._clock,
            )
        except WorkerUnavailable as unavailable:
            raise IntentHandlerUnavailable(unavailable.blocker, "run worker refused") from unavailable
        except WorkerConfigError as invalid:
            raise IntentHandlerUnavailable(
                BLOCKER_WORKER_CONFIG_INVALID, type(invalid).__qualname__
            ) from invalid

        tick = await worker.tick()
        self._report.ticks += 1
        return await self._classify(
            intent=intent, target=target, pipeline=pipeline, tick=tick
        )

    async def _classify(
        self,
        *,
        intent: ClaimedIntent,
        target: ResolvedIntentTarget,
        pipeline: IntentExecutionPipeline,
        tick: WorkerTick,
    ) -> IntentDispatchOutcome:
        """Map the worker's decision onto the closed dispatch vocabulary."""
        status = tick.status
        if status is WorkerTickStatus.IDLE:
            return await self._unclaimed(intent=intent, target=target, pipeline=pipeline)
        if status in _SETTLED_TICK_STATUSES:
            # The run got exactly one settlement write (or was parked for
            # reconciliation).  The intent's work was performed and must never be
            # retried: a second attempt would re-enter the provider.
            return delivered()
        if status is WorkerTickStatus.DISCARDED_STALE:
            # Our fence was superseded while the turn was in flight.  The result
            # was discarded as evidence; the run belongs to another generation and
            # nothing here knows what that generation will conclude.
            return quarantined(IntentDispatchReason.OUTCOME_UNKNOWN)
        if status is WorkerTickStatus.REFUSED:
            # Deterministic scope refusal (run invisible in the tenant scope or a
            # project mismatch): retrying cannot fix it, so refuse terminally.
            return rejected(IntentDispatchReason.SCOPE_UNRESOLVED)
        # RELEASED_ON_SHUTDOWN / ERROR: no settlement was written, so the run may
        # still be claimed later.  Retry, bounded like every other decision here.
        return self._retry(intent, IntentDispatchReason.HANDLER_FAILED)

    async def _unclaimed(
        self,
        *,
        intent: ClaimedIntent,
        target: ResolvedIntentTarget,
        pipeline: IntentExecutionPipeline,
    ) -> IntentDispatchOutcome:
        """Decide what a "nothing was claimable" answer means for this intent.

        The addressed claim returns ``None`` when the run is no longer at the
        announced version/status, is leased by somebody else, or its start
        transition did not apply.  Reading the run back separates "the work is
        already concluded" (replay: delivered, never executed again) from "another
        actor is driving it" (quarantine, never execute twice) from "still
        claimable, the race was lost" (bounded retry).
        """
        runs = _run_source(pipeline)
        try:
            view = await runs.get_run(tenant_id=target.tenant_id, run_id=target.run_id)
        except Exception as exc:  # noqa: BLE001 - a failed read is a transient retry
            logger.warning(
                "agent intent run projection read failed kind=%s error=%s",
                intent.event_type,
                type(exc).__qualname__,
            )
            return self._retry(intent, IntentDispatchReason.HANDLER_FAILED)

        if view is None:
            return rejected(IntentDispatchReason.SCOPE_UNRESOLVED)
        status = _status_value(getattr(view, "status", None))
        if status is None:
            return rejected(IntentDispatchReason.SCOPE_UNRESOLVED)
        if status in TERMINAL_STATUSES:
            # The run already finished: the handoff is satisfied, and a retry
            # could only ever try to execute a terminal run again.
            self._report.replays += 1
            return delivered(IntentDispatchReason.REPLAY_SUPPRESSED)
        if status == AgentRunStatus.RUNNING.value:
            # Another claim owns this run right now.  Executing it here would run
            # the same turn twice, so the honest answer is "unknown outcome".
            return quarantined(IntentDispatchReason.OUTCOME_UNKNOWN)
        return self._retry(intent, IntentDispatchReason.HANDLER_FAILED)

    # -- helpers -----------------------------------------------------------

    def _owner_id(self, intent: ClaimedIntent) -> str:
        """Run lease owner derived from the consumer owner, never its token."""
        return f"{self._owner_prefix}:{intent.lease_owner}"[:_OWNER_MAX]

    def _retry(self, intent: ClaimedIntent, reason: IntentDispatchReason) -> IntentDispatchOutcome:
        """Bounded retry; the poison-pill guard has to live on this side.

        ``EnqueueIntentDispatcher`` records a handler's decision verbatim, so a
        handler that retried unconditionally would bypass the attempts cap it
        applies to its own retries.  At ``max_attempts`` this becomes the same
        terminal ``intent_attempts_exhausted`` refusal.

        The delay is exponential in the attempt counter and floored by
        ``retry_delay_seconds``, so an intent is never scheduled back into the
        claim window in the same instant it was leased, and capped by
        ``max_retry_backoff_seconds`` so a poison pill cannot hide for a week.
        """
        if intent.attempts >= self._max_attempts:
            return rejected(IntentDispatchReason.ATTEMPTS_EXHAUSTED)
        delay = min(
            self._max_retry_backoff_seconds,
            max(
                self._retry_delay_seconds,
                self._retry_backoff_seconds * (2 ** (intent.attempts - 1)),
            ),
        )
        return IntentDispatchOutcome(
            action=IntentDispatchAction.RETRY,
            reason=reason,
            retry_at=intent.claimed_at + timedelta(seconds=delay),
        )

    def _record(self, outcome: IntentDispatchOutcome) -> IntentDispatchOutcome:
        """Count the decision and expose only its closed codes."""
        if outcome.action is IntentDispatchAction.DELIVERED:
            self._report.delivered += 1
        elif outcome.action is IntentDispatchAction.RETRY:
            self._report.retried += 1
        elif outcome.action is IntentDispatchAction.REJECTED:
            self._report.rejected += 1
        else:
            self._report.quarantined += 1
        self._report.last_action = outcome.action.value
        self._report.last_reason = outcome.reason.value
        return outcome

    def _note_blocker(self, blocker: str, *, kind: IntentKind) -> None:
        """Log a named blocker once per code; never a payload, never a message."""
        self._report.last_blocker = blocker
        if blocker in self._logged_blockers:
            return
        self._logged_blockers.add(blocker)
        logger.warning(
            "agent intent handler idle kind=%s blocker=%s", kind.value, blocker
        )


class TerminalIntentRefusalHandler:
    """Terminal, redacted refusal for a kind nothing will ever execute.

    Used for kinds whose delivery is an external projection this deployment does
    not have (approval fan-out, post-start run events).  Retrying such a kind for
    a day only hides the fact that no integration owns it; a terminal refusal
    records ``action=reject reason=<code>`` and pins ``attempts`` exactly as the
    poison-pill guard would, only earlier and with the real reason.
    """

    def __init__(
        self, *, reason: IntentDispatchReason = IntentDispatchReason.HANDLER_NOT_WIRED
    ) -> None:
        if not isinstance(reason, IntentDispatchReason):
            raise TypeError("reason must be an IntentDispatchReason")
        if reason is IntentDispatchReason.DELIVERED:
            raise ValueError("a terminal refusal cannot carry the delivered reason code")
        self.reason = reason

    async def __call__(self, context: DispatchContext) -> IntentDispatchOutcome:
        if not isinstance(context, DispatchContext):
            raise TypeError("the intent handler requires a DispatchContext")
        return rejected(self.reason)


# --------------------------------------------------------------------------- #
# Composition helpers
# --------------------------------------------------------------------------- #


def _lease_store(pipeline: IntentExecutionPipeline) -> FencedLeaseStore:
    store = pipeline.lease_store
    if store is None:  # pragma: no cover - ``blockers`` already refused this
        raise IntentHandlerUnavailable(BLOCKER_LEASE_STORE_UNWIRED)
    return store


def _execution_port(
    pipeline: IntentExecutionPipeline, store: FencedLeaseStore
) -> AgentRunExecutionPort:
    factory = pipeline.execution_factory
    if factory is None:  # pragma: no cover - ``blockers`` already refused this
        raise IntentHandlerUnavailable(BLOCKER_EXECUTION_FACTORY_UNWIRED)
    try:
        port = factory(store)
    except Exception as exc:  # noqa: BLE001 - composition failure is a named blocker
        raise IntentHandlerUnavailable(
            BLOCKER_EXECUTION_FACTORY_FAILED, type(exc).__qualname__
        ) from exc
    if not callable(getattr(port, "claim_next", None)):
        raise IntentHandlerUnavailable(
            BLOCKER_EXECUTION_FACTORY_UNWIRED, "factory did not return an execution port"
        )
    return port


def _run_source(pipeline: IntentExecutionPipeline) -> RunInputSource:
    runs = pipeline.runs
    if runs is None:  # pragma: no cover - ``blockers`` already refused this
        raise IntentHandlerUnavailable(BLOCKER_RUN_SOURCE_UNWIRED)
    return runs


def _journal(pipeline: IntentExecutionPipeline) -> ProviderAttemptJournal:
    journal = pipeline.journal
    if journal is None:  # pragma: no cover - ``blockers`` already refused this
        raise IntentHandlerUnavailable(BLOCKER_JOURNAL_UNWIRED)
    return journal


def _turn_runner(pipeline: IntentExecutionPipeline) -> AgentTurnRunner:
    runner = pipeline.runner
    if runner is None:  # pragma: no cover - ``blockers`` already refused this
        raise IntentHandlerUnavailable(BLOCKER_TURN_RUNNER_UNWIRED)
    return runner


def build_run_intent_handler(
    *,
    pipeline: IntentExecutionPipeline | None = None,
    owner_prefix: str = DEFAULT_OWNER_PREFIX,
    lease_policy: LeasePolicy | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    max_retry_backoff_seconds: float = DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> AgentRunIntentHandler:
    """Build the executor for every kind in :data:`EXECUTABLE_INTENT_KINDS`.

    A ``None`` pipeline is deliberate: the host may compose the handler before
    its execution adapter exists, and each dispatch then reports a named blocker
    and retries instead of consuming the intent.  Pass it to
    ``EnqueueIntentDispatcher.register`` or as ``handlers=``.
    """
    return AgentRunIntentHandler(
        pipeline=pipeline,
        owner_prefix=owner_prefix,
        lease_policy=lease_policy,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        max_retry_backoff_seconds=max_retry_backoff_seconds,
        retry_delay_seconds=retry_delay_seconds,
        clock=clock,
    )


def build_run_intent_handlers(
    *,
    pipeline: IntentExecutionPipeline | None = None,
    owner_prefix: str = DEFAULT_OWNER_PREFIX,
    lease_policy: LeasePolicy | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    max_retry_backoff_seconds: float = DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> dict[IntentKind, DispatchHandler]:
    """Registry keyed by executable kind — the shape ``EnqueueIntentDispatcher`` takes.

    This is the factory a host resource plugs into its dispatcher instead of
    ``build_execution_handlers`` (which can only register a non-addressable
    claim).  One handler instance serves the registry; it holds no per-intent
    state, and every dispatch builds its own fence.
    """
    handler = build_run_intent_handler(
        pipeline=pipeline,
        owner_prefix=owner_prefix,
        lease_policy=lease_policy,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        max_retry_backoff_seconds=max_retry_backoff_seconds,
        retry_delay_seconds=retry_delay_seconds,
        clock=clock,
    )
    return {kind: handler for kind in EXECUTABLE_INTENT_KINDS}


def build_terminal_refusal_handlers(
    kinds: Iterable[IntentKind],
    *,
    reason: IntentDispatchReason = IntentDispatchReason.HANDLER_NOT_WIRED,
) -> dict[IntentKind, DispatchHandler]:
    """Registry that parks the given kinds terminally instead of retrying them."""
    handler = TerminalIntentRefusalHandler(reason=reason)
    registry: dict[IntentKind, DispatchHandler] = {}
    for kind in kinds:
        if not isinstance(kind, IntentKind):
            raise TypeError("kinds must be IntentKind members")
        if kind in EXECUTABLE_INTENT_KINDS:
            raise ValueError(f"{kind.value} is executed by the run intent handler")
        registry[kind] = handler
    return registry


__all__ = [
    "AddressableRunScope",
    "AgentRunIntentHandler",
    "BLOCKER_COMPOSITION_MISSING",
    "BLOCKER_EXECUTION_FACTORY_FAILED",
    "BLOCKER_EXECUTION_FACTORY_UNWIRED",
    "BLOCKER_JOURNAL_UNWIRED",
    "BLOCKER_LEASE_STORE_UNWIRED",
    "BLOCKER_RUN_SOURCE_UNWIRED",
    "BLOCKER_TURN_RUNNER_UNWIRED",
    "BLOCKER_WORKER_CONFIG_INVALID",
    "BLOCKER_WORKER_REFUSED",
    "DEFAULT_OWNER_PREFIX",
    "DEFAULT_RETRY_DELAY_SECONDS",
    "EXECUTABLE_INTENT_KINDS",
    "IntentAddressedLeaseStore",
    "IntentExecutionPipeline",
    "IntentHandlerReport",
    "IntentHandlerUnavailable",
    "TerminalIntentRefusalHandler",
    "build_run_intent_handler",
    "build_run_intent_handlers",
    "build_terminal_refusal_handlers",
]
