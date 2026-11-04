"""Durable, generation-fenced Agent Run worker: claim → execute → terminal transition.

What this module is
-------------------
``application/agent_runs/execution.py`` defines the *contract* for a fenced run
worker and fails closed until something implements it.  This module is the
consumer side of that contract: the loop that claims a run, drives the real
agent turn, renews the lease while the provider works, and writes exactly one
terminal transition — or refuses to write anything at all.

It is deliberately split into three tiers so every rule can be reasoned about
without a database:

1. **pure planning** — :func:`plan_settlement` decides *what* to write from a
   provider result; :class:`Settlement` is the decision.  No I/O happens here,
   which is what makes "unknown never becomes success" checkable.
2. **orchestration** — :class:`AgentRunWorker` owns the claim/heartbeat/settle
   sequence and the fence.  It talks only to protocols.
3. **adapters** — :class:`SessionAgentTurnRunner` binds the orchestration to the
   *real* bootstrap path (``AgentSessionService.start_detached_turn`` +
   ``iter_turn_tail``), not a stub.

Fencing invariants (why a stale worker cannot publish)
------------------------------------------------------
* Every write carries the lease: ``generation``, ``lease_token`` and the
  ``sequence`` observed at claim time.  The port itself is a CAS, so a stale
  worker gets ``False`` back instead of overwriting state.
* Before *every* write the worker re-checks its fence.  A heartbeat returning
  ``False``, raising, or a cancellation observed in the run row sets
  ``superseded`` permanently; nothing is written after that point.
* A heartbeat that is not confirmed within ``heartbeat_interval`` before the
  write is treated as a lost lease (fail closed), because between the last
  confirmed beat and now another worker may have claimed the run.
* A cancelled run bumps the generation (see
  :func:`prodavan.application.agent_runs.execution.AgentRunExecutionPort.cancel_run`),
  so the old worker's token no longer matches and its result is recorded as
  ``discarded_stale`` evidence, never as run state.
* Restart never resets the generation: a fresh claim increments it.  Nothing in
  this module writes ``generation`` itself — only the lease store may.

Provider-outcome journal (why a crash cannot blind-retry)
---------------------------------------------------------
:mod:`prodavan.application.agent_runs.attempts` records one attempt per
``(run, generation, attempt)``.  The worker writes ``started`` *before* calling
the provider and a terminal attempt state after it.  If it dies in between, the
attempt stays unresolved, the next claim refuses to call the provider again and
parks the run for reconciliation — the only fail-closed answer when nobody knows
whether the model already answered.

Blockers (also listed in the task report)
-----------------------------------------
* ``agent_run_lease_release_unwired`` — ``AgentRunExecutionPort`` has no
  ``release_lease``.  Graceful shutdown therefore releases the lease only when
  the adapter exposes that method; otherwise the worker stops touching the run
  and lets the TTL lapse (safe: the next claim increments the generation).  A
  half-written "requeue" without a domain event would corrupt the control plane.
* ``agent_run_provider_attempt_journal_unwired`` — durable attempt storage is
  an abstraction here; the table/revision is a separate migration slice.
* ``agent_run_reconciled_terminal_transition_unwired`` — once a reconciler
  proves the outcome of an ``unknown`` attempt, ``AgentRunStatus`` has no
  non-terminal "needs reconciliation" member and the port has no
  ``agent_run.reconciled`` transition, so a parked run can only be failed with
  ``agent_run.provider_outcome_unknown`` until that slice lands.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from .attempts import (
    AttemptJournalUnavailable,
    AttemptOutcome,
    AttemptRejected,
    AttemptState,
    ProviderAttempt,
    ProviderAttemptJournal,
    build_attempt,
    canonical_digest,
    request_digest,
)
from .execution import (
    AgentRunExecutionPort,
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
    validate_retry_result,
)
from .lease import LEASE_REQUEUE_BLOCKER, LeasePolicy, LeaseRefusal
from .service import EXECUTION_BLOCKERS

logger = logging.getLogger(__name__)

WORKER_BLOCKER = EXECUTION_BLOCKERS["run_claim"]
LEASE_RELEASE_BLOCKER = LEASE_REQUEUE_BLOCKER
RECONCILE_BLOCKER = EXECUTION_BLOCKERS["provider_unknown_outcome"]

FAILURE_PREFIX = "agent_run."
FAILURE_EMPTY_RESULT = "agent_run.empty_result"
FAILURE_PROVIDER_FAILED = "agent_run.provider_failed"
FAILURE_RETRY_EXHAUSTED = "agent_run.retry_exhausted"
FAILURE_INPUT_INVALID = "agent_run.input_invalid"
FAILURE_INPUT_UNAVAILABLE = "agent_run.input_unavailable"
FAILURE_UNRESOLVED_ATTEMPT = "agent_run.unresolved_provider_attempt"
FAILURE_OUTCOME_UNKNOWN = "agent_run.provider_outcome_unknown"
FAILURE_WORKER_SHUTDOWN = "agent_run.worker_shutdown"
FAILURE_LEASE_LOST = "agent_run.lease_lost"

_MAX_REASON = 512


class WorkerConfigError(ValueError):
    """Invalid worker configuration."""


class WorkerUnavailable(RuntimeError):
    """A required durable dependency is missing; the worker must not start."""

    def __init__(self, blocker: str, detail: str) -> None:
        self.blocker = blocker
        super().__init__(f"{blocker}: {detail}")


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


@dataclass(frozen=True, slots=True)
class WorkerRetryPolicy:
    """Bounded exponential retry for *proven* retryable provider failures."""

    max_attempts: int = 3
    initial_delay: timedelta = timedelta(seconds=10)
    max_delay: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise WorkerConfigError("max_attempts must be positive")
        if self.initial_delay < timedelta(seconds=1) or self.max_delay < self.initial_delay:
            raise WorkerConfigError("retry delays are invalid")

    def delay_for(self, attempt: int) -> timedelta:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        exponent = min(attempt - 1, 16)
        delay = self.initial_delay * (2**exponent)
        return min(delay, self.max_delay)

    def retry_at(self, *, attempt: int, failed_at: datetime) -> datetime | None:
        if attempt >= self.max_attempts:
            return None
        return failed_at + self.delay_for(attempt)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Timing and identity knobs for one worker process."""

    owner_id: str
    lease_policy: LeasePolicy = field(default_factory=LeasePolicy)
    heartbeat_interval: timedelta = timedelta(seconds=10)
    poll_interval: timedelta = timedelta(seconds=2)
    shutdown_grace: timedelta = timedelta(seconds=20)
    retry_policy: WorkerRetryPolicy = field(default_factory=WorkerRetryPolicy)

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _required(self.owner_id, "owner_id"))
        if self.poll_interval < timedelta(seconds=0) or self.poll_interval > timedelta(minutes=5):
            raise WorkerConfigError("poll_interval is outside the supported range")
        if self.shutdown_grace < timedelta(seconds=1) or self.shutdown_grace > timedelta(minutes=10):
            raise WorkerConfigError("shutdown_grace is outside the supported range")
        if not isinstance(self.lease_policy, LeasePolicy):
            raise WorkerConfigError("lease_policy must be a LeasePolicy")
        # The lease must outlive at least two missed heartbeats, otherwise a
        # healthy worker loses a run under normal scheduling jitter.
        if self.heartbeat_interval * 2 >= self.lease_policy.ttl:
            raise WorkerConfigError("lease ttl must exceed two heartbeat intervals")


