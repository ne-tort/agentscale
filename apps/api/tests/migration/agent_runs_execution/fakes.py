"""In-memory doubles for the SQL lease store / execution-adapter acceptance artifacts.

These are **test-only**.  Nothing in ``prodavan`` imports this module, and the
adapter refuses to construct without a durable lease store and a control plane,
so a double here cannot leak into a production path.

The doubles are deliberately shaped like the SQL they stand in for:

* :class:`MemoryLeaseStore` mirrors ``SqlAlchemyRunLeaseStore`` predicate for
  predicate — generation + attempt on acquire, generation + token + owner +
  unexpired deadline on renew/release/park, tenant-scoped generation bump on
  fence — and returns row counts instead of raising on a lost race;
* :class:`FakeAtomicTransaction` is a ``AgentControlPlaneTransactionPort`` with
  receipt-based idempotency, per-aggregate sequence/status compare-and-set, and
  event/outbox projections that satisfy ``DurableAgentControlPlane._commit``.
  The *real* application service builds every command, so the tests exercise the
  production sequence arithmetic and transition validation, not a hand-written
  shortcut.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from prodavan.application.agent_control_plane.contracts import (
    AtomicEventOutboxResult,
    ControlPlaneAtomicWrite,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
)
from prodavan.application.agent_runs.execution import (
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
)
from prodavan.application.agent_runs.lease import (
    CLAIMABLE_STATUSES,
    LeaseSlot,
)
from prodavan.domain.agent.control_plane import AgentRunStatus, AgentRunView
from prodavan.domain.automation.outbox import OutboxEvent
from prodavan.infrastructure.persistence.agent_control_plane.lease_store import (
    PARKED_STATUSES,
    RELEASABLE_STATUSES,
    RENEWABLE_STATUSES,
)

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TENANT = "tenant-exec"
PROJECT = "project-exec"
SESSION = "session-exec"
EMPLOYEE = "employee-exec"
RUN_ID = "agr_exec"
LEASE_TTL = timedelta(seconds=60)

RETRY_DUE_STATUS = "retry_due"


# --------------------------------------------------------------------------- #
# In-memory platform state
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class MemoryRun:
    """The ``agent_runs`` row plus the committed aggregate cursor."""

    run_id: str = RUN_ID
    tenant_id: str = TENANT
    project_id: str = PROJECT
    session_id: str | None = SESSION
    requested_by_employee_id: str | None = EMPLOYEE
    status: str = AgentRunStatus.QUEUED.value
    sequence: int = 1
    attempt: int = 1
    output_payload: Mapping[str, object] | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class MemoryLease:
    """The ``agent_run_leases`` row."""

    run_id: str = RUN_ID
    tenant_id: str = TENANT
    project_id: str = PROJECT
    attempt: int = 1
    generation: int = 0
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    retry_at: datetime | None = None

    def clear(self) -> None:
        self.lease_owner = None
        self.lease_token = None
        self.lease_until = None


class MemoryPlatform:
    """Shared state for one run/lease/aggregate triple plus a database clock."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.runs: dict[str, MemoryRun] = {}
        self.leases: dict[str, MemoryLease] = {}

    # -- seeding -----------------------------------------------------------

    def add_run(self, run: MemoryRun | None = None, **overrides: object) -> MemoryRun:
        row = run or MemoryRun()
        for name, value in overrides.items():
            setattr(row, name, value)
        self.runs[row.run_id] = row
        return row

    def add_lease(self, lease: MemoryLease | None = None, **overrides: object) -> MemoryLease:
        row = lease or MemoryLease()
        for name, value in overrides.items():
            setattr(row, name, value)
        self.leases[row.run_id] = row
        return row

    def seed(self, **run_overrides: object) -> tuple[MemoryRun, MemoryLease]:
        """Seed one queued run with an idle lease row (the normal claim input)."""
        return self.add_run(**run_overrides), self.add_lease()

    # -- clock -------------------------------------------------------------

    def advance(self, delta: timedelta) -> datetime:
        self.now = self.now + delta
        return self.now

    # -- inspection --------------------------------------------------------

    def slot(self, run_id: str = RUN_ID) -> LeaseSlot | None:
        lease = self.leases.get(run_id)
        run = self.runs.get(run_id)
        if lease is None or run is None:
            return None
        return _slot(lease, run)


def _slot(lease: MemoryLease, run: MemoryRun) -> LeaseSlot:
    return LeaseSlot(
        run_id=lease.run_id,
        tenant_id=lease.tenant_id,
        project_id=lease.project_id,
        status=run.status,
        sequence=run.sequence,
        attempt=lease.attempt,
        generation=lease.generation,
        session_id=run.session_id,
        lease_owner=lease.lease_owner,
        lease_token=lease.lease_token,
        lease_until=lease.lease_until,
        retry_at=lease.retry_at,
    )


