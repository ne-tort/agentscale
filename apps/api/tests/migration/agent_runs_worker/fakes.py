"""In-memory doubles for the durable Agent Run worker acceptance artifacts.

These are **test-only** implementations of the production protocols.  Nothing
in ``prodavan.application.agent_runs`` imports this module, and the worker
refuses to start without a durable port (``WorkerUnavailable``), so a double
here can never leak into the production path.

The doubles deliberately model the *CAS* semantics of the real adapter:

* ``claim_next`` returns ``None`` when no run is eligible or the CAS loses;
* ``heartbeat`` returns ``False`` after expiry / supersession / cancellation;
* ``finish``/``schedule_retry``/``finish_unknown`` return ``False`` when the
  presented generation/token/sequence does not match — never raise.

They also count calls per outcome so the acceptance tests can assert that a
stale worker does not write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Mapping

from prodavan.application.agent_runs.attempts import (
    CLOSED_ATTEMPT_STATES,
    AttemptConflict,
    AttemptStart,
    AttemptState,
    ProviderAttempt,
    attempt_id_for,
    attempt_idempotency_key,
    lease_token_digest,
    request_digest,
)
from prodavan.application.agent_runs.execution import (
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
)
from prodavan.domain.agent.control_plane import AgentRunStatus

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
TENANT = "tenant-worker"
PROJECT = "project-worker"
SESSION = "session-worker"
EMPLOYEE = "employee-worker"
RUN_ID = "agr_worker"
LEASE_TTL = timedelta(seconds=60)
RETRY_DUE_STATUS = "retry_due"


class FakeClock:
    """Mutable clock shared by the worker and the port (one clock domain)."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now += delta
        return self.now


class AdvancingClock(FakeClock):
    """``FakeClock`` that also follows wall time.

    ``AgentRunWorker._drain`` bounds the shutdown grace window with the *injected*
    clock (``self._now() < deadline``), because that clock is the worker's whole
    time domain.  A frozen clock can therefore never close that window: the drain
    loop keeps sleeping until the process is killed, and a test that awaits
    ``shutdown()`` hangs for ever instead of observing the drained turn.  This
    clock keeps the deterministic base reading *and* moves with real time, which
    is exactly what the production clock does.
    """

    def __init__(self, now: datetime = NOW) -> None:
        super().__init__(now)
        self._started = time.monotonic()

    def __call__(self) -> datetime:
        return self.now + timedelta(seconds=time.monotonic() - self._started)


def make_lease(
    *,
    generation: int = 1,
    attempt: int = 1,
    sequence: int = 2,
    until: datetime | None = None,
    owner: str = "worker-a",
    token: str = "token-generation-1",
    session_id: str | None = SESSION,
) -> AgentRunLease:
    return AgentRunLease(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=RUN_ID,
        session_id=session_id,
        owner_id=owner,
        lease_token=token,
        generation=generation,
        attempt=attempt,
        sequence=sequence,
        lease_until=until or (NOW + LEASE_TTL),
    )


@dataclass(slots=True)
class FakeRunRow:
    """Durable run projection as the worker reads it."""

    run_id: str = RUN_ID
    project_id: str = PROJECT
    session_id: str | None = SESSION
    requested_by_employee_id: str | None = EMPLOYEE
    status: str = AgentRunStatus.QUEUED.value
    generation: int = 0
    attempt: int = 0
    sequence: int = 1
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    available_at: datetime | None = None
    input_payload: Mapping[str, Any] = field(
        default_factory=lambda: {"text": "summarise the pipeline", "attachment_refs": []}
    )

    def as_projection(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=self.run_id,
            project_id=self.project_id,
            session_id=self.session_id,
            requested_by_employee_id=self.requested_by_employee_id,
            status=self.status,
            input_payload=dict(self.input_payload),
        )