# ---------------------------------------------------------------------------
# Run input / turn request
# ---------------------------------------------------------------------------


class RunInputInvalid(ValueError):
    """The persisted run input cannot be executed (fail closed, no provider call)."""


@dataclass(frozen=True, slots=True)
class RunInput:
    """The executable part of ``agent_runs.input_payload``."""

    text: str
    attachment_refs: tuple[str, ...] = ()
    model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _required(self.text, "text", max_length=200_000))
        if len(self.attachment_refs) > 32:
            raise RunInputInvalid("too many attachment references")
        for ref in self.attachment_refs:
            _required(ref, "attachment_ref", max_length=512)
        if self.model is not None:
            object.__setattr__(self, "model", _required(self.model, "model", max_length=128))


_MAX_ATTACHMENTS = 32


def parse_run_input(payload: Mapping[str, Any] | None) -> RunInput:
    """Extract the prompt/attachments/model from a persisted run input.

    Missing or non-string ``text`` is an invalid input, not an empty prompt: a
    worker must never call the provider with an unintended empty message.
    """
    if payload is None:
        raise RunInputInvalid("run input payload is missing")
    if not isinstance(payload, Mapping):
        raise RunInputInvalid("run input payload must be an object")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RunInputInvalid("run input payload has no prompt text")
    refs = payload.get("attachment_refs") or ()
    if not isinstance(refs, (list, tuple)) or not all(isinstance(ref, str) for ref in refs):
        raise RunInputInvalid("run input attachment_refs must be a list of strings")
    if len(refs) > _MAX_ATTACHMENTS:
        raise RunInputInvalid("too many attachment references")
    model = payload.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise RunInputInvalid("run input model must be a non-empty string")
    return RunInput(text=text, attachment_refs=tuple(refs), model=model)


@dataclass(frozen=True, slots=True)
class RunTurnRequest:
    """Everything the turn runner needs; no lease token leaves this object."""

    tenant_id: str
    project_id: str
    run_id: str
    session_id: str
    employee_id: str | None
    generation: int
    attempt: int
    trace_id: str
    turn_id: str
    input: RunInput

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "run_id", "session_id", "trace_id", "turn_id"):
            _required(getattr(self, name), name, max_length=128)
        if self.employee_id is not None:
            _required(self.employee_id, "employee_id", max_length=64)
        if self.generation < 1 or self.attempt < 1:
            raise ValueError("generation and attempt must be positive")
        if len(self.turn_id) > 64:
            raise ValueError("turn_id exceeds the agent turn ledger limit")


class CancelSignal:
    """Cooperative cancellation handle shared between the worker and a runner."""

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str) -> None:
        if not self._reason:
            self._reason = _required(reason, "cancel reason", max_length=_MAX_REASON)
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


class AgentTurnRunner(Protocol):
    """Executes one turn through the real agent bootstrap path.

    Implementations must:

    * be safe to call with the same ``turn_id`` more than once — the durable turn
      ledger makes the second call an observation, never a second provider call;
    * honour ``cancel`` promptly and report :attr:`ProviderOutcome.UNKNOWN` when
      the provider may already have been invoked (never ``FAILED``/``SUCCEEDED``);
    * raise :class:`RunInputInvalid` only *before* touching the provider;
    * never raise for an ordinary provider error — return a result instead.
    """

    async def execute(
        self, *, request: RunTurnRequest, cancel: CancelSignal
    ) -> ProviderExecutionResult: ...