# --------------------------------------------------------------------------- #
# Lease store double
# --------------------------------------------------------------------------- #


class MemoryLeaseStore:
    """``FencedLeaseStore`` double with the SQL store's exact fence semantics."""

    def __init__(self, platform: MemoryPlatform) -> None:
        self.platform = platform
        self.scopes = 0
        self._in_scope = False
        self.calls: list[str] = []

    @asynccontextmanager
    async def claim_scope(self):
        if self._in_scope:
            raise RuntimeError("a claim scope is already active on this lease store")
        self.scopes += 1
        self._in_scope = True
        try:
            yield
        finally:
            self._in_scope = False

    async def database_now(self) -> datetime:
        return self.platform.now

    async def select_claim_candidate(
        self, *, now: datetime, statuses: frozenset[str] | None = None
    ) -> LeaseSlot | None:
        self.calls.append("select_claim_candidate")
        allowed = CLAIMABLE_STATUSES if statuses is None else statuses
        candidates: list[tuple[MemoryLease, MemoryRun]] = []
        for lease in self.platform.leases.values():
            run = self.platform.runs.get(lease.run_id)
            if run is None:
                continue
            if run.status not in allowed:
                continue
            if run.sequence < 1 or lease.attempt < 1:
                continue
            if lease.retry_at is not None and lease.retry_at > now:
                continue
            if lease.lease_until is not None and lease.lease_until > now:
                continue
            candidates.append((lease, run))
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item[0].retry_at is not None,
                item[0].retry_at or EPOCH,
                item[0].lease_until or EPOCH,
                item[0].run_id,
            )
        )
        lease, run = candidates[0]
        return _slot(lease, run)

    async def read_slot(self, *, run_id: str) -> LeaseSlot | None:
        return self.platform.slot(run_id)

    async def cas_acquire(
        self, *, slot: LeaseSlot, lease: AgentRunLease, now: datetime
    ) -> int:
        self.calls.append("cas_acquire")
        row = self.platform.leases.get(slot.run_id)
        run = self.platform.runs.get(slot.run_id)
        if row is None or run is None:
            return 0
        if row.generation != slot.generation or row.attempt != slot.attempt:
            return 0
        if row.retry_at is not None and row.retry_at > now:
            return 0
        if row.lease_until is not None and row.lease_until > now:
            return 0
        if run.status not in CLAIMABLE_STATUSES:
            return 0
        row.lease_owner = lease.owner_id
        row.lease_token = lease.lease_token
        row.lease_until = lease.lease_until
        row.generation = lease.generation
        row.attempt = lease.attempt
        return 1

    async def cas_renew(
        self, *, lease: AgentRunLease, now: datetime, lease_until: datetime
    ) -> int:
        self.calls.append("cas_renew")
        if not self._owns(lease):
            return 0
        row = self.platform.leases[lease.run_id]
        if row.lease_until is None or row.lease_until <= now or row.lease_until > lease_until:
            return 0
        if self.platform.runs[lease.run_id].status not in RENEWABLE_STATUSES:
            return 0
        row.lease_until = lease_until
        return 1

    async def cas_release(self, *, lease: AgentRunLease, now: datetime, status: str) -> int:
        self.calls.append("cas_release")
        target = str(status).strip().lower()
        if target not in RELEASABLE_STATUSES:
            raise ValueError("release status is outside the releasable set")
        if not self._owns(lease):
            return 0
        if self.platform.runs[lease.run_id].status != target:
            return 0
        row = self.platform.leases[lease.run_id]
        row.clear()
        if target == AgentRunStatus.QUEUED.value:
            row.retry_at = None
        return 1

    async def cas_park(
        self, *, lease: AgentRunLease, now: datetime, retry_at: datetime | None
    ) -> int:
        self.calls.append("cas_park")
        if not self._owns(lease):
            return 0
        if self.platform.runs[lease.run_id].status not in PARKED_STATUSES:
            return 0
        row = self.platform.leases[lease.run_id]
        row.clear()
        row.retry_at = retry_at
        return 1

    async def cas_fence(self, *, tenant_id: str, run_id: str, now: datetime) -> int:
        self.calls.append("cas_fence")
        row = self.platform.leases.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            return 0
        row.generation += 1
        row.clear()
        return 1

    # -- helpers -----------------------------------------------------------

    def _owns(self, lease: AgentRunLease) -> bool:
        row = self.platform.leases.get(lease.run_id)
        if row is None:
            return False
        return (
            row.tenant_id == lease.tenant_id
            and row.generation == lease.generation
            and row.lease_token == lease.lease_token
            and row.lease_owner == lease.owner_id
            and row.lease_until is not None
        )