class FakeExecutionPort:
    """Test double for ``AgentRunExecutionPort`` with real CAS semantics."""

    def __init__(self, *, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.row = FakeRunRow()
        self.calls: list[tuple[str, int | None]] = []
        self.claim_count = 0
        self.heartbeat_results: list[bool] = []
        self.finish_calls: list[ProviderExecutionResult] = []
        self.retry_calls: list[datetime] = []
        self.unknown_calls: list[str | None] = []
        self.release_calls: list[str] = []

    # -- fake clock --------------------------------------------------------

    def advance(self, delta: timedelta) -> None:
        self.clock.advance(delta)

    def _due(self) -> bool:
        return self.row.available_at is None or self.row.available_at <= self.clock.now

    def _lease_live(self) -> bool:
        return self.row.lease_until is not None and self.row.lease_until > self.clock.now

    # -- port surface ------------------------------------------------------

    async def claim_next(
        self, *, owner_id: str, now: datetime, lease_until: datetime
    ) -> AgentRunLease | None:
        self.claim_count += 1
        self.calls.append(("claim", None))
        if self.row.status not in {AgentRunStatus.QUEUED.value, RETRY_DUE_STATUS}:
            return None
        if not self._due():
            return None
        if self.row.lease_owner is not None and self._lease_live():
            return None
        self.row.generation += 1
        self.row.attempt += 1
        self.row.sequence += 1  # the claim commits agent_run.start
        self.row.status = AgentRunStatus.RUNNING.value
        self.row.lease_owner = owner_id
        self.row.lease_token = f"token-generation-{self.row.generation}"
        self.row.lease_until = lease_until
        return make_lease(
            generation=self.row.generation,
            attempt=self.row.attempt,
            sequence=self.row.sequence,
            until=lease_until,
            owner=owner_id,
            token=self.row.lease_token,
            # The lease is cut from the very row that was claimed: a run without
            # a session cannot produce a lease that pretends to have one.
            session_id=self.row.session_id,
        )

    async def heartbeat(
        self, *, lease: AgentRunLease, now: datetime, lease_until: datetime
    ) -> bool:
        self.calls.append(("heartbeat", lease.generation))
        live = (
            self.row.generation == lease.generation
            and self.row.lease_token == lease.lease_token
            and self.row.lease_owner == lease.owner_id
            and self.row.status == AgentRunStatus.RUNNING.value
            and self._lease_live()
        )
        self.heartbeat_results.append(live)
        if live:
            assert self.row.lease_until is not None
            self.row.lease_until = max(lease_until, self.row.lease_until)
        return live

    async def finish(
        self, *, lease: AgentRunLease, result: ProviderExecutionResult, now: datetime
    ) -> bool:
        self.calls.append(("finish", lease.generation))
        self.finish_calls.append(result)
        if not self._owns(lease):
            return False
        self.row.status = (
            AgentRunStatus.SUCCEEDED.value
            if result.outcome is ProviderOutcome.SUCCEEDED
            else AgentRunStatus.FAILED.value
        )
        self.row.sequence += 1
        self._release_row()
        return True

    async def schedule_retry(
        self,
        *,
        lease: AgentRunLease,
        retry_at: datetime,
        failure_code: str,
        now: datetime,
    ) -> bool:
        self.calls.append(("schedule_retry", lease.generation))
        self.retry_calls.append(retry_at)
        if not self._owns(lease):
            return False
        self.row.status = RETRY_DUE_STATUS
        self.row.available_at = retry_at
        self.row.sequence += 1
        self._release_row()
        return True

    async def finish_unknown(
        self,
        *,
        lease: AgentRunLease,
        provider_request_id: str | None,
        observed_at: datetime,
    ) -> bool:
        self.calls.append(("finish_unknown", lease.generation))
        self.unknown_calls.append(provider_request_id)
        if not self._owns(lease):
            return False
        # Fail-closed: the run is parked in a state that needs reconciliation and
        # can never be read as success.
        self.row.status = AgentRunStatus.FAILED.value
        self.row.sequence += 1
        self._release_row()
        return True

    async def cancel_run(self, **_: Any) -> bool:  # pragma: no cover - not exercised here
        raise NotImplementedError

    async def release_lease(self, *, lease: AgentRunLease, now: datetime) -> bool:
        """Optional adapter extension; the worker only calls it when present."""
        self.release_calls.append(lease.run_id)
        if not self._owns(lease):
            return False
        self.row.status = AgentRunStatus.QUEUED.value
        self._release_row()
        return True

    # -- helpers -----------------------------------------------------------

    def _owns(self, lease: AgentRunLease) -> bool:
        return (
            self.row.generation == lease.generation
            and self.row.lease_token == lease.lease_token
            and self.row.lease_owner == lease.owner_id
            and self._lease_live()
        )

    def _release_row(self) -> None:
        self.row.lease_owner = None
        self.row.lease_token = None
        self.row.lease_until = None

    # -- simulation of external actors -------------------------------------

    def cancel(self) -> int:
        """Cancel from another actor: bump generation and fence the worker out."""
        self.row.generation += 1
        self.row.status = AgentRunStatus.CANCELLED.value
        self._release_row()
        return self.row.generation

    def steal(self) -> None:
        """A second worker claimed after the lease expired."""
        self.row.generation += 1
        self.row.lease_token = f"token-generation-{self.row.generation}"
        self.row.lease_owner = "worker-b"
        self.row.lease_until = self.clock.now + LEASE_TTL


class FakePortWithoutRelease(FakeExecutionPort):
    """A port that does not implement the (not yet specified) lease release."""

    release_lease = None  # type: ignore[assignment]


class ExpiringPort(FakeExecutionPort):
    """Lease whose beats are refused once the deadline has passed."""

    async def heartbeat(
        self, *, lease: AgentRunLease, now: datetime, lease_until: datetime
    ) -> bool:
        self.calls.append(("heartbeat", lease.generation))
        if now >= lease.lease_until:
            self.heartbeat_results.append(False)
            return False
        self.heartbeat_results.append(True)
        return True


class FakeRunStore:
    """``RunInputSource`` double (tenant scoped, like the durable read port)."""

    def __init__(self, row: FakeRunRow) -> None:
        self.row = row
        self.reads: list[str] = []
        self.hidden = False

    async def get_run(self, *, tenant_id: str, run_id: str):
        self.reads.append(run_id)
        if self.hidden or tenant_id != TENANT or run_id != self.row.run_id:
            return None
        return self.row.as_projection()


class FakeAttemptPort:
    """``ProviderAttemptJournalPort`` double with insert-once + CAS-finish."""

    def __init__(self) -> None:
        self.records: dict[str, ProviderAttempt] = {}
        self.finishes: list[tuple[str, AttemptState]] = []
        self.start_calls = 0
        self.refuse_finish = False

    async def insert_start(self, attempt: ProviderAttempt) -> AttemptStart:
        self.start_calls += 1
        existing = next(
            (r for r in self.records.values() if r.idempotency_key == attempt.idempotency_key),
            None,
        )
        if existing is not None:
            if existing.request_digest != attempt.request_digest:
                raise AttemptConflict()
            return AttemptStart(attempt=existing, created=False)
        self.records[attempt.attempt_id] = attempt
        return AttemptStart(attempt=attempt, created=True)

    async def read(self, *, attempt_id: str) -> ProviderAttempt | None:
        return self.records.get(attempt_id)

    async def find_unresolved(self, *, run_id: str) -> ProviderAttempt | None:
        unresolved = [
            record
            for record in self.records.values()
            if record.run_id == run_id and record.is_unresolved
        ]
        if not unresolved:
            return None
        return max(unresolved, key=lambda record: (record.generation, record.attempt))

    async def cas_finish(
        self,
        *,
        attempt_id: str,
        expected_state: AttemptState,
        next_state: AttemptState,
        finished_at: datetime,
        failure_code: str | None,
        provider_request_id: str | None,
        metadata: Mapping[str, Any],
        metadata_dropped: int,
    ) -> int:
        self.finishes.append((attempt_id, next_state))
        if self.refuse_finish:
            return 0
        current = self.records.get(attempt_id)
        if current is None or current.state is not expected_state:
            return 0
        self.records[attempt_id] = replace(
            current,
            state=next_state,
            finished_at=finished_at if next_state in CLOSED_ATTEMPT_STATES else None,
            failure_code=failure_code,
            provider_request_id=provider_request_id or current.provider_request_id,
            metadata=dict(metadata),
            metadata_dropped=current.metadata_dropped + metadata_dropped,
        )
        return 1

    # -- helpers -----------------------------------------------------------

    def seed_unresolved(
        self,
        *,
        run_id: str = RUN_ID,
        generation: int = 1,
        attempt: int = 1,
        state: AttemptState = AttemptState.STARTED,
        provider_request_id: str | None = "req-prior",
    ) -> ProviderAttempt:
        """Insert an attempt left behind by a crashed/parked worker."""
        key = attempt_idempotency_key(
            tenant_id=TENANT, run_id=run_id, generation=generation, attempt=attempt
        )
        closed = state in CLOSED_ATTEMPT_STATES
        record = ProviderAttempt(
            attempt_id=attempt_id_for(key),
            idempotency_key=key,
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id=run_id,
            session_id=SESSION,
            generation=generation,
            attempt=attempt,
            sequence=2,
            provider="agent-runtime",
            request_digest=request_digest(
                provider="agent-runtime",
                model=None,
                session_id=SESSION,
                attachments=0,
                prompt="summarise the pipeline",
            ),
            lease_token_digest=lease_token_digest(f"token-generation-{generation}"),
            state=state,
            started_at=NOW,
            finished_at=NOW if closed else None,
            failure_code=(
                "agent_run.provider_failed"
                if state is AttemptState.KNOWN_FAILED
                else "agent_run.lease_lost"
                if state is AttemptState.DISCARDED_STALE
                else None
            ),
            provider_request_id=provider_request_id,
        )
        self.records[record.attempt_id] = record
        return record

    @property
    def start_states(self) -> list[AttemptState]:
        return [record.state for record in self.records.values()]


class ScriptedRunner:
    """``AgentTurnRunner`` double returning a scripted result."""

    def __init__(
        self,
        result: ProviderExecutionResult | None = None,
        *,
        raise_exc: BaseException | None = None,
        wait_for_cancel: bool = False,
        on_execute: Any = None,
    ) -> None:
        self.result = result
        self.raise_exc = raise_exc
        self.wait_for_cancel = wait_for_cancel
        self.on_execute = on_execute
        self.requests: list[Any] = []
        self.calls = 0

    async def execute(self, *, request, cancel) -> ProviderExecutionResult:
        self.calls += 1
        self.requests.append(request)
        if self.on_execute is not None:
            await self.on_execute(request, cancel)
        if self.wait_for_cancel:
            await cancel.wait()
            return ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result or success()


def success(**kwargs: Any) -> ProviderExecutionResult:
    payload = {"assistant_text": "done", "event_count": 2}
    payload.update(kwargs.pop("output_payload", {}))
    return ProviderExecutionResult(
        outcome=ProviderOutcome.SUCCEEDED, output_payload=payload, **kwargs
    )


def retryable(*, retry_at: datetime) -> ProviderExecutionResult:
    return ProviderExecutionResult(
        outcome=ProviderOutcome.RETRYABLE_FAILURE,
        failure_code="agent_error.AGENT_PROVIDER_RATE_LIMIT",
        retry_at=retry_at,
    )


def unknown(*, provider_request_id: str | None = "req-unknown") -> ProviderExecutionResult:
    return ProviderExecutionResult(
        outcome=ProviderOutcome.UNKNOWN, provider_request_id=provider_request_id
    )


def failed() -> ProviderExecutionResult:
    return ProviderExecutionResult(
        outcome=ProviderOutcome.FAILED, failure_code="agent_error.AGENT_INVALID_MODEL"
    )