class RunInputSource(Protocol):
    """Reads the persisted run projection (implemented by the control-plane read port)."""

    async def get_run(self, *, tenant_id: str, run_id: str): ...


# ---------------------------------------------------------------------------
# Settlement planning (pure)
# ---------------------------------------------------------------------------


class SettlementKind(StrEnum):
    SUCCEED = "succeed"
    FAIL = "fail"
    RETRY = "retry"
    UNKNOWN = "unknown"
    DISCARD = "discard"


@dataclass(frozen=True, slots=True)
class Settlement:
    """The single write the worker is allowed to perform for one result."""

    kind: SettlementKind
    reason: str
    failure_code: str | None = None
    retry_at: datetime | None = None
    output_payload: Mapping[str, Any] | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.reason, "reason", max_length=_MAX_REASON)
        if self.kind is SettlementKind.RETRY and self.retry_at is None:
            raise ValueError("a retry settlement requires retry_at")
        if self.kind is SettlementKind.FAIL and self.failure_code is None:
            raise ValueError("a fail settlement requires a failure code")
        if self.kind in {SettlementKind.DISCARD, SettlementKind.UNKNOWN} and self.output_payload is not None:
            raise ValueError("a non-success settlement cannot carry output")
        if self.retry_at is not None:
            _aware(self.retry_at, "retry_at")

    @property
    def writes_run_state(self) -> bool:
        return self.kind is not SettlementKind.DISCARD