# --------------------------------------------------------------------------- #
# Control-plane doubles
# --------------------------------------------------------------------------- #


def _event_results(
    write: ControlPlaneAtomicWrite, *, duplicate: bool = False
) -> tuple[AtomicEventOutboxResult, ...]:
    results: list[AtomicEventOutboxResult] = []
    for event in write.events:
        version = int(event.sequence or 0)
        key = OutboxEvent.aggregate_event_key(
            tenant_id=write.command.linkage.tenant_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            aggregate_version=version,
        )
        results.append(
            AtomicEventOutboxResult(
                event_id=str(event.event_id),
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=version,
                outbox_idempotency_key=key,
                queue_deduplication_key=key,
                event_recorded=True,
                queue_intent_recorded=True,
                duplicate=duplicate,
            )
        )
    return tuple(results)


def _view(run: MemoryRun, *, status: AgentRunStatus | None = None) -> AgentRunView:
    return AgentRunView(
        id=run.run_id,
        project_id=run.project_id,
        session_id=run.session_id,
        requested_by_employee_id=run.requested_by_employee_id,
        idempotency_key="run-intent-key",
        input_payload={},
        status=status or AgentRunStatus(run.status),
        attempt=run.attempt,
        output_payload=dict(run.output_payload) if run.output_payload else None,
        failure_code=run.failure_code,
        failure_message=run.failure_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class AppliedTransition:
    """One committed control-plane transition, as the tests observe it."""

    operation: str
    run_id: str
    expected_sequence: int
    target_status: str
    event_types: tuple[str, ...] = ()
    detail_failure_code: str | None = None


class FakeAtomicTransaction:
    """``AgentControlPlaneTransactionPort`` with receipts and aggregate CAS."""

    def __init__(self, platform: MemoryPlatform) -> None:
        self.platform = platform
        self.receipts: dict[tuple[str, str, str], tuple[str, ControlPlaneWriteResult]] = {}
        self.events: list[object] = []
        self.transitions: list[AppliedTransition] = []
        self.force_conflict_next = False

    async def commit(self, *, write: ControlPlaneAtomicWrite) -> ControlPlaneWriteResult:
        command = write.command
        key = (
            command.linkage.tenant_id,
            command.linkage.project_id,
            command.command_idempotency_key,
        )
        digest = command.command_digest
        previous = self.receipts.get(key)
        if previous is not None:
            stored_digest, stored = previous
            if stored_digest != digest:
                return ControlPlaneWriteResult(
                    status=ControlPlaneWriteStatus.CONFLICT,
                    reason="idempotency_key_payload_conflict",
                )
            return self._replay(stored)

        if self.force_conflict_next:
            self.force_conflict_next = False
            result = ControlPlaneWriteResult(
                status=ControlPlaneWriteStatus.CONFLICT, reason="forced_conflict"
            )
            self.receipts[key] = (digest, result)
            return result

        run = self.platform.runs.get(command.run_id)
        if command.expected_run_status is None:
            if run is not None:
                return self._record(key, digest, _conflict("run_identity_conflict"))
            run = self.platform.add_run(
                run_id=command.run_id,
                project_id=command.linkage.project_id,
                session_id=command.linkage.session_id,
                requested_by_employee_id=command.details.get("requested_by_employee_id"),  # type: ignore[arg-type]
                status=str(command.target_run_status.value if command.target_run_status else ""),
                sequence=0,
            )
        else:
            if run is None or run.status != command.expected_run_status.value:
                return self._record(key, digest, _conflict("run_status_conflict"))
            if run.project_id != command.linkage.project_id:
                return self._record(key, digest, _conflict("run_scope_not_found"))
            if run.session_id is not None and run.session_id != command.linkage.session_id:
                return self._record(key, digest, _conflict("run_scope_not_found"))

        for version in command.aggregates:
            if version.aggregate_type != "agent_run":
                continue
            if version.expected_sequence != run.sequence:
                return self._record(key, digest, _conflict("aggregate_sequence_conflict"))

        target = command.target_run_status
        assert target is not None  # every adapter transition has a target
        run.status = target.value
        run.sequence = run.sequence + 1
        if target is AgentRunStatus.RUNNING and run.started_at is None:
            run.started_at = self.platform.now
        if target in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
            run.finished_at = self.platform.now
        details = command.details
        if details.get("output_payload") is not None:
            run.output_payload = dict(details["output_payload"])  # type: ignore[arg-type]
        failure_code = details.get("failure_code")
        if failure_code is not None:
            run.failure_code = str(failure_code)[:64]
        if details.get("reason") is not None and target is AgentRunStatus.FAILED:
            run.failure_message = str(details["reason"])

        self.events.extend(write.events)
        self.transitions.append(
            AppliedTransition(
                operation=command.operation,
                run_id=command.run_id,
                expected_sequence=command.aggregates[0].expected_sequence,
                target_status=target.value,
                event_types=tuple(event.event_type for event in write.events),
                detail_failure_code=None if failure_code is None else str(failure_code),
            )
        )
        result = ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=_event_results(write),
            run=_view(run, status=target),
        )
        return self._record(key, digest, result)

    # -- helpers -----------------------------------------------------------

    def _record(
        self,
        key: tuple[str, str, str],
        digest: str,
        result: ControlPlaneWriteResult,
    ) -> ControlPlaneWriteResult:
        self.receipts[key] = (digest, result)
        return result

    def _replay(self, stored: ControlPlaneWriteResult) -> ControlPlaneWriteResult:
        if stored.status is not ControlPlaneWriteStatus.APPLIED:
            return stored
        duplicate_events = tuple(
            AtomicEventOutboxResult(
                event_id=result.event_id,
                aggregate_type=result.aggregate_type,
                aggregate_id=result.aggregate_id,
                aggregate_version=result.aggregate_version,
                outbox_idempotency_key=result.outbox_idempotency_key,
                queue_deduplication_key=result.queue_deduplication_key,
                event_recorded=True,
                queue_intent_recorded=True,
                duplicate=True,
            )
            for result in stored.event_results
        )
        return ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.DUPLICATE,
            event_results=duplicate_events,
            run=stored.run,
            approval=stored.approval,
        )

    @property
    def event_types(self) -> list[str]:
        return [str(getattr(event, "event_type", "")) for event in self.events]

    @property
    def operations(self) -> list[str]:
        return [transition.operation for transition in self.transitions]


