"""Acceptance fixtures for MIG-AR-HANDLER-01 (real intent handler wiring).

Authored under the migration verification freeze: these modules are written to be
runnable, and are **not executed** by this task.  Everything here is a test-only
double; ``prodavan`` does not import any of it.

What is real and what is doubled
--------------------------------
*Real* code under test: ``EnqueueIntentDispatcher`` (classification, scope
validation, supersession, reservation, outcome recording), the new
``AgentRunIntentHandler`` + ``IntentAddressedLeaseStore``, the frozen
``ControlPlaneAgentRunExecutionAdapter`` (claim ordering, start transition,
compensation, fencing), ``AgentRunWorker`` (preflight, attempt journal,
heartbeat, settlement planning) and the real ``ProviderAttemptJournal`` object
over a doubled storage port.

*Doubled* infrastructure only: the lease store rows (``MemoryLeaseStore``), the
control-plane transaction (``FakeAtomicTransaction``), the attempt journal port,
the turn runner, and the intent row store.  Each double models the CAS behaviour
it stands in for and records calls, so "the turn ran exactly once" and "nothing
was written after the fence was lost" are assertions, not claims.

Two markers (``SECRET_MARKER``, ``PROMPT_MARKER``) are fixed literals: "no secret
and no prompt reached an audit row, a handler report or a handler log line" is a
substring check rather than an opinion.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.application.agent_control_plane.service import DurableAgentControlPlane
from prodavan.application.agent_runs.attempts import ProviderAttemptJournal
from prodavan.application.agent_runs.dispatcher import (
    ClaimedIntent,
    IntentDispatchOutcome,
    IntentDispatchReason,
    IntentKind,
    IntentScopeViolation,
    ReservationStatus,
    ResolvedIntentTarget,
)
from prodavan.application.agent_runs.execution_adapter import (
    ControlPlaneAgentRunExecutionAdapter,
)
from prodavan.application.agent_runs.handler import (
    AgentRunIntentHandler,
    IntentExecutionPipeline,
    build_run_intent_handler,
)
from prodavan.application.agent_runs.lease import LeasePolicy
from prodavan.domain.agent.control_plane import AgentRunStatus, AgentRunView
from tests.migration.agent_runs_execution.fakes import (
    FakeAtomicTransaction,
    MemoryLease,
    MemoryLeaseStore,
    MemoryPlatform,
    MemoryRun,
)
from tests.migration.agent_runs_worker.fakes import FakeAttemptPort, ScriptedRunner

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
TENANT = "tenant-handler"
PROJECT = "project-handler"
SESSION = "session-handler"
EMPLOYEE = "employee-handler"
RUN = "agr_handler"
OTHER_RUN = "agr_handler_other"
QUEUE = "agent-control-plane"
DEDUP_KEY = "13=tenant-handler:10=agent_run:11=agr_handler:1=1"
LINKAGE_KEY = "create-run-handler"

#: Never allowed to appear in an audit row, a report or a handler log record.
SECRET_MARKER = "sk-live-HANDLER-MUST-NOT-BE-AUDITED"
PROMPT_MARKER = "customer-private-prompt-body"


def run_input(text: str | None = None) -> dict[str, Any]:
    """The persisted ``agent_runs.input_payload`` the worker executes."""
    return {"text": text or f"please summarise {PROMPT_MARKER}", "attachment_refs": []}


# --------------------------------------------------------------------------- #
# Clocks
# --------------------------------------------------------------------------- #


@dataclass
class MutableClock:
    """One clock domain for the handler and the worker (the DB clock is separate)."""

    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


# --------------------------------------------------------------------------- #
# Run projection (satisfies both the adapter's read port and RunInputSource)
# --------------------------------------------------------------------------- #


class RunProjectionPort:
    """``AgentControlPlaneReadPort`` + ``RunInputSource`` over ``MemoryPlatform``.

    The adapter documents that one tenant-scoped read port satisfies both, which
    is exactly what production does; ``input_payload`` therefore has to be part
    of the projection (it is what the worker executes).
    """

    def __init__(self, platform: MemoryPlatform, *, input_payload: Mapping[str, Any] | None = None) -> None:
        self.platform = platform
        self.input_payload = dict(input_payload or run_input())
        self.reads: list[tuple[str, str]] = []
        self.hidden = False
        self.fail_with: BaseException | None = None

    async def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunView | None:
        self.reads.append((tenant_id, run_id))
        if self.fail_with is not None:
            raise self.fail_with
        if self.hidden:
            return None
        run = self.platform.runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            return None
        return self._view(run)

    async def get_approval(self, *, tenant_id: str, approval_id: str):  # pragma: no cover
        raise NotImplementedError

    def _view(self, run: MemoryRun) -> AgentRunView:
        return AgentRunView(
            id=run.run_id,
            project_id=run.project_id,
            session_id=run.session_id,
            requested_by_employee_id=run.requested_by_employee_id,
            idempotency_key=LINKAGE_KEY,
            input_payload=dict(self.input_payload),
            status=AgentRunStatus(run.status),
            attempt=run.attempt,
            output_payload=dict(run.output_payload) if run.output_payload else None,
            failure_code=run.failure_code,
            failure_message=run.failure_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=NOW,
            updated_at=NOW,
        )


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


@dataclass
class IntentHarness:
    """One fully composed dispatch → handler → adapter → worker pipeline."""

    platform: MemoryPlatform = field(default_factory=MemoryPlatform)
    clock: MutableClock = field(default_factory=MutableClock)
    transaction: FakeAtomicTransaction = field(init=False)
    leases: MemoryLeaseStore = field(init=False)
    runs: RunProjectionPort = field(init=False)
    attempt_port: FakeAttemptPort = field(init=False)
    journal: ProviderAttemptJournal = field(init=False)
    runner: ScriptedRunner = field(init=False)
    pipeline: IntentExecutionPipeline = field(init=False)
    handler: AgentRunIntentHandler = field(init=False)
    #: Every lease store the factory was asked to narrow, in dispatch order.
    narrowed_stores: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transaction = FakeAtomicTransaction(self.platform)
        self.leases = MemoryLeaseStore(self.platform)
        self.runs = RunProjectionPort(self.platform)
        self.attempt_port = FakeAttemptPort()
        self.journal = ProviderAttemptJournal(port=self.attempt_port)
        self.runner = ScriptedRunner()
        self.pipeline = IntentExecutionPipeline(
            lease_store=self.leases,
            execution_factory=self._adapter,
            runs=self.runs,
            journal=self.journal,
            runner=self.runner,
        )
        self.handler = build_run_intent_handler(pipeline=self.pipeline, clock=self.clock)

    # -- composition -------------------------------------------------------

    def _adapter(self, leases: Any) -> ControlPlaneAgentRunExecutionAdapter:
        self.narrowed_stores.append(leases)
        return ControlPlaneAgentRunExecutionAdapter(
            leases=leases,
            control_plane=DurableAgentControlPlane(
                transaction=self.transaction, tenant_id=TENANT
            ),
            runs=self.runs,
            service_actor_id=EMPLOYEE,
        )

    def seed_run(
        self,
        *,
        run_id: str = RUN,
        status: str = AgentRunStatus.QUEUED.value,
        sequence: int = 1,
        input_payload: Mapping[str, Any] | None = None,
    ) -> tuple[MemoryRun, MemoryLease]:
        """One queued run + its idle lease row + its committed aggregate cursor."""
        run = self.platform.add_run(
            MemoryRun(
                run_id=run_id,
                tenant_id=TENANT,
                project_id=PROJECT,
                session_id=SESSION,
                requested_by_employee_id=EMPLOYEE,
                status=status,
                sequence=sequence,
                attempt=1,
            )
        )
        lease = self.platform.add_lease(
            MemoryLease(
                run_id=run_id,
                tenant_id=TENANT,
                project_id=PROJECT,
                attempt=1,
                generation=0,
            )
        )
        if input_payload is not None:
            self.runs.input_payload = dict(input_payload)
        return run, lease

    # -- inspection --------------------------------------------------------

    @property
    def run(self) -> MemoryRun:
        return self.platform.runs[RUN]

    @property
    def lease(self) -> MemoryLease:
        return self.platform.leases[RUN]

    @property
    def transitions(self) -> list[str]:
        return self.transaction.operations

    @property
    def event_types(self) -> list[str]:
        return self.transaction.event_types

    def handler_report(self) -> dict[str, Any]:
        return self.handler.report()


def build_harness(
    *,
    status: str = AgentRunStatus.QUEUED.value,
    sequence: int = 1,
    input_payload: Mapping[str, Any] | None = None,
    result: Any = None,
    **runner_kwargs: Any,
) -> IntentHarness:
    harness = IntentHarness()
    harness.seed_run(status=status, sequence=sequence, input_payload=input_payload)
    if result is not None or runner_kwargs:
        harness.runner = ScriptedRunner(result, **runner_kwargs)
        harness.pipeline = IntentExecutionPipeline(
            lease_store=harness.leases,
            execution_factory=harness._adapter,
            runs=harness.runs,
            journal=harness.journal,
            runner=harness.runner,
        )
        harness.handler = build_run_intent_handler(pipeline=harness.pipeline, clock=harness.clock)
    return harness


# --------------------------------------------------------------------------- #
# Intent-side doubles (the durable consumer, reduced to one row)
# --------------------------------------------------------------------------- #


class MemoryIntentStore:
    """``IntentConsumerPort`` over one intent row, with real duplicate semantics.

    ``reserve`` is the durable duplicate-suppression point in production, so this
    double answers the same way: ``ALREADY_DELIVERED`` once the row is delivered,
    ``IN_FLIGHT_UNKNOWN`` while another claim is mid-dispatch, ``RESERVED``
    exactly once.  ``record_outcome`` writes the closed reason code the consumer
    would store in ``last_error``.
    """

    def __init__(
        self,
        *,
        intents: list[ClaimedIntent] | None = None,
        delivered: bool = False,
        in_flight: bool = False,
    ) -> None:
        self.intents = list(intents or [])
        self.delivered = delivered
        self.in_flight = in_flight
        self.claims: list[str] = []
        self.reserve_calls = 0
        self.records: list[IntentDispatchOutcome] = []
        self.audit_records: list[str] = []
        self.state = "pending"
        self.retry_at: datetime | None = None
        self.delivered_at: datetime | None = None

    async def claim(self, *, owner_id: str, limit: int):
        self.claims.append(owner_id)
        if not self.intents:
            return ()
        batch = tuple(self.intents[:limit])
        self.intents = self.intents[len(batch) :]
        if self.state != "delivered":
            self.state = "claimed"
        return batch

    async def reserve(self, *, intent: ClaimedIntent, kind: IntentKind, target: ResolvedIntentTarget):
        del kind, target
        self.reserve_calls += 1
        if self.delivered or self.delivered_at is not None:
            return ReservationStatus.ALREADY_DELIVERED
        if self.in_flight:
            return ReservationStatus.IN_FLIGHT_UNKNOWN
        self.state = "dispatching"
        return ReservationStatus.RESERVED

    async def record_outcome(
        self,
        *,
        intent: ClaimedIntent,
        kind: IntentKind | None,
        target: ResolvedIntentTarget | None,
        outcome: IntentDispatchOutcome,
    ) -> bool:
        del target
        self.records.append(outcome)
        action = outcome.action.value
        reason = outcome.reason.value
        kind_code = "" if kind is None else f" kind={kind.value}"
        self.audit_records.append(
            f"action={action} reason={reason}{kind_code} attempts={intent.attempts}"
        )
        if action == "delivered":
            self.state = "delivered"
            self.delivered = True
            self.delivered_at = NOW
        elif action == "retry":
            self.state = "pending"
            self.retry_at = outcome.retry_at
        else:
            self.state = "failed"
        return True

    async def release_expired(self, *, limit: int) -> int:
        del limit
        return 0

    # -- simulation helpers ------------------------------------------------

    def redeliver(self, intent: ClaimedIntent) -> None:
        """A duplicate delivery of an already delivered intent (broker replay)."""
        self.intents = [intent]
        self.state = "claimed"


class FixedResolver:
    """``IntentScopeResolverPort`` returning a fixed target (or a fixed violation)."""

    def __init__(
        self,
        target: ResolvedIntentTarget | None = None,
        error: IntentScopeViolation | None = None,
    ) -> None:
        self.target = target
        self.error = error
        self.calls = 0

    async def resolve(self, *, intent: ClaimedIntent):
        del intent
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.target


# --------------------------------------------------------------------------- #
# Intent / target builders
# --------------------------------------------------------------------------- #


def make_intent(
    *,
    event_type: str = "agent_run.created",
    aggregate_type: str = "agent_run",
    aggregate_id: str = RUN,
    aggregate_version: int = 1,
    tenant_id: str = TENANT,
    project_id: str = PROJECT,
    run_id: str = RUN,
    session_id: str | None = SESSION,
    status: str = "queued",
    payload_extra: Mapping[str, Any] | None = None,
    attempts: int = 1,
    claimed_at: datetime = NOW,
    lease_owner: str = "agent-run-api:trace-handler",
    lease_token: str = "intent-lease-token",
    lease_generation: int = 1,
) -> ClaimedIntent:
    body: dict[str, Any] = {
        "project_id": project_id,
        "status": status,
        "control_plane": {
            "tenant_id": tenant_id,
            "actor_id": EMPLOYEE,
            "project_id": project_id,
            "session_id": session_id,
            "trace_id": "trace-handler",
            "command_idempotency_key": LINKAGE_KEY,
        },
    }
    if payload_extra:
        body.update(payload_extra)
    return ClaimedIntent(
        event_id="event-handler",
        queue_name=QUEUE,
        tenant_id=tenant_id,
        project_id=project_id,
        deduplication_key=DEDUP_KEY,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        run_id=run_id,
        session_id=session_id,
        occurred_at=claimed_at - timedelta(seconds=1),
        payload=body,
        lease_owner=lease_owner,
        lease_token=lease_token,
        lease_generation=lease_generation,
        attempts=attempts,
        claimed_at=claimed_at,
        lease_until=claimed_at + timedelta(seconds=30),
    )


def make_target(
    *,
    tenant_id: str = TENANT,
    project_id: str = PROJECT,
    run_id: str = RUN,
    session_id: str | None = SESSION,
    run_status: AgentRunStatus = AgentRunStatus.QUEUED,
    aggregate_type: str = "agent_run",
    aggregate_id: str | None = None,
    aggregate_status: str = "queued",
    aggregate_sequence: int = 1,
) -> ResolvedIntentTarget:
    return ResolvedIntentTarget(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        session_id=session_id,
        run_status=run_status,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id or run_id,
        aggregate_status=aggregate_status,
        aggregate_sequence=aggregate_sequence,
    )


# --------------------------------------------------------------------------- #
# Log capture (handler module only; never the whole root logger)
# --------------------------------------------------------------------------- #


class ListHandler(logging.Handler):
    """Collect records so "no secret in a handler log line" can be asserted."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(
            record.getMessage() + " " + json.dumps(record.args, default=str, ensure_ascii=False)
            for record in self.records
        )


@contextmanager
def capture_handler_logs() -> Iterator[ListHandler]:
    """Capture ``prodavan.application.agent_runs.handler`` records only."""
    handler = ListHandler()
    target = logging.getLogger("prodavan.application.agent_runs.handler")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


# --------------------------------------------------------------------------- #
# Convenience assertions helpers
# --------------------------------------------------------------------------- #


def default_kind() -> IntentKind:
    return IntentKind.RUN_CREATED


def default_retry_reason() -> IntentDispatchReason:
    return IntentDispatchReason.EXECUTION_UNWIRED


def new_handler(pipeline: IntentExecutionPipeline | None = None, **kwargs: Any) -> AgentRunIntentHandler:
    return build_run_intent_handler(pipeline=pipeline, **kwargs)


def policy_with_ttl(*, seconds: float = 60.0) -> LeasePolicy:
    return LeasePolicy(ttl=timedelta(seconds=seconds))


def callable_factory(fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
    return fn