def plan_settlement(
    result: ProviderExecutionResult,
    *,
    now: datetime,
    attempt: int,
    retry_policy: WorkerRetryPolicy,
    fence_ok: bool = True,
) -> Settlement:
    """Decide what to write for a provider result.

    This is the only place that can decide "success", and it refuses to do so
    for an unknown outcome, a stale fence, or a success with no output.
    """
    _aware(now, "now")
    if not isinstance(result, ProviderExecutionResult):
        raise ValueError("result must be a ProviderExecutionResult")
    if not fence_ok:
        return Settlement(
            kind=SettlementKind.DISCARD,
            reason="lease was superseded or expired before the result could be written",
        )

    outcome = result.outcome
    if outcome is ProviderOutcome.UNKNOWN:
        return Settlement(
            kind=SettlementKind.UNKNOWN,
            reason="provider outcome is ambiguous and must be reconciled",
            provider_request_id=result.provider_request_id,
        )

    if outcome is ProviderOutcome.SUCCEEDED:
        if not result.output_payload:
            return Settlement(
                kind=SettlementKind.FAIL,
                reason="provider reported success without an output payload",
                failure_code=FAILURE_EMPTY_RESULT,
                provider_request_id=result.provider_request_id,
            )
        return Settlement(
            kind=SettlementKind.SUCCEED,
            reason="provider reported success with a durable output payload",
            output_payload=result.output_payload,
            provider_request_id=result.provider_request_id,
        )

    if outcome is ProviderOutcome.FAILED:
        return Settlement(
            kind=SettlementKind.FAIL,
            reason="provider reported a non-retryable failure",
            failure_code=result.failure_code or FAILURE_PROVIDER_FAILED,
            provider_request_id=result.provider_request_id,
        )

    # ProviderOutcome.RETRYABLE_FAILURE
    retry_at = validate_retry_result(result)
    scheduled = retry_policy.retry_at(attempt=attempt, failed_at=now)
    if scheduled is None or retry_at > now + retry_policy.max_delay:
        return Settlement(
            kind=SettlementKind.FAIL,
            reason="retryable failure cannot be scheduled within the retry policy",
            failure_code=FAILURE_RETRY_EXHAUSTED,
            provider_request_id=result.provider_request_id,
        )
    return Settlement(
        kind=SettlementKind.RETRY,
        reason="provider reported a retryable failure with a durable retry time",
        retry_at=retry_at,
        # A retry is a *failure* attempt state (``known_retryable``), and the
        # journal refuses a failure transition without a bounded reason code.
        # The provider's own code is the honest one; the generic run-level code
        # is only the fallback when the runner withheld it.
        failure_code=result.failure_code or FAILURE_PROVIDER_FAILED,
        provider_request_id=result.provider_request_id,
    )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class WorkerTickStatus(StrEnum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    UNKNOWN_RECORDED = "unknown_recorded"
    DISCARDED_STALE = "discarded_stale"
    REFUSED = "refused"
    RELEASED_ON_SHUTDOWN = "released_on_shutdown"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class WorkerTick:
    """Outcome of one claim→execute→settle iteration (no secrets, no prompts)."""

    status: WorkerTickStatus
    run_id: str | None = None
    generation: int | None = None
    reason: str | None = None
    failure_code: str | None = None
    wrote_run_state: bool = False

    @property
    def claimed(self) -> bool:
        return self.run_id is not None


@dataclass(slots=True)
class _ActiveRun:
    lease: AgentRunLease
    attempt: ProviderAttempt
    cancel: CancelSignal
    fence_reason: LeaseRefusal | None = None
    last_confirmed_heartbeat: datetime | None = None
    heartbeat_task: asyncio.Task[None] | None = None

    @property
    def superseded(self) -> bool:
        return self.fence_reason is not None


class AgentRunWorker:
    """One worker process: claim eligible runs, execute them, settle them.

    The worker never enqueues into process memory, never retries an unknown
    outcome, and never writes after losing its fence.
    """

    def __init__(
        self,
        *,
        port: AgentRunExecutionPort,
        runs: RunInputSource,
        journal: ProviderAttemptJournal,
        runner: AgentTurnRunner,
        config: WorkerConfig,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        for name, value in (
            ("claim_next", getattr(port, "claim_next", None)),
            ("heartbeat", getattr(port, "heartbeat", None)),
            ("finish", getattr(port, "finish", None)),
            ("finish_unknown", getattr(port, "finish_unknown", None)),
            ("schedule_retry", getattr(port, "schedule_retry", None)),
        ):
            if not callable(value):
                raise WorkerUnavailable(
                    WORKER_BLOCKER,
                    f"execution port does not implement {name}",
                )
        if not callable(getattr(runs, "get_run", None)):
            raise WorkerUnavailable(WORKER_BLOCKER, "run input source has no get_run")
        if not isinstance(journal, ProviderAttemptJournal):
            raise WorkerUnavailable(
                "agent_run_provider_attempt_journal_unwired",
                "a durable provider attempt journal is required",
            )
        if not callable(getattr(runner, "execute", None)):
            raise WorkerUnavailable(WORKER_BLOCKER, "turn runner has no execute")
        if not isinstance(config, WorkerConfig):
            raise WorkerConfigError("config must be a WorkerConfig")
        self._port = port
        self._runs = runs
        self._journal = journal
        self._runner = runner
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stop: asyncio.Event | None = None
        self._active: _ActiveRun | None = None

    # -- public API --------------------------------------------------------

    @property
    def config(self) -> WorkerConfig:
        return self._config

    @property
    def busy(self) -> bool:
        return self._active is not None

    async def tick(self) -> WorkerTick:
        """Claim at most one run and drive it to one terminal transition."""
        now = self._now()
        try:
            lease = await self._port.claim_next(
                owner_id=self._config.owner_id,
                now=now,
                lease_until=self._config.lease_policy.deadline(now),
            )
        except Exception:
            logger.exception("agent run claim failed")
            return WorkerTick(WorkerTickStatus.ERROR, reason="claim_failed")

        if lease is None:
            return WorkerTick(WorkerTickStatus.IDLE)

        if self._stopping:
            # Do not start new work while draining; hand the lease straight back.
            released = await self._release_lease(lease)
            return WorkerTick(
                WorkerTickStatus.RELEASED_ON_SHUTDOWN,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="worker_stopping",
                wrote_run_state=released,
            )

        return await self._execute_claimed(lease)

    async def run_forever(self, *, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set, then drain without abandoning a lease."""
        self._stop = stop
        try:
            while not stop.is_set():
                tick = await self.tick()
                if tick.status is WorkerTickStatus.IDLE:
                    await self._wait_or_stop(stop, self._config.poll_interval)
                elif tick.status is WorkerTickStatus.ERROR:
                    await self._wait_or_stop(stop, self._config.poll_interval)
        finally:
            await self._drain()
            self._stop = None

    @property
    def _stopping(self) -> bool:
        return self._stop is not None and self._stop.is_set()

    # -- execution ---------------------------------------------------------

    async def _execute_claimed(self, lease: AgentRunLease) -> WorkerTick:
        try:
            projection = await self._runs.get_run(tenant_id=lease.tenant_id, run_id=lease.run_id)
        except Exception:
            logger.exception("agent run projection read failed run=%s", lease.run_id)
            return await self._fail_without_attempt(
                lease, FAILURE_INPUT_UNAVAILABLE, "run projection could not be read"
            )
        if projection is None:
            # The run vanished (tenant scope mismatch or deletion): never write.
            return WorkerTick(
                WorkerTickStatus.REFUSED,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="run_not_visible_in_tenant_scope",
            )
        if getattr(projection, "project_id", None) != lease.project_id:
            return WorkerTick(
                WorkerTickStatus.REFUSED,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="run_project_scope_mismatch",
            )
        session_id = getattr(projection, "session_id", None) or lease.session_id
        if not session_id:
            return await self._fail_without_attempt(
                lease, FAILURE_INPUT_INVALID, "run has no agent session to execute"
            )
        try:
            run_input = parse_run_input(getattr(projection, "input_payload", None))
        except RunInputInvalid:
            return await self._fail_without_attempt(
                lease, FAILURE_INPUT_INVALID, "run input payload is not executable"
            )

        unresolved = await self._journal.unresolved_for_run(run_id=lease.run_id)
        if unresolved is not None and unresolved.generation <= lease.generation:
            # A previous attempt never produced a known outcome. Re-calling the
            # provider could duplicate spend and side effects, so park instead.
            return await self._record_unknown(
                lease,
                provider_request_id=unresolved.provider_request_id,
                reason="previous provider attempt is unresolved; reconciliation required",
            )

        request = RunTurnRequest(
            tenant_id=lease.tenant_id,
            project_id=lease.project_id,
            run_id=lease.run_id,
            session_id=session_id,
            employee_id=getattr(projection, "requested_by_employee_id", None),
            generation=lease.generation,
            attempt=lease.attempt,
            trace_id=f"agent-run:{lease.run_id}:{lease.generation}",
            turn_id=f"run:{lease.run_id}:{lease.generation}",
            input=run_input,
        )
        frozen_text = run_input.text
        try:
            attempt = build_attempt(
                lease=lease,
                provider="agent-runtime",
                request_digest_value=request_digest(
                    provider="agent-runtime",
                    model=run_input.model,
                    session_id=session_id,
                    attachments=len(run_input.attachment_refs),
                    prompt=frozen_text,
                ),
                started_at=self._now(),
                deadline_at=lease.lease_until,
                metadata={"provider": "agent-runtime", "phase": "dispatch"},
            )
            start = await self._journal.begin(attempt)
        except AttemptJournalUnavailable:
            return await self._fail_without_attempt(
                lease, FAILURE_INPUT_UNAVAILABLE, "durable provider attempt journal is unavailable"
            )
        except Exception:
            logger.exception("attempt journal write failed run=%s", lease.run_id)
            return WorkerTick(
                WorkerTickStatus.ERROR,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="attempt_journal_write_failed",
            )
        # An existing attempt for this exact generation/attempt means the
        # provider call for this key may already have happened: replaying it is
        # not idempotent, so the run is parked instead of executed twice.
        if start.attempt.idempotency_key != attempt.idempotency_key or start.attempt.state is not AttemptState.STARTED:
            return await self._record_unknown(
                lease,
                provider_request_id=start.attempt.provider_request_id,
                reason="provider attempt key already used; reconciliation required",
            )
        attempt = start.attempt

        active = _ActiveRun(lease=lease, attempt=attempt, cancel=CancelSignal())
        self._active = active
        active.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(active), name=f"agent-run-heartbeat:{lease.run_id}"
        )
        try:
            result = await self._run_turn(active, request)
        finally:
            self._active = None
            if active.heartbeat_task is not None:
                active.heartbeat_task.cancel()
                await asyncio.gather(active.heartbeat_task, return_exceptions=True)
                active.heartbeat_task = None

        return await self._settle(active, result)

    async def _run_turn(self, active: _ActiveRun, request: RunTurnRequest) -> ProviderExecutionResult:
        try:
            return await self._runner.execute(request=request, cancel=active.cancel)
        except RunInputInvalid:
            return ProviderExecutionResult(
                outcome=ProviderOutcome.FAILED,
                failure_code=FAILURE_INPUT_INVALID,
            )
        except asyncio.CancelledError:
            active.cancel.cancel("worker_task_cancelled")
            return ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN)
        except Exception:  # noqa: BLE001 — an unknown failure is never success
            logger.exception("agent turn runner raised run=%s", request.run_id)
            return ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN)

    # -- heartbeat / fence -------------------------------------------------

    async def _heartbeat_loop(self, active: _ActiveRun) -> None:
        interval = self._config.heartbeat_interval.total_seconds()
        try:
            while not active.superseded and not active.cancel.cancelled:
                await asyncio.sleep(interval)
                await self._beat(active)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed beat is a lost fence, not a crash
            logger.exception("agent run heartbeat loop failed run=%s", active.lease.run_id)
            self._lose_fence(active, None)

    async def _beat(self, active: _ActiveRun) -> bool:
        """Renew the lease once; on any doubt, fence the worker out for good."""
        if active.superseded:
            return False
        now = self._now()
        try:
            held = await self._port.heartbeat(
                lease=active.lease,
                now=now,
                lease_until=self._config.lease_policy.deadline(now),
            )
        except Exception:  # noqa: BLE001 — no confirmed beat means no write is safe
            logger.exception("agent run heartbeat failed run=%s", active.lease.run_id)
            self._lose_fence(active, None)
            return False
        if not held:
            self._lose_fence(active, LeaseRefusal.GENERATION_STALE)
            return False
        active.last_confirmed_heartbeat = now
        return True

    def _lose_fence(self, active: _ActiveRun, refusal: LeaseRefusal | None) -> None:
        if active.fence_reason is None:
            active.fence_reason = refusal or LeaseRefusal.NOT_LEASED
            logger.warning(
                "agent run lease fenced out run=%s generation=%s reason=%s",
                active.lease.run_id,
                active.lease.generation,
                active.fence_reason.value,
            )
        active.cancel.cancel("lease_fenced_out")

    async def _confirm_fence(self, active: _ActiveRun) -> bool:
        """Prove the lease is still ours, immediately before a write.

        The claim is **not** evidence at write time: it happened before the
        provider call, which can outlast the whole lease TTL, and in the meantime
        an operator may have cancelled the run or another worker may have claimed
        it.  Only a heartbeat confirmed within ``heartbeat_interval`` proves
        ownership, so when the last confirmed beat is missing or older than that
        the worker performs one confirm beat *now* and:

        * a ``False`` answer, an exception or an expired deadline loses the fence
          permanently — the result becomes ``discarded_stale`` evidence and no
          run state is written;
        * a cooperative local cancel (shutdown) is **not** proof of supersession
          by itself: if the lease is still ours the run must be settled rather
          than left ``running`` forever;
        * races the worker cannot see are still caught by the port's CAS, which
          returns ``False`` and turns the result into ``discarded_stale``.
        """
        if active.superseded:
            return False
        last = active.last_confirmed_heartbeat
        if last is not None and self._now() - last <= self._config.heartbeat_interval:
            return True
        return await self._beat(active)

    # -- settlement --------------------------------------------------------

    async def _settle(self, active: _ActiveRun, result: ProviderExecutionResult) -> WorkerTick:
        lease = active.lease
        # Ownership is *proven* before the settlement is planned: a confirmed beat
        # extends the lease, a refused beat fences the worker out, and the plan is
        # then made on evidence rather than on the claim that preceded the turn.
        fence_ok = await self._confirm_fence(active)
        now = self._now()
        settlement = plan_settlement(
            result,
            now=now,
            attempt=lease.attempt,
            retry_policy=self._config.retry_policy,
            fence_ok=fence_ok,
        )

        if settlement.kind is SettlementKind.DISCARD:
            await self._record_attempt(active, result, AttemptOutcome.DISCARDED_STALE, FAILURE_LEASE_LOST)
            return WorkerTick(
                WorkerTickStatus.DISCARDED_STALE,
                run_id=lease.run_id,
                generation=lease.generation,
                reason=settlement.reason,
            )

        if settlement.kind is SettlementKind.UNKNOWN:
            return await self._record_unknown(
                lease,
                provider_request_id=settlement.provider_request_id,
                reason=settlement.reason,
                active=active,
                result=result,
            )

        try:
            if settlement.kind is SettlementKind.SUCCEED:
                applied = await self._port.finish(lease=lease, result=result, now=now)
                status = WorkerTickStatus.SUCCEEDED
                attempt_outcome = AttemptOutcome.SUCCEEDED
            elif settlement.kind is SettlementKind.RETRY:
                assert settlement.retry_at is not None
                applied = await self._port.schedule_retry(
                    lease=lease,
                    retry_at=settlement.retry_at,
                    failure_code=settlement.failure_code or FAILURE_PROVIDER_FAILED,
                    now=now,
                )
                status = WorkerTickStatus.RETRY_SCHEDULED
                attempt_outcome = AttemptOutcome.RETRYABLE
            else:
                applied = await self._port.finish(
                    lease=lease,
                    result=ProviderExecutionResult(
                        outcome=ProviderOutcome.FAILED,
                        failure_code=settlement.failure_code,
                    ),
                    now=now,
                )
                status = WorkerTickStatus.FAILED
                attempt_outcome = AttemptOutcome.FAILED
        except Exception:  # noqa: BLE001 — a failed write leaves the run for the reconciler
            logger.exception("agent run settlement write failed run=%s", lease.run_id)
            return WorkerTick(
                WorkerTickStatus.ERROR,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="settlement_write_failed",
                failure_code=settlement.failure_code,
            )

        if not applied:
            # The port's CAS matched zero rows: another generation won. The
            # result is evidence, not run state.
            await self._record_attempt(active, result, AttemptOutcome.DISCARDED_STALE, FAILURE_LEASE_LOST)
            return WorkerTick(
                WorkerTickStatus.DISCARDED_STALE,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="settlement_cas_lost_race",
            )

        await self._record_attempt(active, result, attempt_outcome, settlement.failure_code)
        return WorkerTick(
            status,
            run_id=lease.run_id,
            generation=lease.generation,
            reason=settlement.reason,
            failure_code=settlement.failure_code,
            wrote_run_state=True,
        )

    async def _record_attempt(
        self,
        active: _ActiveRun,
        result: ProviderExecutionResult,
        outcome: AttemptOutcome,
        failure_code: str | None,
    ) -> None:
        """Persist the attempt outcome; failure to do so only *adds* caution."""
        if outcome is AttemptOutcome.DISCARDED_STALE:
            failure_code = FAILURE_LEASE_LOST
        if outcome is AttemptOutcome.SUCCEEDED:
            failure_code = None
        if outcome is AttemptOutcome.RETRYABLE and failure_code is None:
            # ``known_retryable`` is a failure attempt state: the journal refuses
            # the transition without a bounded code, so never drop the provider's
            # own reason on the way in.
            failure_code = result.failure_code or FAILURE_PROVIDER_FAILED
        try:
            await self._journal.finish(
                active.attempt,
                outcome=outcome,
                at=self._now(),
                failure_code=failure_code,
                provider_request_id=result.provider_request_id,
                metadata={
                    "provider": "agent-runtime",
                    "provider_status": result.outcome.value,
                    "assistant_text_digest": _output_digest(result.output_payload),
                    "event_count": _output_event_count(result.output_payload),
                },
            )
        except AttemptRejected:
            logger.warning(
                "attempt journal refused outcome run=%s state=%s",
                active.lease.run_id,
                active.attempt.state.value,
            )
        except Exception:  # noqa: BLE001 — journal lag must not invent a run outcome
            logger.exception("attempt journal finish failed run=%s", active.lease.run_id)

    async def _record_unknown(
        self,
        lease: AgentRunLease,
        *,
        provider_request_id: str | None,
        reason: str,
        active: _ActiveRun | None = None,
        result: ProviderExecutionResult | None = None,
    ) -> WorkerTick:
        """Park the run for reconciliation — never a success, never a blind retry.

        The attempt is journalled *after* the run write so the record describes
        what actually happened to the run: ``unknown`` when the park was
        applied, ``discarded_stale`` when another generation won the race.
        """
        now = self._now()
        try:
            applied = await self._port.finish_unknown(
                lease=lease,
                provider_request_id=provider_request_id,
                observed_at=now,
            )
        except Exception:  # noqa: BLE001 — an unrecordable unknown still blocks retry
            logger.exception("agent run unknown-outcome write failed run=%s", lease.run_id)
            return WorkerTick(
                WorkerTickStatus.ERROR,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="unknown_outcome_write_failed",
                failure_code=FAILURE_OUTCOME_UNKNOWN,
            )
        if not applied:
            if active is not None:
                await self._record_attempt(
                    active,
                    result or ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN),
                    AttemptOutcome.DISCARDED_STALE,
                    FAILURE_LEASE_LOST,
                )
            return WorkerTick(
                WorkerTickStatus.DISCARDED_STALE,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="unknown_outcome_cas_lost_race",
            )
        if active is not None:
            await self._record_attempt(
                active,
                result or ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN),
                AttemptOutcome.UNKNOWN,
                None,
            )
        return WorkerTick(
            WorkerTickStatus.UNKNOWN_RECORDED,
            run_id=lease.run_id,
            generation=lease.generation,
            reason=reason,
            failure_code=FAILURE_OUTCOME_UNKNOWN,
            wrote_run_state=True,
        )

    async def _fail_without_attempt(
        self,
        lease: AgentRunLease,
        failure_code: str,
        reason: str,
    ) -> WorkerTick:
        """Fail a claim that never reached the provider (no attempt row exists)."""
        try:
            applied = await self._port.finish(
                lease=lease,
                result=ProviderExecutionResult(
                    outcome=ProviderOutcome.FAILED,
                    failure_code=failure_code,
                ),
                now=self._now(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("agent run preflight failure write failed run=%s", lease.run_id)
            return WorkerTick(
                WorkerTickStatus.ERROR,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="preflight_failure_write_failed",
                failure_code=failure_code,
            )
        if not applied:
            return WorkerTick(
                WorkerTickStatus.DISCARDED_STALE,
                run_id=lease.run_id,
                generation=lease.generation,
                reason="preflight_failure_cas_lost_race",
            )
        return WorkerTick(
            WorkerTickStatus.FAILED,
            run_id=lease.run_id,
            generation=lease.generation,
            reason=reason,
            failure_code=failure_code,
            wrote_run_state=True,
        )

    # -- shutdown ----------------------------------------------------------

    async def _drain(self) -> None:
        """Stop heartbeats, give the in-flight turn a grace window, then release."""
        active = self._active
        if active is None:
            return
        # Give the in-flight turn a bounded window to reach its own terminal
        # transition; a turn that finishes inside the window settles normally
        # (and clears ``_active``), so nothing is released underneath it.
        deadline = self._now() + self._config.shutdown_grace
        while self._active is not None and self._now() < deadline:
            await asyncio.sleep(0.05)
        active = self._active
        if active is None:
            return
        active.cancel.cancel("worker_shutdown")
        if active.heartbeat_task is not None:
            active.heartbeat_task.cancel()
        # The lease is never silently dropped: with ``release_lease`` the next
        # claim can start immediately, without it the TTL decides.
        await self._release_lease(active.lease)

    async def _release_lease(self, lease: AgentRunLease) -> bool:
        """Return an unused lease when the adapter supports it; otherwise keep it.

        Releasing without an accompanying domain event would desynchronise the
        control-plane aggregate, so a missing ``release_lease`` is *not* worked
        around here: the lease simply expires and the next claim increments the
        generation.
        """
        release = getattr(self._port, "release_lease", None)
        if not callable(release):
            logger.warning(
                "lease release is not wired (%s); run=%s keeps its lease until ttl",
                LEASE_RELEASE_BLOCKER,
                lease.run_id,
            )
            return False
        try:
            released = await release(lease=lease, now=self._now())
            return bool(released)
        except Exception:  # noqa: BLE001
            logger.exception("lease release failed run=%s", lease.run_id)
            return False

    # -- helpers -----------------------------------------------------------

    def _now(self) -> datetime:
        return _aware(self._clock(), "now")

    async def _wait_or_stop(self, stop: asyncio.Event, delay: timedelta) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, delay.total_seconds()))
        except TimeoutError:
            return

    async def shutdown(self, *, stop: asyncio.Event) -> None:
        """Request a graceful stop and wait for the drain (idempotent).

        The signal is *recorded* on the worker, not merely awaited: after this
        returns, :meth:`tick` refuses new work and hands a fresh claim straight
        back, so a host that shuts a worker down outside ``run_forever`` cannot
        keep claiming runs from the queue.
        """
        stop.set()
        self._stop = stop
        await self._drain()


def _output_digest(payload: Mapping[str, Any] | None) -> str | None:
    if not payload:
        return None
    return canonical_digest({str(key): _plain_value(value) for key, value in payload.items()})


def _output_event_count(payload: Mapping[str, Any] | None) -> int | None:
    if not payload:
        return None
    value = payload.get("event_count")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# Real turn runner adapter
# ---------------------------------------------------------------------------


class SessionAgentTurnRunner:
    """Runs a claimed Agent Run through the production agent-session path.

    This is the harness used by the SSE chat route: ``start_detached_turn``
    reserves a durable ``agent_turns`` row for ``turn_id`` and an autonomous
    task drives the runtime, then ``iter_turn_tail`` observes the persisted
    events.  Reusing it (instead of inventing a second bridge call) keeps one
    bootstrap contract for interactive and durable runs.

    The runner never returns ``FAILED`` for a cancellation: a cancelled provider
    call may still have reached the model, so the honest answer is ``UNKNOWN``.
    """

    def __init__(
        self,
        *,
        owner_id: str,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._owner_id = _required(owner_id, "owner_id")
        self._session_factory = session_factory

    def _factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory
        from prodavan.infrastructure.persistence.database import get_session_factory

        return get_session_factory()

    async def execute(
        self, *, request: RunTurnRequest, cancel: CancelSignal
    ) -> ProviderExecutionResult:
        from prodavan.application.agent.session_service import AgentSessionService
        from prodavan.domain.identity import ROLE_PLATFORM_ADMIN, Principal
        from prodavan.infrastructure.persistence.models.identity import EmployeeRow

        principal = Principal(
            sub=f"system:agent-run-worker:{self._owner_id}",
            roles=frozenset({ROLE_PLATFORM_ADMIN}),
        )
        factory = self._factory()
        async with factory() as session:
            service = AgentSessionService(session)
            employee = (
                await session.get(EmployeeRow, request.employee_id)
                if request.employee_id
                else None
            )
            turn = await service.start_detached_turn(
                project_id=request.project_id,
                session_id=request.session_id,
                text=request.input.text,
                attachment_refs=list(request.input.attachment_refs),
                principal=principal,
                employee=employee,
                model=request.input.model,
                turn_id=request.turn_id,
            )
            await session.commit()
            events = await self._tail(service, turn, cancel)
            if events is None:
                return ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN)

            if turn.error_code in _RETRYABLE_AGENT_CODES:
                return ProviderExecutionResult(
                    outcome=ProviderOutcome.RETRYABLE_FAILURE,
                    failure_code=f"agent_error.{turn.error_code}",
                    retry_at=self._retry_hint(),
                )
            if turn.error_code in _AMBIGUOUS_AGENT_CODES:
                return ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN)
            if turn.error_code:
                return ProviderExecutionResult(
                    outcome=ProviderOutcome.FAILED,
                    failure_code=f"agent_error.{turn.error_code}",
                )
            return ProviderExecutionResult(
                outcome=ProviderOutcome.SUCCEEDED,
                output_payload={
                    "assistant_text": turn.assistant_text,
                    "event_count": len(events),
                    "turn_id": turn.turn_id,
                    "session_id": turn.session_id,
                },
            )

    async def _tail(
        self,
        service: Any,
        turn: Any,
        cancel: CancelSignal,
    ) -> list[dict] | None:
        """Collect the tail; ``None`` means the turn was cancelled mid-flight."""
        collected: list[dict] = []

        async def drain() -> None:
            async for event in service.iter_turn_tail(turn):
                collected.append(event)

        tail = asyncio.create_task(drain())
        cancel_wait = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {tail, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_wait in done and tail not in done:
                from prodavan.application.agent.detached_turn import detached_turns

                detached_turns.request_cancel(turn.session_id)
                tail.cancel()
                await asyncio.gather(tail, return_exceptions=True)
                return None
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
            await tail
            return collected
        finally:
            for task in (tail, cancel_wait):
                if not task.done():
                    task.cancel()
            await asyncio.gather(tail, cancel_wait, return_exceptions=True)

    def _retry_hint(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=15)


_RETRYABLE_AGENT_CODES = frozenset(
    {
        "AGENT_PROVIDER_NETWORK",
        "AGENT_PROVIDER_RATE_LIMIT",
        "AGENT_PROVIDER_UNAVAILABLE",
        "AGENT_RUNTIME_UNAVAILABLE",
        "BRIDGE_UNREACHABLE",
        "BRIDGE_EMPTY_STREAM",
        "POD_NOT_RUNNING",
    }
)
_AMBIGUOUS_AGENT_CODES = frozenset(
    {
        "INTERRUPTED",
        "CANCELLED",
        "BRIDGE_SEND_FAILED",
        "AGENT_RUNTIME_ERROR",
        "INTERNAL",
    }
)


async def run_worker_once(
    worker: AgentRunWorker, *, max_ticks: int = 1
) -> Sequence[WorkerTick]:
    """Test/diagnostic helper: run a bounded number of ticks, never in a loop forever."""
    if max_ticks < 1:
        raise ValueError("max_ticks must be positive")
    ticks: list[WorkerTick] = []
    for _ in range(max_ticks):
        tick = await worker.tick()
        ticks.append(tick)
        if tick.status is WorkerTickStatus.IDLE:
            break
    return tuple(ticks)


__all__ = [
    "AgentRunWorker",
    "AgentTurnRunner",
    "CancelSignal",
    "FAILURE_EMPTY_RESULT",
    "FAILURE_INPUT_INVALID",
    "FAILURE_INPUT_UNAVAILABLE",
    "FAILURE_LEASE_LOST",
    "FAILURE_OUTCOME_UNKNOWN",
    "FAILURE_PROVIDER_FAILED",
    "FAILURE_RETRY_EXHAUSTED",
    "FAILURE_UNRESOLVED_ATTEMPT",
    "FAILURE_WORKER_SHUTDOWN",
    "LEASE_RELEASE_BLOCKER",
    "RECONCILE_BLOCKER",
    "RunInput",
    "RunInputInvalid",
    "RunInputSource",
    "RunTurnRequest",
    "SessionAgentTurnRunner",
    "Settlement",
    "SettlementKind",
    "WORKER_BLOCKER",
    "WorkerConfig",
    "WorkerConfigError",
    "WorkerRetryPolicy",
    "WorkerTick",
    "WorkerTickStatus",
    "WorkerUnavailable",
    "plan_settlement",
    "parse_run_input",
    "run_worker_once",
]