def _conflict(reason: str) -> ControlPlaneWriteResult:
    return ControlPlaneWriteResult(status=ControlPlaneWriteStatus.CONFLICT, reason=reason)


class FakeRunReadPort:
    """``AgentControlPlaneReadPort`` double (tenant scoped, like the real one)."""

    def __init__(self, platform: MemoryPlatform) -> None:
        self.platform = platform
        self.reads: list[tuple[str, str]] = []
        self.hidden = False

    async def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunView | None:
        self.reads.append((tenant_id, run_id))
        if self.hidden:
            return None
        run = self.platform.runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            return None
        return _view(run)

    async def get_approval(self, *, tenant_id: str, approval_id: str):  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Result helpers
# --------------------------------------------------------------------------- #


def success(output: Mapping[str, object] | None = None) -> ProviderExecutionResult:
    payload = {"assistant_text": "done", "event_count": 2}
    if output:
        payload.update(output)
    return ProviderExecutionResult(outcome=ProviderOutcome.SUCCEEDED, output_payload=payload)


def failure(failure_code: str = "agent_error.AGENT_INVALID_MODEL") -> ProviderExecutionResult:
    return ProviderExecutionResult(outcome=ProviderOutcome.FAILED, failure_code=failure_code)


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


@dataclass(slots=True)
class Harness:
    """One fully wired adapter over doubles (real control-plane service)."""

    platform: MemoryPlatform = field(default_factory=MemoryPlatform)
    leases: MemoryLeaseStore = field(init=False)
    transaction: FakeAtomicTransaction = field(init=False)
    runs: FakeRunReadPort = field(init=False)
    control_plane: object = field(init=False)
    adapter: object = field(init=False)

    def __post_init__(self) -> None:
        from prodavan.application.agent_control_plane.service import DurableAgentControlPlane
        from prodavan.application.agent_runs.execution_adapter import (
            ControlPlaneAgentRunExecutionAdapter,
        )

        self.leases = MemoryLeaseStore(self.platform)
        self.transaction = FakeAtomicTransaction(self.platform)
        self.runs = FakeRunReadPort(self.platform)
        self.control_plane = DurableAgentControlPlane(
            transaction=self.transaction, tenant_id=TENANT
        )
        self.adapter = ControlPlaneAgentRunExecutionAdapter(
            leases=self.leases,
            control_plane=self.control_plane,  # type: ignore[arg-type]
            runs=self.runs,
            service_actor_id=EMPLOYEE,
        )
